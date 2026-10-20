import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_resolver(addon):
    path = ROOT / addon / "airplay_name.py"
    spec = importlib.util.spec_from_file_location(f"{addon}_airplay_name_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AirplayNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.options = Path(self.temp.name) / "options.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_auto_preserves_the_existing_per_receiver_name(self):
        for addon in ("airplay_2_multiroom_audio",):
            with self.subTest(addon=addon):
                self.options.write_text(json.dumps({"airplay_name": "auto"}))
                resolver = load_resolver(addon)
                self.assertEqual("airplay usb dac", resolver.configured_name("airplay usb dac", self.options))

    def test_auto_uses_the_fallback_when_the_option_is_absent(self):
        resolver = load_resolver("airplay_2_multiroom_audio")
        self.options.write_text("{}")
        self.assertEqual("airplay usb dac", resolver.configured_name("airplay usb dac", self.options))

    def test_custom_name_preserves_spaces_and_unicode(self):
        self.options.write_text(json.dumps({"airplay_name": "JCH Hi-Fi 🎵"}, ensure_ascii=False))
        resolver = load_resolver("airplay_2_multiroom_audio")
        self.assertEqual("JCH Hi-Fi 🎵", resolver.configured_name("old name", self.options))

    def test_custom_template_keeps_receiver_names_distinct(self):
        self.options.write_text(json.dumps({"airplay_name": "Living Room - {name}"}))
        resolver = load_resolver("airplay_2_multiroom_audio")
        self.assertEqual("Living Room - USB DAC", resolver.configured_name("USB DAC", self.options))

    def test_empty_control_and_oversized_names_are_rejected(self):
        resolver = load_resolver("airplay_2_multiroom_audio")
        for value in ("", "   ", "room\nname", "é" * 32, "r" * 51):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolver.validate_name(value)

    def test_empty_configured_name_fails_with_an_actionable_error(self):
        self.options.write_text(json.dumps({"airplay_name": " "}))
        env = {**os.environ, "AIRPLAY_OPTIONS_FILE": str(self.options)}
        result = subprocess.run(
            [sys.executable, str(ROOT / "airplay_2_multiroom_audio/airplay_name.py"), "old name"],
            env=env, capture_output=True, text=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("airplay_name must be 'auto' or a non-empty receiver name", result.stderr)



if __name__ == "__main__":
    unittest.main()
