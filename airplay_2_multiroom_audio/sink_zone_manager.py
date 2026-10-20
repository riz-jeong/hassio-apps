#!/usr/bin/env python3
"""
sink_zone_manager.py — one s6 service that owns every PulseAudio-sink
("remap sink") AirPlay 2 zone, the same way cast_bridge_manager.py owns
the Cast-backed ones.

Why this exists
---------------
generate_airplay2_services.sh used to create one s6 service directory
per remap sink. s6-rc compiles its service database once, at container
start (S6_STAGE2_HOOK), so a remap sink that appeared or disappeared
later -- a Bluetooth speaker reconnecting, a USB DAC being replugged, the
Multi-room Audio Controller recreating a sink -- could only be picked up
by restarting the whole addon. This process spawns and supervises the
shairport-sync instances itself, so zones can come and go at runtime.

What it does
------------
  * At startup, starts one shairport-sync per module-remap-sink, using
    exactly the config, port assignment, log file and command line the
    shell generator used to produce.
  * Watches `pactl subscribe` for sink new/remove events (debounced, with
    a periodic poll as a backstop) and reconciles the running zones with
    the sinks that exist:
      - sink disappeared -> stop that zone's shairport-sync (the zone
        leaves the AirPlay picker; its port stays reserved)
      - sink (re)appeared -> start the zone again on the SAME port and
        name, so its AirPlay 2 device ID, and therefore its Music
        Assistant player and settings, stay the same
  * Restarts a shairport-sync that exits unexpectedly, with backoff.

Stable creates zones only for remap sinks. Beta additionally creates a
zone for a connected Bluetooth A2DP sink when no remap sink sits on top
of it. After a PulseAudio restart (`ha audio restart`) the manager
bounces master sinks, reloads remap modules and restarts zones, because
old shairport-sync processes can hold dead connections to the old server.

What stays in generate_airplay2_services.sh: the one-time dbus / avahi /
nqptp / cast-bridge services and, before any of this starts, the master
sink bounce + remap sink recreate + duplicate cleanup. The stage-2 hook
runs before any service starts, so remap sinks are already in their
fresh state when this process first lists them.

Port assignments live in the same port_map.txt cast_bridge_manager.py
uses ("name port" per line). Both processes now start concurrently, so
every read-modify-write of that file happens under one shared flock.
"""

import fcntl
import json
import logging
import os
import re
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from airplay_name import configured_name

log = logging.getLogger("sink_zone_manager")

CONFIG_DIR = Path(os.environ.get("SZM_CONFIG_DIR", "/config/shairport-sync/config"))
LOG_DIR = Path(os.environ.get("SZM_LOG_DIR", "/config/shairport-sync/logs"))
PORT_MAP_FILE = CONFIG_DIR / "port_map.txt"
PORT_MAP_LOCK_FILE = CONFIG_DIR / ".port_map.lock"

AIRPLAY_INTERFACE = os.environ.get("AIRPLAY_INTERFACE", "")

# shairport-sync's own verbosity (-v / -vv / -vvv), from the add-on's
# "log_level" option. Falls back to -vv, which is what it always used.
OPTIONS_FILE = Path(os.environ.get("SZM_OPTIONS_FILE", "/data/options.json"))


def shairport_verbosity() -> str:
    try:
        level = json.loads(OPTIONS_FILE.read_text()).get("log_level", "vv")
    except (OSError, ValueError, AttributeError):
        level = "vv"
    return "-" + level if level in ("v", "vv", "vvv") else "-vv"


def decoded_buffer_seconds() -> int:
    """Return the bounded AirPlay 2 decoded-audio buffer from add-on options."""
    try:
        options = json.loads(OPTIONS_FILE.read_text())
    except FileNotFoundError:
        options = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read add-on options from {OPTIONS_FILE}: {exc}") from exc
    if not isinstance(options, dict):
        raise ValueError(f"add-on options in {OPTIONS_FILE} must be a JSON object")
    seconds = options.get("airplay_decoded_buffer_seconds", 3)
    if type(seconds) is not int or not 1 <= seconds <= 10:
        raise ValueError("airplay_decoded_buffer_seconds must be an integer from 1 to 10")
    return seconds

