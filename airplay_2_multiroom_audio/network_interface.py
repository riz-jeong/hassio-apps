#!/usr/bin/env python3
"""Select and validate the network interface used by AirPlay and Avahi."""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("airplay-interface")
_IFNAME = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_UNSUITABLE_KINDS = {
    "dummy", "erspan", "gre", "gretap", "ip6gre", "ip6tnl", "ipip",
    "sit", "tap", "tun", "veth", "vti", "vti6", "wireguard", "vxlan",
}
_UNSUITABLE_NAMES = re.compile(
    r"^(?:lo|docker\d*|br-[0-9a-f]{6,}|veth.*|cni.*|podman.*|virbr.*|"
    r"tailscale.*|wg\d*|tun\d*|tap\d*|zt[a-z0-9]*|vpn.*|ppp\d*)$",
    re.IGNORECASE,
)


class InterfaceResolutionError(ValueError):
    """Raised when an appropriate AirPlay interface cannot be selected."""


def _interfaces(links):
    return {entry.get("ifname"): entry for entry in links if entry.get("ifname")}


def _is_suitable(name, links, addresses):
    entry = _interfaces(links).get(name)
    if not entry:
        return False, f"network interface '{name}' does not exist in this add-on container"
    flags = entry.get("flags", [])
    if "UP" not in flags or entry.get("operstate", "UP") == "DOWN":
        return False, f"network interface '{name}' is down; bring it up or choose another interface"
    if "MULTICAST" not in flags:
        return False, f"network interface '{name}' does not support multicast required for mDNS discovery"
    kind = (entry.get("linkinfo") or {}).get("info_kind", "").lower()
    if name == "lo" or _UNSUITABLE_NAMES.match(name) or kind in _UNSUITABLE_KINDS:
        return False, f"network interface '{name}' is a loopback, container, tunnel, or virtual interface"
    for address_entry in addresses:
        if address_entry.get("ifname") != name:
            continue
        if any(info.get("scope") != "host" for info in address_entry.get("addr_info", [])):
            return True, ""
    return False, f"network interface '{name}' has no LAN address and is not suitable for mDNS discovery"


def select_interface(configured, links, addresses, ipv4_routes, ipv6_routes):
    """Return a validated manual interface or the best eligible default route."""
    configured = str(configured or "auto").strip()
    if configured.lower() != "auto":
        if configured in (".", "..") or not _IFNAME.fullmatch(configured):
            raise InterfaceResolutionError(
                "airplay_interface must be 'auto' or a valid Linux interface name "
                "(1-15 letters, numbers, '.', '_', or '-')"
            )
        suitable, reason = _is_suitable(configured, links, addresses)
        if not suitable:
            raise InterfaceResolutionError(reason)
        return configured

    route_families = (("IPv4", ipv4_routes), ("IPv6", ipv6_routes))
    for family, routes in route_families:
        candidates = []
        for route in routes:
            name = route.get("dev")
            if not name:
                continue
            suitable, _ = _is_suitable(name, links, addresses)
            if suitable:
                candidates.append((int(route.get("metric", 0)), name))
        if not candidates:
            continue
        best_metric = min(metric for metric, _ in candidates)
        best = sorted({name for metric, name in candidates if metric == best_metric})
        if len(best) > 1:
            raise InterfaceResolutionError(
                "automatic interface detection is ambiguous: equally preferred default routes "
                f"use {', '.join(best)}; set airplay_interface manually"
            )
        log.info("Selected AirPlay interface '%s' automatically from the %s default route", best[0], family)
        return best[0]
    raise InterfaceResolutionError(
        "no suitable LAN interface is associated with an IPv4 or IPv6 default route; "
        "set airplay_interface to an up interface with a LAN address"
    )


def _run_ip(run, arguments):
    try:
        result = run(["ip", *arguments], check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise InterfaceResolutionError(
            "the 'ip' utility is unavailable; install iproute2 in the add-on image"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise InterfaceResolutionError(
            f"could not inspect network interfaces with 'ip {' '.join(arguments)}'"
            + (f": {detail}" if detail else "")
        ) from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InterfaceResolutionError("the 'ip' utility returned invalid JSON network data") from exc


def load_runtime_state(run=subprocess.run):
    """Read interface state and routes from the add-on's current network namespace."""
    links = _run_ip(run, ["-j", "-d", "link", "show"])
    addresses = _run_ip(run, ["-j", "address", "show"])
    ipv4_routes = _run_ip(run, ["-j", "route", "show", "default"])
    ipv6_routes = _run_ip(run, ["-j", "-6", "route", "show", "default"])
    return links, addresses, ipv4_routes, ipv6_routes


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)
    options_file = Path(os.environ.get("AIRPLAY_OPTIONS_FILE", "/data/options.json"))
    try:
        options = json.loads(options_file.read_text())
    except FileNotFoundError:
        options = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise InterfaceResolutionError(f"could not read add-on options from {options_file}: {exc}") from exc
    configured = options.get("airplay_interface", "auto")
    links, addresses, ipv4_routes, ipv6_routes = load_runtime_state()
    selected = select_interface(configured, links, addresses, ipv4_routes, ipv6_routes)
    mode = "manually configured" if str(configured).strip().lower() != "auto" else "detected automatically"
    log.info("Using AirPlay interface '%s' (%s)", selected, mode)
    print(selected)


if __name__ == "__main__":
    try:
        main()
    except InterfaceResolutionError as exc:
        log.error("AirPlay interface selection failed: %s", exc)
        sys.exit(1)
