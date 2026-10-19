import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_manager(addon_name, module_name):
    addon = ROOT / addon_name
    sys.path.insert(0, str(addon))
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, addon / "sink_zone_manager.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(addon))


class PlaybackReliabilityTests(unittest.TestCase):
    def test_both_addons_expose_the_same_bounded_buffer_option(self):
        for addon, version in (("airplay_2_multiroom_audio", '"1.06"'),
                               ("airplay_2_multiroom_audio_beta", '"0.14"')):
            with self.subTest(addon=addon):
                schema = (ROOT / addon / "config.yaml").read_text()
                translation = (ROOT / addon / "en.yaml").read_text()
                readme = (ROOT / addon / "README.md").read_text()
                self.assertIn(f"version: {version}", schema)
                self.assertIn("airplay_decoded_buffer_seconds: 5", schema)
                self.assertIn("airplay_decoded_buffer_seconds: int(1,10)", schema)
                self.assertIn("airplay_decoded_buffer_seconds:", translation)
                self.assertIn("audio_decoded_buffer_desired_length_in_seconds", readme)
                self.assertIn("Shairport Sync `5.6`", readme)
                self.assertIn("NQPTP `1.2.9`", readme)

    def test_addons_pin_compatible_latest_shairport_and_nqptp_releases(self):
        dockerfiles = (
            ROOT / "airplay_2_multiroom_audio" / "Dockerfile",
            ROOT / "airplay_2_multiroom_audio_beta" / "Dockerfile",
            ROOT / "airplay_2_multiroom_audio_beta" / "Dockerfile.txt",
        )
        for dockerfile in dockerfiles:
            with self.subTest(dockerfile=dockerfile):
                content = dockerfile.read_text()
                self.assertIn("ARG SHAIRPORT_SYNC_VERSION=5.6", content)
                self.assertIn("ARG NQPTP_VERSION=1.2.9", content)
                self.assertIn('git checkout --detach "${NQPTP_VERSION}"', content)
                self.assertIn('git checkout --detach "${SHAIRPORT_SYNC_VERSION}"', content)
                self.assertIn("Shared Memory Interface Version: smi11.", content)
                self.assertNotIn("git switch development", content)
        self.assertEqual(dockerfiles[1].read_text(), dockerfiles[2].read_text())

    def test_startup_logs_shairport_and_nqptp_build_versions(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon):
                script = (ROOT / addon / "generate_airplay2_services.sh").read_text()
                self.assertIn('shairport-sync -V', script)
                self.assertIn('nqptp -V', script)

    def test_cast_pipe_buffer_remains_separate_from_sink_zone_buffer(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon):
                manager = (ROOT / addon / "cast_bridge_manager.py").read_text()
                self.assertIn("audio_backend_buffer_desired_length_in_seconds = 0.1;", manager)
                self.assertIn("audio_decoded_buffer_desired_length_in_seconds = 0.35;", manager)

    def test_stable_starts_the_sink_zone_manager_instead_of_static_zone_services(self):
        script = (ROOT / "airplay_2_multiroom_audio" /
                  "generate_airplay2_services.sh").read_text()
        self.assertIn("airplay2-sink-zone-manager", script)
        self.assertIn("sink_zone_manager.py", script)
        self.assertNotIn('touch "${CONTENTS_DIR}/airplay2-${friendly_name}"', script)

    def test_each_relevant_container_build_copies_its_sink_manager(self):
        for dockerfile in (
            ROOT / "airplay_2_multiroom_audio" / "Dockerfile",
            ROOT / "airplay_2_multiroom_audio_beta" / "Dockerfile",
            ROOT / "airplay_2_multiroom_audio_beta" / "Dockerfile.txt",
        ):
            with self.subTest(dockerfile=dockerfile):
                self.assertIn("COPY sink_zone_manager.py", dockerfile.read_text())

    def test_both_managers_target_the_resolved_pulse_sink(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon), tempfile.TemporaryDirectory() as tmp:
                manager = load_manager(addon, f"{addon}_sink_manager")
                manager.CONFIG_DIR = Path(tmp)
                manager.LOG_DIR = Path(tmp)
                zone = manager.Zone("internal_zone", 5120, 7011,
                                    pa_sink="airplay_usb_dac")
                zone._ensure_custom_file()
                zone._write_config()
                config = zone.config_file.read_text()
                self.assertIn('output_backend = "pulseaudio";', config)
                self.assertIn('sink = "airplay_usb_dac";', config)
                self.assertEqual(1, config.count('sink ='))

    def test_buffered_audio_depth_defaults_to_five_seconds_and_is_configurable(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon), tempfile.TemporaryDirectory() as tmp:
                manager = load_manager(addon, f"{addon}_audio_buffer")
                manager.OPTIONS_FILE = Path(tmp) / "options.json"
                manager.CONFIG_DIR = Path(tmp)
                manager.LOG_DIR = Path(tmp)
                zone = manager.Zone("airplay_usb_dac", 5120, 7011,
                                    pa_sink="airplay_usb_dac")
                zone._ensure_custom_file()
                zone._write_config()
                generated = zone.config_file.read_text()
                self.assertIn("audio_decoded_buffer_desired_length_in_seconds = 5.0;",
                              generated)

                manager.OPTIONS_FILE.write_text('{"airplay_decoded_buffer_seconds": 3}')
                zone._write_config()
                generated = zone.config_file.read_text()
                self.assertIn("audio_decoded_buffer_desired_length_in_seconds = 3.0;",
                              generated)
                self.assertEqual(1, generated.count(
                    "audio_decoded_buffer_desired_length_in_seconds ="))

    def test_invalid_buffered_audio_depth_fails_clearly(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            for value in (0, 11, True, "5"):
                with self.subTest(addon=addon, value=value), tempfile.TemporaryDirectory() as tmp:
                    manager = load_manager(addon, f"{addon}_invalid_audio_buffer_{value}")
                    manager.OPTIONS_FILE = Path(tmp) / "options.json"
                    manager.OPTIONS_FILE.write_text(
                        json.dumps({"airplay_decoded_buffer_seconds": value}))
                    manager.CONFIG_DIR = Path(tmp)
                    manager.LOG_DIR = Path(tmp)
                    zone = manager.Zone("airplay_usb_dac", 5120, 7011,
                                        pa_sink="airplay_usb_dac")
                    zone._ensure_custom_file()
                    with self.assertRaisesRegex(
                            ValueError,
                            "airplay_decoded_buffer_seconds must be an integer from 1 to 10"):
                        zone._write_config()

    def test_custom_buffer_setting_takes_precedence_without_duplicate_directive(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon), tempfile.TemporaryDirectory() as tmp:
                manager = load_manager(addon, f"{addon}_custom_audio_buffer")
                manager.OPTIONS_FILE = Path(tmp) / "options.json"
                manager.OPTIONS_FILE.write_text('{"airplay_decoded_buffer_seconds": 5}')
                manager.CONFIG_DIR = Path(tmp)
                manager.LOG_DIR = Path(tmp)
                zone = manager.Zone("airplay_usb_dac", 5120, 7011,
                                    pa_sink="airplay_usb_dac")
                zone.custom_file.write_text(
                    "audio_decoded_buffer_desired_length_in_seconds = 2.5;\n")
                zone._write_config()
                generated = zone.config_file.read_text()
                self.assertNotIn("audio_decoded_buffer_desired_length_in_seconds =", generated)
                self.assertIn("audio_decoded_buffer_desired_length_in_seconds = 2.5;",
                              zone.custom_file.read_text())

    def test_generated_configs_preserve_custom_settings_without_forcing_mute(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon), tempfile.TemporaryDirectory() as tmp:
                manager = load_manager(addon, f"{addon}_custom_config")
                manager.CONFIG_DIR = Path(tmp)
                manager.LOG_DIR = Path(tmp)
                zone = manager.Zone("airplay_usb_dac", 5120, 7011,
                                    pa_sink="airplay_usb_dac")
                zone.custom_file.write_text(
                    "audio_backend_latency_offset_in_seconds = -0.15;\n")
                zone._write_config()
                generated = zone.config_file.read_text()
                custom = zone.custom_file.read_text()
                self.assertIn("@include", generated)
                self.assertIn("audio_backend_latency_offset_in_seconds = -0.15;", custom)
                self.assertNotIn("ignore_volume_control", generated)
                self.assertNotIn("mute_using_playback_control", generated)

    def test_sink_query_failure_does_not_remove_running_zone(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon):
                manager = load_manager(addon, f"{addon}_manager_query_failure")
                existing = SimpleNamespace(stop=unittest.mock.Mock())
                instance = manager.Manager()
                instance.booted = True
                instance.zones = {"airplay_usb_dac": existing}
                with patch.object(manager, "list_remap_sinks", return_value=None):
                    instance.reconcile()
                self.assertIs(instance.zones["airplay_usb_dac"], existing)
                existing.stop.assert_not_called()

    def test_pulseaudio_restart_restarts_a_zone_without_changing_its_sink(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon):
                manager = load_manager(addon, f"{addon}_manager_restart")
                old_zone = SimpleNamespace(stop=unittest.mock.Mock())
                new_zone = SimpleNamespace()
                instance = manager.Manager()
                instance.booted = True
                instance.zones = {"airplay_usb_dac": old_zone}
                instance.pulse_restarted = True
                with ExitStack() as stack:
                    stack.enter_context(patch.object(
                        manager, "list_remap_sinks", return_value=["airplay_usb_dac"]))
                    stack.enter_context(patch.object(manager, "remap_modules", return_value=[]))
                    if hasattr(manager, "bluetooth_sinks"):
                        stack.enter_context(patch.object(manager, "bluetooth_sinks", return_value={}))
                    stack.enter_context(patch.object(manager, "bounce_and_recreate_remaps"))
                    stack.enter_context(patch.object(manager.time, "sleep"))
                    stack.enter_context(patch.object(instance.stop_event, "wait"))
                    stack.enter_context(patch.object(
                        instance, "_start_zone", side_effect=lambda sink: instance.zones.update(
                            {sink: new_zone})))
                    instance.reconcile()
                old_zone.stop.assert_called_once()
                self.assertIs(instance.zones["airplay_usb_dac"], new_zone)

    def test_recovery_does_not_reuse_a_sink_list_that_disappeared(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            for recovered in ([], None):
                with self.subTest(addon=addon, recovered=recovered):
                    manager = load_manager(addon, f"{addon}_manager_missing_sink_{recovered is None}")
                    old_zone = SimpleNamespace(stop=unittest.mock.Mock())
                    instance = manager.Manager()
                    instance.booted = True
                    instance.zones = {"airplay_usb_dac": old_zone}
                    instance.pulse_restarted = True
                    start_zone = unittest.mock.Mock()
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(manager, "list_remap_sinks",
                                                         side_effect=[["airplay_usb_dac"], recovered]))
                        stack.enter_context(patch.object(manager, "remap_modules", return_value=[]))
                        if hasattr(manager, "bluetooth_sinks"):
                            stack.enter_context(patch.object(manager, "bluetooth_sinks", return_value={}))
                        stack.enter_context(patch.object(manager, "bounce_and_recreate_remaps"))
                        stack.enter_context(patch.object(instance.stop_event, "wait"))
                        stack.enter_context(patch.object(instance, "_start_zone", start_zone))
                        instance.reconcile()
                    old_zone.stop.assert_called_once()
                    start_zone.assert_not_called()
                    self.assertEqual({}, instance.zones)

    def test_zone_returns_after_sink_reappears_after_recovery(self):
        for addon in ("airplay_2_multiroom_audio", "airplay_2_multiroom_audio_beta"):
            with self.subTest(addon=addon):
                manager = load_manager(addon, f"{addon}_manager_sink_returns")
                old_zone = SimpleNamespace(stop=unittest.mock.Mock())
                new_zone = SimpleNamespace()
                instance = manager.Manager()
                instance.booted = True
                instance.zones = {"airplay_usb_dac": old_zone}
                instance.pulse_restarted = True
                with ExitStack() as stack:
                    stack.enter_context(patch.object(
                        manager, "list_remap_sinks",
                        side_effect=[["airplay_usb_dac"], [], ["airplay_usb_dac"]]))
                    stack.enter_context(patch.object(manager, "remap_modules", return_value=[]))
                    if hasattr(manager, "bluetooth_sinks"):
                        stack.enter_context(patch.object(manager, "bluetooth_sinks", return_value={}))
                    stack.enter_context(patch.object(manager, "bounce_and_recreate_remaps"))
                    stack.enter_context(patch.object(manager.time, "sleep"))
                    stack.enter_context(patch.object(instance.stop_event, "wait"))
                    stack.enter_context(patch.object(
                        instance, "_start_zone", side_effect=lambda sink: instance.zones.update(
                            {sink: new_zone})))
                    instance.reconcile()
                    self.assertEqual({}, instance.zones)
                    instance.reconcile()
                old_zone.stop.assert_called_once()
                self.assertIs(instance.zones["airplay_usb_dac"], new_zone)

    def test_stable_manager_keeps_remap_sinks_as_its_only_zone_type(self):
        manager = load_manager("airplay_2_multiroom_audio",
                               "stable_sink_manager_no_bluetooth")
        instance = manager.Manager()
        self.assertEqual(["airplay_usb_dac"], instance._desired(["airplay_usb_dac"]))
        self.assertFalse(hasattr(manager, "bluetooth_sinks"))


if __name__ == "__main__":
    unittest.main()