# Same starting points the shell generator used. Cast zones use 5150+ /
# 6800+, so the two ranges only meet if there are a lot of zones, and
# port_map.txt plus is_port_available() catch that case anyway.
RTSP_PORT_BASE = 5120
UDP_PORT_BASE = 7011
UDP_PORT_STEP = 10  # shairport-sync's default udp_port_range is 10

SUPERVISE_INTERVAL_SECONDS = 5
POLL_INTERVAL_SECONDS = 30
EVENT_DEBOUNCE_SECONDS = 2.0
STOP_GRACE_SECONDS = 4.0
HEALTHY_AFTER_SECONDS = 60

DUPLICATE_SUFFIX_RE = re.compile(r"\.\d+$")


# -- shared port_map.txt ------------------------------------------------
class port_map_lock:
    """Exclusive flock shared with cast_bridge_manager.py."""

    def __enter__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = open(PORT_MAP_LOCK_FILE, "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()


def load_port_map() -> dict:
    m = {}
    if PORT_MAP_FILE.exists():
        for line in PORT_MAP_FILE.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2:
                try:
                    m[parts[0]] = int(parts[1])
                except ValueError:
                    pass
    return m


def save_port_map(m: dict) -> None:
    tmp = PORT_MAP_FILE.with_name(PORT_MAP_FILE.name + ".tmp")
    tmp.write_text("".join(f"{name} {port}\n" for name, port in m.items()))
    os.replace(tmp, PORT_MAP_FILE)


def is_port_available(port: int) -> bool:
    for socktype in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        s = socket.socket(socket.AF_INET, socktype)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return False
        finally:
            s.close()
    return True


def assign_rtsp_port(name: str) -> int:
    """Returns name's persisted RTSP port if it is still free and not
    claimed by a different name in port_map.txt; otherwise picks the next
    free, unclaimed port, persists it, and returns it.

    shairport-sync derives a zone's AirPlay 2 device ID from its name AND
    its RTSP port, so keeping the port stable keeps the device ID stable
    (Music Assistant would otherwise see a brand-new player with default
    settings). The "claimed by a different name" check reads the map
    itself, not just the OS's bind state, because at startup every
    shairport-sync is down and every port looks free.
    """
    with port_map_lock():
        port_map = load_port_map()
        others = {k: v for k, v in port_map.items() if k != name}
        existing = port_map.get(name)
        if existing is not None:
            if existing in others.values():
                log.warning("Persisted port %d for '%s' is also claimed by another "
                            "zone in port_map.txt, reassigning", existing, name)
            elif is_port_available(existing):
                log.info("Reusing persisted port %d for '%s'", existing, name)
                return existing
            else:
                log.warning("Persisted port %d for '%s' is no longer free, reassigning",
                            existing, name)
        port = RTSP_PORT_BASE
        used = set(others.values())
        while port in used or not is_port_available(port):
            port += 1
        port_map[name] = port
        save_port_map(port_map)
        log.info("Assigned new port %d for '%s'", port, name)
        return port


# -- PulseAudio ---------------------------------------------------------
def list_remap_sinks():
    """Names of the module-remap-sink sinks that should have a zone, in
    pactl's order, or None if pactl could not be queried (callers must
    NOT treat None as "no sinks", or a PulseAudio hiccup would tear every
    zone down)."""
    try:
        res = subprocess.run(["pactl", "list", "sinks", "short"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("pactl list sinks failed: %s", e)
        return None
    if res.returncode != 0:
        log.warning("pactl list sinks exited %d: %s", res.returncode, res.stderr.strip())
        return None

    names = []
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and "module-remap-sink" in parts[2]:
            names.append(parts[1])
    return names


def remap_modules():
    """[(module_index, argument_string)] for every module-remap-sink, or None
    if pactl could not be queried."""
    try:
        res = subprocess.run(["pactl", "list", "modules"], capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("pactl list modules failed: %s", e)
        return None
    if res.returncode != 0:
        log.warning("pactl list modules exited %d: %s", res.returncode, res.stderr.strip())
        return None

    mods, idx, is_remap = [], None, False
    for line in res.stdout.splitlines():
        m = re.match(r"Module #(\d+)", line)
        if m:
            idx, is_remap = m.group(1), False
        elif re.match(r"\s*Name:\s*module-remap-sink\s*$", line):
            is_remap = True
        elif is_remap and re.match(r"\s*Argument:", line):
            mods.append((idx, line.split("Argument:", 1)[1].strip()))
            is_remap = False
    return mods


def remap_master_sink(sink_name):
    """Return the physical PulseAudio master for a remap sink, if known."""
    modules = remap_modules()
    if modules is None:
        return None
    for _, arguments in modules:
        remap_name = re.search(r"(?:^|\s)sink_name=(\S+)", arguments)
        master_name = re.search(r"(?:^|\s)master=(\S+)", arguments)
        if remap_name and remap_name.group(1) == sink_name and master_name:
            return master_name.group(1)
    return None


def sink_sample_rate(sink_name):
    """Read the current negotiated rate for a named PulseAudio sink."""
    try:
        result = subprocess.run(["pactl", "list", "sinks"], capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Cannot detect sample rate for sink '%s': %s", sink_name, exc)
        return None
    if result.returncode != 0:
        log.warning("Cannot detect sample rate for sink '%s': pactl exited %d: %s",
                    sink_name, result.returncode, result.stderr.strip())
        return None

    current_name = None
    for line in result.stdout.splitlines():
        if line.startswith("Sink #"):
            current_name = None
        name = re.match(r"\s*Name:\s*(\S+)", line)
        if name:
            current_name = name.group(1)
            continue
        if current_name == sink_name:
            spec = re.match(r"\s*Sample Specification:.*?\b(\d{4,6})Hz\b", line)
            if spec:
                return int(spec.group(1))
    log.warning("PulseAudio did not report a sample rate for sink '%s'", sink_name)
    return None


def bounce_and_recreate_remaps(wanted):
    """After a PulseAudio restart a master sink can look fine (IDLE) while its
    ALSA device never reopens, so nothing is audible. Same fix as the addon's
    boot-time step: suspend/resume each master sink once, then reload each
    remap module with its original arguments. `wanted` is the list of zone
    sink names; other remap sinks are left alone."""
    mods = remap_modules()
    if mods is None:
        return

    def run(*cmd):
        r = subprocess.run(["pactl", *cmd], capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            log.warning("pactl %s failed: %s", " ".join(cmd[:2]), r.stderr.strip())
        return r.returncode == 0

    bounced = set()
    for idx, args in mods:
        sink = re.search(r"sink_name=(\S+)", args)
        master = re.search(r"master=(\S+)", args)
        if not sink or sink.group(1) not in wanted:
            continue
        try:
            if master and master.group(1) not in bounced:
                bounced.add(master.group(1))
                log.info("Bouncing master sink %s (underlies %s)", master.group(1), sink.group(1))
                run("suspend-sink", master.group(1), "1")
                time.sleep(0.5)
                run("suspend-sink", master.group(1), "0")
            log.info("Recreating remap sink %s (module #%s)", sink.group(1), idx)
            run("unload-module", idx)
            time.sleep(0.2)
            run("load-module", "module-remap-sink", args)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("Bounce/recreate of %s failed: %s", sink.group(1), e)


_warned_skips = set()


def zone_sinks(names):
    """Filters remap sink names down to the ones that get a zone."""
    keep = []
    for n in names:
        if n.startswith("sendspin_"):
            continue
        if DUPLICATE_SUFFIX_RE.search(n) and DUPLICATE_SUFFIX_RE.sub("", n) in names:
            if n not in _warned_skips:
                _warned_skips.add(n)
                log.warning("Ignoring '%s': looks like an auto-suffixed duplicate of '%s' "
                            "(restart the addon to have the duplicate cleaned up)",
                            n, DUPLICATE_SUFFIX_RE.sub("", n))
            continue
        keep.append(n)
    return keep


# -- one zone -----------------------------------------------------------
class Zone:
    def __init__(self, sink: str, rtsp_port: int, udp_base: int,
                 pa_sink: str = None, display_name: str = None,
                 output_rate="auto"):
        # `sink` is the zone's key (file names, port_map.txt). It is the
        # PulseAudio sink name for a remap sink; a Bluetooth zone is keyed by
        # its alias instead and names the real sink in `pa_sink`.
        self.sink = sink
        self.pa_sink = pa_sink or sink
        self.output_rate = (output_rate if type(output_rate) is int
                            and 8000 <= output_rate <= 384000 else "auto")
        self.display_name = configured_name(display_name or sink.replace("_", " "))
        self.rtsp_port = rtsp_port
        self.udp_base = udp_base
        self.config_file = CONFIG_DIR / f"{sink}-ap2.conf"
        self.custom_file = CONFIG_DIR / f"{sink}-ap2.custom"
        self.log_file = LOG_DIR / f"{sink}-ap2.log"
        self.proc = None
        self.started_at = 0.0
        self.failures = 0
        self.dead_noted = False
        self.next_restart_at = 0.0

    def _ensure_custom_file(self):
        # Per-zone user overrides, spliced into the general block via
        # libconfig's @include. Never overwritten once it exists. It must
        # always exist: a missing @include target stops shairport-sync
        # from parsing its config.
        if not self.custom_file.exists():
            self.custom_file.write_text(
                "// Per-zone shairport-sync overrides, included inside general = { ... }\n"
                "// Example:\n"
                "// audio_backend_latency_offset_in_seconds = -0.15;\n")
            log.info("Created %s", self.custom_file)

    def _write_config(self):
        custom_config = self.custom_file.read_text()
        buffer_seconds = decoded_buffer_seconds()
        custom_buffer = re.search(
            r"(?m)^\s*audio_decoded_buffer_desired_length_in_seconds\s*=", custom_config)
        buffer_setting = "" if custom_buffer else (
            "  audio_decoded_buffer_desired_length_in_seconds = "
            f"{buffer_seconds:.1f};")
        custom_backend_buffer = re.search(
            r"(?m)^\s*audio_backend_buffer_desired_length_in_seconds\s*=", custom_config)
        backend_buffer_setting = "" if custom_backend_buffer else (
            "  audio_backend_buffer_desired_length_in_seconds = 0.35;")
        output_rate_setting = (str(self.output_rate) if type(self.output_rate) is int
                               else json.dumps(self.output_rate))
        if re.search(r"(?m)^\s*interface\s*=", custom_config):
            raise ValueError(
                f"{self.custom_file} sets interface, which conflicts with the selected "
                "AirPlay interface; remove the interface setting"
            )
        if re.search(r"(?m)^\s*name\s*=", custom_config):
            raise ValueError(
                f"{self.custom_file} sets name, which conflicts with airplay_name; "
                "remove the name setting"
            )
        self.config_file.write_text(f"""\
general :
{{
  name = {json.dumps(self.display_name, ensure_ascii=False)};
  port = {self.rtsp_port};
  interface = "{AIRPLAY_INTERFACE}";
  output_backend = "pulseaudio";
  udp_port_base = {self.udp_base};
  mdns_backend = "avahi";
{buffer_setting}
{backend_buffer_setting}
  @include "{self.custom_file}"
}};
sessioncontrol :
{{
  allow_session_interruption = "yes";
}};
diagnostics :
{{
  log_show_time_since_startup = "yes";
  log_show_time_since_last_message = "no";
}};
pulseaudio :
{{
  sink = "{self.pa_sink}";
  output_rate = {output_rate_setting};
  application_name = "Shairport Sync";
}};
""")
        selected_buffer = "custom" if custom_buffer else f"{buffer_seconds}s"
        log.info("AirPlay zone '%s' uses decoded audio buffer: %s",
                 self.display_name, selected_buffer)
        rate_label = (f"{self.output_rate} Hz" if type(self.output_rate) is int
                      else "automatic rate selection")
        log.info("AirPlay zone '%s' uses PulseAudio sink '%s' at %s",
                 self.display_name, self.pa_sink, rate_label)

    def start(self, fresh: bool = True):
        """fresh=True truncates the zone log (a new session of the zone);
        fresh=False appends, so the output of a crash is not wiped by the
        restart that follows it."""
        self._ensure_custom_file()
        self._write_config()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if fresh:
            open(self.log_file, "wb").close()
        with open(self.log_file, "ab") as fh:
            fh.write(f"{time.strftime('%a %b %d %H:%M:%S %Z %Y')} - Starting AirPlay 2 receiver: "
                     f"{self.display_name} for {self.pa_sink} on port {self.rtsp_port}\n".encode())
            fh.flush()
            self.proc = subprocess.Popen(
                ["shairport-sync", "-a", self.display_name, "-p", str(self.rtsp_port),
                 "-c", str(self.config_file), shairport_verbosity()],
                stdout=fh, stderr=subprocess.STDOUT)
        self.started_at = time.time()
        self.dead_noted = False
        log.info("Started zone '%s' (sink %s, port %d, udp %d, pid %d)",
                 self.display_name, self.pa_sink, self.rtsp_port, self.udp_base, self.proc.pid)

    def terminate(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

    def wait_or_kill(self, deadline: float):
        p = self.proc
        if p is None:
            return
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            log.warning("shairport-sync for '%s' did not exit, killing", self.display_name)
            p.kill()
            p.wait()
        self.proc = None

    def stop(self):
        self.terminate()
        self.wait_or_kill(time.time() + STOP_GRACE_SECONDS)

    def supervise(self, now: float):
        p = self.proc
        if p is None:
            return
        if p.poll() is None:
            if now - self.started_at > HEALTHY_AFTER_SECONDS:
                self.failures = 0
            return
        if not self.dead_noted:
            self.dead_noted = True
            self.failures += 1
            delay = 0 if self.failures == 1 else min(60, 5 * 2 ** (self.failures - 2))
            self.next_restart_at = now + delay
            log.warning("shairport-sync for '%s' exited with code %s, restarting in %ds "
                        "(failure %d)", self.display_name, p.returncode, delay, self.failures)
        if now >= self.next_restart_at:
            try:
                self.start(fresh=False)
            except ValueError as exc:
                log.error("Cannot restart AirPlay zone '%s': %s", self.display_name, exc)
                self.next_restart_at = now + 60


# -- the manager --------------------------------------------------------
class Manager:
    def __init__(self):
        self.zones = {}        # sink name -> Zone (running)
        self.udp_bases = {}    # sink name -> udp base, kept across stop/start
        self.booted = False
        self.stop_event = threading.Event()
        self.change_event = threading.Event()
        self.last_sink_event = 0.0
        self.pactl_subscribe = None
        self.pulse_restarted = False   # set when the PulseAudio connection dropped

    def _udp_base_for(self, sink: str) -> int:
        if sink not in self.udp_bases:
            used = set(self.udp_bases.values())
            base = UDP_PORT_BASE
            while base in used:
                base += UDP_PORT_STEP
            self.udp_bases[sink] = base
        return self.udp_bases[sink]

    def _start_zone(self, sink: str):
        if re.search(r"\s", sink):
            log.warning("Skipping sink '%s': whitespace in the name breaks port_map.txt", sink)
            return
        master_sink = remap_master_sink(sink)
        sample_rate = sink_sample_rate(master_sink) if master_sink else None
        if sample_rate is None:
            log.warning("Using Shairport Sync automatic sample-rate selection for '%s' "
                        "because its PulseAudio output sink rate could not be detected", sink)
            sample_rate = "auto"
        zone = Zone(sink, assign_rtsp_port(sink), self._udp_base_for(sink),
                    output_rate=sample_rate)
        try:
            zone.start(fresh=True)
        except ValueError as exc:
            log.error("Skipping AirPlay zone '%s': %s", sink, exc)
            return
        self.zones[sink] = zone

    def _desired(self, names):
        """Zone keys that should be running: remap sinks only in Stable."""
        return zone_sinks(names)

    def reconcile(self):
        names = list_remap_sinks()
        if names is None:
            return
        desired = self._desired(names)
        add = [s for s in desired if s not in self.zones]
        remove = [s for s in self.zones if s not in desired]

        if self.pulse_restarted:
            self.pulse_restarted = False
            if self.booted:
                # Running shairport-sync processes still hold connections to the
                # old PulseAudio server and make no sound. Restart them all.
                log.warning("PulseAudio was restarted: restarting %d zone(s)", len(self.zones))
                for s in list(self.zones):
                    self.zones.pop(s).stop()
                self.stop_event.wait(2)   # let PulseAudio finish loading its cards
                bounce_and_recreate_remaps(desired)
                recovered_names = list_remap_sinks()
                if recovered_names is None:
                    log.warning("PulseAudio is back but its sink list is unavailable; "
                                "zones will start after a later successful sink query")
                    recovered_names = []
                names = recovered_names
                desired = self._desired(names)
                add = [s for s in desired if s not in self.zones]
                remove = []

        if not self.booted:
            self.booted = True
            if not desired:
                log.warning("No remap or Bluetooth sinks found at startup. Watching for new ones.")
            for s in add:
                self._start_zone(s)
            log.info("Started %d zone(s). Supervising.", len(self.zones))
            return

        if not add and not remove:
            return

        log.info("Sink set changed: starting %s, stopping %s", add or "nothing", remove or "nothing")
        for s in remove:
            self.zones.pop(s).stop()
            log.info("Stopped zone for '%s' (sink is gone; its port stays reserved)", s)
        for s in add:
            self._start_zone(s)

    def supervise(self):
        now = time.time()
        for zone in self.zones.values():
            zone.supervise(now)

    # -- pactl subscribe watcher ----------------------------------------
    def _watch_events(self):
        while not self.stop_event.is_set():
            try:
                self.pactl_subscribe = subprocess.Popen(
                    ["pactl", "subscribe"], stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True)
            except OSError as e:
                log.warning("Could not start 'pactl subscribe': %s", e)
                self.stop_event.wait(5)
                continue
            for line in self.pactl_subscribe.stdout:
                if "on sink #" in line and ("'new'" in line or "'remove'" in line):
                    self.last_sink_event = time.time()
                    self.change_event.set()
            self.pactl_subscribe.wait()
            if not self.stop_event.is_set():
                log.warning("'pactl subscribe' exited, restarting it in 2s")
                self.pulse_restarted = True
                self.stop_event.wait(2)
                # Events may have been missed meanwhile.
                self.last_sink_event = time.time()
                self.change_event.set()

    def shutdown(self):
        log.info("Shutting down, stopping %d zone(s)", len(self.zones))
        if self.pactl_subscribe is not None and self.pactl_subscribe.poll() is None:
            self.pactl_subscribe.terminate()
        for z in self.zones.values():
            z.terminate()
        deadline = time.time() + STOP_GRACE_SECONDS
        for z in self.zones.values():
            z.wait_or_kill(deadline)

    def run(self):
        threading.Thread(target=self._watch_events, daemon=True, name="pactl-subscribe").start()
        last_reconcile = last_supervise = 0.0
        self.reconcile()
        last_reconcile = time.time()
        try:
            while not self.stop_event.is_set():
                now = time.time()
                if self.change_event.is_set() and now - self.last_sink_event >= EVENT_DEBOUNCE_SECONDS:
                    self.change_event.clear()
                    self.reconcile()
                    last_reconcile = time.time()
                elif now - last_reconcile >= (POLL_INTERVAL_SECONDS if self.booted
                                              else SUPERVISE_INTERVAL_SECONDS):
                    # Backstop poll; before the first successful pactl
                    # query (PulseAudio not up yet) it retries faster.
                    self.reconcile()
                    last_reconcile = time.time()
                if now - last_supervise >= SUPERVISE_INTERVAL_SECONDS:
                    self.supervise()
                    last_supervise = time.time()
                self.stop_event.wait(0.5)
        finally:
            self.shutdown()


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [sink-zones] %(levelname)s %(message)s")
    if not AIRPLAY_INTERFACE:
        raise SystemExit("AIRPLAY_INTERFACE was not resolved by add-on startup; restart the add-on")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    mgr = Manager()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: mgr.stop_event.set())

    log.info("sink_zone_manager starting (interface %s, shairport-sync %s)",
             AIRPLAY_INTERFACE, shairport_verbosity())
    mgr.run()


if __name__ == "__main__":
    main()
