import importlib.util
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_peq():
    addon = ROOT / "airplay_2_multiroom_audio"
    sys.path.insert(0, str(addon))
    try:
        spec = importlib.util.spec_from_file_location("parametric_eq_test", addon / "parametric_eq.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(addon))


class ParametricEqTests(unittest.TestCase):
    def test_disabled_by_default_and_zero_gain_bands_are_bypassed(self):
        peq = load_peq()
        with tempfile.TemporaryDirectory() as tmp:
            options = Path(tmp) / "options.json"
            options.write_text("{}")
            settings = peq.load_settings(options)
            self.assertFalse(settings.enabled)
            self.assertEqual(5, len(settings.bands))
            self.assertEqual(0.0, settings.max_boost_db)
            self.assertIsNone(peq.generate_impulse_response(settings, 48000, Path(tmp)))

    def test_enabled_band_generates_mono_pcm16_ir_for_supported_rates(self):
        peq = load_peq()
        with tempfile.TemporaryDirectory() as tmp:
            options = Path(tmp) / "options.json"
            options.write_text(json.dumps({
                "airplay_eq_enabled": True,
                "airplay_eq_band_1_frequency": 1000,
                "airplay_eq_band_1_gain_db": 6,
            }))
            settings = peq.load_settings(options)
            generated = peq.generate_impulse_response(settings, 48000, Path(tmp))
            self.assertIsNotNone(generated)
            with wave.open(str(generated), "rb") as ir:
                self.assertEqual(1, ir.getnchannels())
                self.assertEqual(2, ir.getsampwidth())
                self.assertEqual(48000, ir.getframerate())
                self.assertGreater(ir.getnframes(), 1)
            high_rate = peq.generate_impulse_response(settings, 96000, Path(tmp))
            with wave.open(str(high_rate), "rb") as ir:
                self.assertEqual(96000, ir.getframerate())

    def test_invalid_band_values_fail_with_option_name(self):
        peq = load_peq()
        with tempfile.TemporaryDirectory() as tmp:
            options = Path(tmp) / "options.json"
            options.write_text(json.dumps({
                "airplay_eq_band_1_frequency": 24000,
            }))
            with self.assertRaisesRegex(ValueError, "airplay_eq_band_1_frequency"):
                peq.load_settings(options)

    def test_impulse_response_rejects_unsupported_sample_rates(self):
        peq = load_peq()
        with tempfile.TemporaryDirectory() as tmp:
            options = Path(tmp) / "options.json"
            options.write_text(json.dumps({
                "airplay_eq_enabled": True,
                "airplay_eq_band_1_gain_db": 3,
            }))
            settings = peq.load_settings(options)
            with self.assertRaisesRegex(ValueError, "44100"):
                peq.generate_impulse_response(settings, 32000, Path(tmp))

    def test_shairport_config_matches_fixed_and_automatic_output_rates(self):
        peq = load_peq()
        with tempfile.TemporaryDirectory() as tmp:
            options = Path(tmp) / "options.json"
            options.write_text(json.dumps({
                "airplay_eq_enabled": True,
                "airplay_eq_band_1_gain_db": 4,
            }))
            fixed = peq.shairport_dsp_config(options, 48000, Path(tmp) / "fixed")
            automatic = peq.shairport_dsp_config(options, "auto", Path(tmp) / "auto")
            self.assertIn('convolution_enabled = "yes";', fixed)
            self.assertIn("airplay-peq-48000.wav", fixed)
            self.assertNotIn("airplay-peq-44100.wav", fixed)
            self.assertIn("airplay-peq-44100.wav", automatic)
            self.assertIn("airplay-peq-48000.wav", automatic)
            self.assertIn("convolution_gain = -", automatic)
            high_rate = peq.shairport_dsp_config(options, 96000, Path(tmp) / "high-rate")
            self.assertIn("airplay-peq-96000.wav", high_rate)

    def test_addon_exposes_disabled_five_band_peq_with_documented_defaults(self):
        config = (ROOT / "airplay_2_multiroom_audio" / "config.yaml").read_text()
        translation = (ROOT / "airplay_2_multiroom_audio" / "en.yaml").read_text()
        readme = (ROOT / "airplay_2_multiroom_audio" / "README.md").read_text()
        self.assertIn("airplay_eq_enabled: false", config)
        self.assertIn("airplay_eq_enabled: bool", config)
        self.assertIn("Parametric EQ", translation)
        self.assertIn("airplay_eq_band_5_q", config)
        self.assertIn("five-band Parametric EQ", readme)

    def test_both_shairport_startup_paths_apply_the_generated_dsp_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            options = tmp / "options.json"
            options.write_text(json.dumps({
                "airplay_eq_enabled": True,
                "airplay_eq_band_2_frequency": 500,
                "airplay_eq_band_2_gain_db": -3,
                "airplay_eq_band_2_q": 1.2,
            }))

            addon = ROOT / "airplay_2_multiroom_audio"
            sys.path.insert(0, str(addon))
            try:
                spec = importlib.util.spec_from_file_location(
                    "parametric_eq_sink_manager", addon / "sink_zone_manager.py")
                manager = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(manager)
            finally:
                sys.path.remove(str(addon))
            manager.OPTIONS_FILE = options
            manager.CONFIG_DIR = tmp / "sink-config"
            manager.LOG_DIR = tmp / "logs"
            manager.PEQ_DIR = tmp / "sink-peq"
            manager.AIRPLAY_INTERFACE = "eth0"
            manager.CONFIG_DIR.mkdir(parents=True)
            zone = manager.Zone("usb", 5120, 7011, pa_sink="usb", output_rate=48000)
            zone._ensure_custom_file()
            zone._write_config()
            self.assertIn("convolution_enabled", zone.config_file.read_text())

            sys.path.insert(0, str(addon))
            try:
                spec = importlib.util.spec_from_file_location(
                    "parametric_eq_cast_manager", addon / "cast_bridge_manager.py")
                cast_manager = importlib.util.module_from_spec(spec)
                with patch.dict(sys.modules, {
                    "pychromecast": SimpleNamespace(),
                    "zeroconf": SimpleNamespace(ServiceListener=object),
                }):
                    spec.loader.exec_module(cast_manager)
            finally:
                sys.path.remove(str(addon))
            cast_manager.OPTIONS_FILE = options
            cast_manager.CONFIG_DIR = tmp / "cast-config"
            cast_manager.LOG_DIR = tmp / "cast-logs"
            cast_manager.PIPE_DIR = tmp / "pipes"
            cast_manager.PEQ_DIR = tmp / "cast-peq"
            cast_manager.AIRPLAY_INTERFACE = "eth0"
            cast_manager.configured_name = lambda name: name
            cast_manager.CONFIG_DIR.mkdir(parents=True)
            cast_manager.open_log = lambda path: None
            with unittest.mock.patch.object(cast_manager.subprocess, "Popen"):
                cast_manager.start_shairport("cast", str(tmp / "pipes" / "cast.pipe"),
                                              5150, 6800)
            cast_config = (tmp / "cast-config" / "cast-ap2.conf").read_text()
            self.assertIn("convolution_enabled", cast_config)


if __name__ == "__main__":
    unittest.main()
