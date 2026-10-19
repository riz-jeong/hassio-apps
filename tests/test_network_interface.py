import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "airplay_2_multiroom_audio" / "network_interface.py"
SPEC = importlib.util.spec_from_file_location("network_interface", RESOLVER)
network_interface = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(network_interface)


def link(name, *, kind=None, up=True):
    return {
        "ifname": name,
        "flags": ["UP", "MULTICAST"] if up else [],
        "operstate": "UP" if up else "DOWN",
        "linkinfo": {"info_kind": kind} if kind else {},
    }


def address(name, local, scope="global"):
    return {"ifname": name, "addr_info": [{"local": local, "scope": scope}]}


class NetworkInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.links = [link("eth0"), link("eth1"), link("lo", kind="loopback")]
        self.addresses = [address("eth0", "192.168.1.2"), address("eth1", "10.0.0.2")]

    def test_auto_uses_lowest_metric_default_route_with_multiple_interfaces(self):
        routes = [{"dev": "eth0", "metric": 200}, {"dev": "eth1", "metric": 100}]
        self.assertEqual(
            "eth1",
            network_interface.select_interface("auto", self.links, self.addresses, routes, []),
        )

    def test_auto_rejects_equal_priority_defaults_on_different_interfaces(self):
        routes = [{"dev": "eth0", "metric": 100}, {"dev": "eth1", "metric": 100}]
        with self.assertRaisesRegex(network_interface.InterfaceResolutionError, "ambiguous"):
            network_interface.select_interface("auto", self.links, self.addresses, routes, [])

    def test_auto_skips_virtual_and_tunnel_default_routes(self):
        links = self.links + [link("docker0", kind="bridge"), link("wg0", kind="wireguard")]
        addresses = self.addresses + [address("docker0", "172.17.0.1"), address("wg0", "10.8.0.1")]
        routes = [
            {"dev": "wg0", "metric": 10},
            {"dev": "docker0", "metric": 20},
            {"dev": "eth0", "metric": 100},
        ]
        self.assertEqual("eth0", network_interface.select_interface("auto", links, addresses, routes, []))

    def test_auto_uses_ipv6_default_when_no_eligible_ipv4_default_exists(self):
        self.assertEqual(
            "eth1",
            network_interface.select_interface(
                "auto", self.links, self.addresses, [], [{"dev": "eth1", "metric": 100}]
            ),
        )

    def test_auto_prefers_ipv4_when_both_families_have_default_routes(self):
        self.assertEqual(
            "eth0",
            network_interface.select_interface(
                "auto", self.links, self.addresses,
                [{"dev": "eth0", "metric": 200}], [{"dev": "eth1", "metric": 1}],
            ),
        )

    def test_manual_interface_can_select_an_up_lan_interface_without_default_route(self):
        self.assertEqual(
            "eth0", network_interface.select_interface("eth0", self.links, self.addresses, [], [])
        )

    def test_manual_interface_rejects_invalid_names_and_unknown_interfaces(self):
        for name in ("bad/name", "../eth0", "this-interface-name-is-too-long", "eth:0", ".", ".."):
            with self.subTest(name=name), self.assertRaises(network_interface.InterfaceResolutionError):
                network_interface.select_interface(name, self.links, self.addresses, [], [])
        with self.assertRaisesRegex(network_interface.InterfaceResolutionError, "does not exist"):
            network_interface.select_interface("missing0", self.links, self.addresses, [], [])

    def test_manual_interface_rejects_down_or_unsuitable_interfaces(self):
        links = self.links + [link("wg0", kind="wireguard"), link("eth2", up=False), link("eth3")]
        addresses = self.addresses + [address("wg0", "10.8.0.1"), address("eth2", "192.168.1.3")]
        for name in ("wg0", "eth2", "eth3", "lo"):
            with self.subTest(name=name), self.assertRaises(network_interface.InterfaceResolutionError):
                network_interface.select_interface(name, links, addresses, [], [])

    def test_manual_interface_rejects_interfaces_without_multicast(self):
        links = [link("eth0", up=False), {**link("eth4"), "flags": ["UP"]}]
        with self.assertRaisesRegex(network_interface.InterfaceResolutionError, "multicast"):
            network_interface.select_interface("eth4", links, [address("eth4", "192.168.1.4")], [], [])

    def test_auto_reports_missing_default_routes(self):
        with self.assertRaisesRegex(network_interface.InterfaceResolutionError, "default route"):
            network_interface.select_interface("auto", self.links, self.addresses, [], [])

    def test_runtime_reports_unavailable_ip_command(self):
        def unavailable(*_args, **_kwargs):
            raise FileNotFoundError("ip")

        with self.assertRaisesRegex(network_interface.InterfaceResolutionError, "iproute2"):
            network_interface.load_runtime_state(run=unavailable)

    def test_startup_cli_resolves_manual_and_auto_from_runtime_ip_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            options_file = temp / "options.json"
            options_file.write_text('{"airplay_interface": "eth0"}')
            fake_ip = temp / "ip"
            fixture = {
                "links": [link("eth0"), link("eth1")],
                "addresses": [address("eth0", "192.168.1.2"), address("eth1", "10.0.0.2")],
                "ipv4": [{"dev": "eth1", "metric": 25}],
                "ipv6": [],
            }
            fake_ip.write_text(
                f"#!{sys.executable}\nimport json, sys\n"
                f"fixture = json.loads({json.dumps(fixture)!r})\n"
                "args = sys.argv[1:]\n"
                "key = ('links' if 'link' in args else 'addresses' if 'address' in args "
                "else 'ipv6' if '-6' in args else 'ipv4')\n"
                "print(json.dumps(fixture[key]))\n"
            )
            fake_ip.chmod(0o755)
            env = {**os.environ, "AIRPLAY_OPTIONS_FILE": str(options_file), "PATH": temp_dir}
            result = subprocess.run([sys.executable, str(RESOLVER)], env=env,
                                    capture_output=True, text=True)
            options_file.write_text('{"airplay_interface": "auto"}')
            automatic = subprocess.run([sys.executable, str(RESOLVER)], env=env,
                                       capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("eth0", result.stdout.strip())
        self.assertIn("manually configured", result.stderr)
        self.assertEqual(0, automatic.returncode, automatic.stderr)
        self.assertEqual("eth1", automatic.stdout.strip())
        self.assertIn("detected automatically", automatic.stderr)

    def test_startup_cli_rejects_invalid_manual_interface(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            options_file = temp / "options.json"
            options_file.write_text('{"airplay_interface": "bad/name"}')
            fake_ip = temp / "ip"
            fake_ip.write_text(f"#!{sys.executable}\nprint('[]')\n")
            fake_ip.chmod(0o755)
            env = {**os.environ, "AIRPLAY_OPTIONS_FILE": str(options_file), "PATH": temp_dir}
            result = subprocess.run([sys.executable, str(RESOLVER)], env=env,
                                    capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("valid Linux interface name", result.stderr)

    def test_startup_cli_reports_missing_default_route(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            options_file = temp / "options.json"
            options_file.write_text('{"airplay_interface": "auto"}')
            fake_ip = temp / "ip"
            fake_ip.write_text(f"#!{sys.executable}\nprint('[]')\n")
            fake_ip.chmod(0o755)
            env = {**os.environ, "AIRPLAY_OPTIONS_FILE": str(options_file), "PATH": temp_dir}
            result = subprocess.run([sys.executable, str(RESOLVER)], env=env,
                                    capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("default route", result.stderr)

    def test_addon_resolvers_remain_identical(self):
        stable = (ROOT / "airplay_2_multiroom_audio" / "network_interface.py").read_bytes()
        beta = (ROOT / "airplay_2_multiroom_audio_beta" / "network_interface.py").read_bytes()
        self.assertEqual(stable, beta)

    def test_beta_sink_manager_generates_config_with_selected_interface(self):
        manager_path = ROOT / "airplay_2_multiroom_audio_beta" / "sink_zone_manager.py"
        manager_dir = str(manager_path.parent)
        old_value = os.environ.get("AIRPLAY_INTERFACE")
        os.environ["AIRPLAY_INTERFACE"] = "eth1"
        sys.path.insert(0, manager_dir)
        try:
            spec = importlib.util.spec_from_file_location("sink_zone_manager_test", manager_path)
            manager = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(manager)
        finally:
            sys.path.remove(manager_dir)
            if old_value is None:
                os.environ.pop("AIRPLAY_INTERFACE", None)
            else:
                os.environ["AIRPLAY_INTERFACE"] = old_value

        with tempfile.TemporaryDirectory() as temp_dir:
            manager.CONFIG_DIR = Path(temp_dir)
            manager.LOG_DIR = Path(temp_dir)
            zone = manager.Zone("room", 5120, 7011, pa_sink="sink_room")
            zone._ensure_custom_file()
            zone._write_config()
            generated = zone.config_file.read_text()
        self.assertIn('interface = "eth1";', generated)
        self.assertIn('mdns_backend = "avahi";', generated)
        self.assertIn("@include", generated)
        self.assertEqual(1, generated.count("interface ="))

    def test_beta_sink_manager_uses_configured_airplay_name(self):
        addon = ROOT / "airplay_2_multiroom_audio_beta"
        sys.path.insert(0, str(addon))
        try:
            spec = importlib.util.spec_from_file_location("sink_zone_manager_name_test", addon / "sink_zone_manager.py")
            manager = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(manager)
        finally:
            sys.path.remove(str(addon))
        with tempfile.TemporaryDirectory() as temp_dir:
            options_file = Path(temp_dir) / "options.json"
            options_file.write_text('{"airplay_name":"JCH Hi-Fi"}')
            old_options = os.environ.get("AIRPLAY_OPTIONS_FILE")
            os.environ["AIRPLAY_OPTIONS_FILE"] = str(options_file)
            try:
                manager.CONFIG_DIR = Path(temp_dir)
                manager.LOG_DIR = Path(temp_dir)
                zone = manager.Zone("internal_sink_id", 5120, 7011, pa_sink="sink_usb")
                zone._ensure_custom_file()
                zone._write_config()
                generated = zone.config_file.read_text()
                started = []
                from types import SimpleNamespace
                def fake_start(command, **kwargs):
                    kwargs["stdout"].close()
                    started.append(command)
                    return SimpleNamespace(pid=123)

                with patch.object(manager.subprocess, "Popen",
                                  side_effect=fake_start):
                    zone.start()
                self.assertEqual("JCH Hi-Fi", started[0][started[0].index("-a") + 1])
                self.assertTrue((Path(temp_dir) / "internal_sink_id-ap2.conf").exists())
            finally:
                if old_options is None:
                    os.environ.pop("AIRPLAY_OPTIONS_FILE", None)
                else:
                    os.environ["AIRPLAY_OPTIONS_FILE"] = old_options
        self.assertIn('name = "JCH Hi-Fi";', generated)
        self.assertIn('sink = "sink_usb";', generated)

    def test_beta_sink_manager_rejects_interface_override_in_custom_config(self):
        manager_path = ROOT / "airplay_2_multiroom_audio_beta" / "sink_zone_manager.py"
        manager_dir = str(manager_path.parent)
        old_value = os.environ.get("AIRPLAY_INTERFACE")
        os.environ["AIRPLAY_INTERFACE"] = "eth1"
        sys.path.insert(0, manager_dir)
        try:
            spec = importlib.util.spec_from_file_location("sink_zone_manager_custom_test", manager_path)
            manager = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(manager)
        finally:
            sys.path.remove(manager_dir)
            if old_value is None:
                os.environ.pop("AIRPLAY_INTERFACE", None)
            else:
                os.environ["AIRPLAY_INTERFACE"] = old_value
        with tempfile.TemporaryDirectory() as temp_dir:
            manager.CONFIG_DIR = Path(temp_dir)
            manager.LOG_DIR = Path(temp_dir)
            zone = manager.Zone("room", 5120, 7011, pa_sink="sink_room")
            zone.custom_file.write_text('interface = "eth0";\n')
            with self.assertRaisesRegex(ValueError, "remove the interface setting"):
                zone._write_config()
            zone.custom_file.write_text('name = "another name";\n')
            with self.assertRaisesRegex(ValueError, "conflicts with airplay_name"):
                zone._write_config()

    def test_stable_sink_config_template_generates_one_selected_interface(self):
        generator = (ROOT / "airplay_2_multiroom_audio" / "generate_airplay2_services.sh").read_text()
        marker = 'cat > "${config_file}" <<EOF\n'
        template = generator.split(marker, 1)[1].split("\nEOF", 1)[0]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "sink.conf"
            env = {
                **os.environ,
                "AIRPLAY_INTERFACE": "eth1",
                "current_name": "USB DAC",
                "current_port": "5120",
                "current_udp_base": "7011",
                "current_sink": "sink_usb",
                "custom_file": str(Path(temp_dir) / "zone.custom"),
                "quoted_receiver_name": '"JCH Hi-Fi"',
                "receiver_name": "JCH Hi-Fi",
                "shell_receiver_name": "'JCH Hi-Fi'",
            }
            command = f'cat > "{output}" <<EOF\n{template}\nEOF'
            result = subprocess.run(["bash", "-c", command], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            generated = output.read_text()
        self.assertIn('interface = "eth1";', generated)
        self.assertIn('mdns_backend = "avahi";', generated)
        self.assertIn('sink = "sink_usb";', generated)
        self.assertIn('name = "JCH Hi-Fi";', generated)
        self.assertIn("@include", generated)
        self.assertEqual(1, generated.count("interface ="))

    def test_stable_sink_run_script_passes_the_configured_name_to_shairport(self):
        generator = (ROOT / "airplay_2_multiroom_audio" / "generate_airplay2_services.sh").read_text()
        marker = 'cat > "${service_dir}/run" <<EOF\n'
        template = generator.split(marker, 1)[1].split("\nEOF", 1)[0]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "run"
            env = {**os.environ, "shell_receiver_name": "'JCH Hi-Fi'", "current_log": "/tmp/zone.log",
                   "receiver_name": "JCH Hi-Fi", "current_sink": "sink_usb", "current_port": "5120",
                   "config_file": "/tmp/zone.conf"}
            command = f'cat > "{output}" <<EOF\n{template}\nEOF'
            result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            generated = output.read_text()
        self.assertIn("-a 'JCH Hi-Fi'", generated)

    def test_avahi_template_writes_one_selected_interface(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon):
                generator = (ROOT / addon / "generate_airplay2_services.sh").read_text()
                marker = 'awk -v iface="${AIRPLAY_INTERFACE}" '
                awk_program = generator.split(marker, 1)[1].split("' /etc/avahi/avahi-daemon.conf", 1)[0]
                awk_program = awk_program.lstrip("'").strip()
                with tempfile.TemporaryDirectory() as temp_dir:
                    source = Path(temp_dir) / "avahi.conf"
                    destination = Path(temp_dir) / "generated.conf"
                    source.write_text("[server]\nallow-interfaces=old0\nhost-name=old-name\n[wide-area]\n")
                    command = f"awk -v iface=eth1 '{awk_program}' '{source}' > '{destination}'"
                    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
                    self.assertEqual(0, result.returncode, result.stderr)
                    generated = destination.read_text()
                self.assertEqual(1, generated.count("allow-interfaces=eth1"))
                self.assertEqual(1, generated.count("host-name=airplay2-multiroom"))
                self.assertNotIn("old0", generated)

    def test_cast_managers_generate_config_with_the_resolved_interface(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon), tempfile.TemporaryDirectory() as temp_dir:
                fake_pychromecast = types.ModuleType("pychromecast")
                fake_zeroconf = types.ModuleType("zeroconf")
                fake_zeroconf.ServiceListener = object
                old_pychromecast = sys.modules.get("pychromecast")
                old_zeroconf = sys.modules.get("zeroconf")
                sys.modules["pychromecast"] = fake_pychromecast
                sys.modules["zeroconf"] = fake_zeroconf
                sys.path.insert(0, str(ROOT / addon))
                try:
                    manager_path = ROOT / addon / "cast_bridge_manager.py"
                    spec = importlib.util.spec_from_file_location(f"{addon}_cast_test", manager_path)
                    manager = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(manager)
                finally:
                    sys.path.remove(str(ROOT / addon))
                    if old_pychromecast is None:
                        sys.modules.pop("pychromecast", None)
                    else:
                        sys.modules["pychromecast"] = old_pychromecast
                    if old_zeroconf is None:
                        sys.modules.pop("zeroconf", None)
                    else:
                        sys.modules["zeroconf"] = old_zeroconf
                manager.AIRPLAY_INTERFACE = "eth1"
                temp = Path(temp_dir)
                manager.CONFIG_DIR = temp / "config"
                manager.LOG_DIR = temp / "logs"
                manager.PIPE_DIR = temp / "pipes"
                manager.CONFIG_DIR.mkdir()
                fifo = str(manager.PIPE_DIR / "cast.pipe")
                options_file = temp / "options.json"
                options_file.write_text('{"airplay_name":"JCH Hi-Fi"}')
                old_options_file = os.environ.get("AIRPLAY_OPTIONS_FILE")
                os.environ["AIRPLAY_OPTIONS_FILE"] = str(options_file)
                started = []
                def fake_popen(*_args, **kwargs):
                    kwargs["stdout"].close()
                    started.append(_args[0])
                    return object()

                try:
                    with patch.object(manager.subprocess, "Popen", side_effect=fake_popen):
                        manager.start_shairport("Cast Room", fifo, 5150, 6811)
                finally:
                    if old_options_file is None:
                        os.environ.pop("AIRPLAY_OPTIONS_FILE", None)
                    else:
                        os.environ["AIRPLAY_OPTIONS_FILE"] = old_options_file
                generated = (manager.CONFIG_DIR / "Cast Room-ap2.conf").read_text()
                self.assertIn('interface = "eth1";', generated)
                self.assertIn('mdns_backend = "avahi";', generated)
                self.assertIn('name = "JCH Hi-Fi";', generated)
                self.assertIn("-a", started[0])
                self.assertEqual("JCH Hi-Fi", started[0][started[0].index("-a") + 1])
                self.assertEqual(1, generated.count("interface ="))

    def test_every_runtime_path_uses_the_resolved_value(self):
        stable_dir = ROOT / "airplay_2_multiroom_audio"
        beta_dir = ROOT / "airplay_2_multiroom_audio_beta"
        stable_generator = (stable_dir / "generate_airplay2_services.sh").read_text()
        beta_generator = (beta_dir / "generate_airplay2_services.sh").read_text()
        self.assertIn('interface = "${AIRPLAY_INTERFACE}";', stable_generator)
        self.assertIn('interface = "{AIRPLAY_INTERFACE}";',
                      (stable_dir / "cast_bridge_manager.py").read_text())
        self.assertIn('interface = "{AIRPLAY_INTERFACE}";',
                      (beta_dir / "cast_bridge_manager.py").read_text())
        self.assertIn('interface = "{AIRPLAY_INTERFACE}";',
                      (beta_dir / "sink_zone_manager.py").read_text())
        for generator in (stable_generator, beta_generator):
            self.assertIn('awk -v iface="${AIRPLAY_INTERFACE}"', generator)
            self.assertIn('network_interface.py', generator)
            self.assertNotIn("AIRPLAY_INTERFACE:-", generator)
        self.assertIn("grep -Eq '^[[:space:]]*interface[[:space:]]*='", stable_generator)
        self.assertIn("export AIRPLAY_INTERFACE=", stable_generator)
        self.assertIn("export AIRPLAY_INTERFACE=", beta_generator)
        for addon in (stable_dir, beta_dir):
            config = (addon / "config.yaml").read_text()
            dockerfile = (addon / "Dockerfile").read_text()
            self.assertIn("airplay_interface: auto", config)
            self.assertIn("airplay_interface: str", config)
            self.assertIn("iproute2", dockerfile)
            self.assertIn("COPY network_interface.py", dockerfile)
            self.assertIn("COPY airplay_name.py", dockerfile)
            self.assertIn("airplay_name: auto", config)
            self.assertIn("airplay_name: str", config)
        self.assertIn("COPY airplay_name.py", (beta_dir / "Dockerfile.txt").read_text())
        for manager in (stable_dir / "cast_bridge_manager.py", beta_dir / "cast_bridge_manager.py",
                        beta_dir / "sink_zone_manager.py"):
            self.assertIn('os.environ.get("AIRPLAY_INTERFACE", "")', manager.read_text())

    def test_both_addons_expose_an_automatic_airplay_name_option(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon):
                config = (ROOT / addon / "config.yaml").read_text()
                translations = (ROOT / addon / "en.yaml").read_text()
                self.assertIn("airplay_name: auto", config)
                self.assertIn("  airplay_name:", translations)

    def test_interface_dependent_services_are_refreshed_on_restart(self):
        stable = (ROOT / "airplay_2_multiroom_audio/generate_airplay2_services.sh").read_text()
        beta = (ROOT / "airplay_2_multiroom_audio_beta/generate_airplay2_services.sh").read_text()
        self.assertNotIn('if [ -d "$service_dir" ]; then', stable)
        for generator in (stable, beta):
            avahi = generator.split('AVAHI_SERVICE="${BASE_DIR}/airplay2-avahi"', 1)[1]
            avahi_setup, _ = avahi.split('NQPTP_SERVICE=', 1)
            self.assertIn('fi\ncat > "${AVAHI_SERVICE}/run"', avahi_setup)
            cast = generator.split('CASTBRIDGE_MANAGER_SERVICE="${BASE_DIR}/airplay2-castbridge-manager"', 1)[1]
            cast_setup = cast.split('# One manager for every PulseAudio-sink zone', 1)[0] \
                if '# One manager for every PulseAudio-sink zone' in cast else cast
            self.assertIn('fi\ncat > "${CASTBRIDGE_MANAGER_SERVICE}/run"', cast_setup)
        beta_sink = beta.split('SINK_ZONE_MANAGER_SERVICE="${BASE_DIR}/airplay2-sink-zone-manager"', 1)[1]
        self.assertRegex(beta_sink, r'fi\n(?:#.*\n)?cat > "\$\{SINK_ZONE_MANAGER_SERVICE\}/run"')
        self.assertIn('cat > "${config_file}" <<EOF', stable)


if __name__ == "__main__":
    unittest.main()
