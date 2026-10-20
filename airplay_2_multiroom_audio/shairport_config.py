"""Helpers for stable, per-instance Shairport Sync configuration."""

import hashlib
import json
from pathlib import Path


def _options(options_file):
    try:
        options = json.loads(Path(options_file).read_text())
    except FileNotFoundError:
        options = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read add-on options from {options_file}: {exc}") from exc
    if not isinstance(options, dict):
        raise ValueError(f"add-on options in {options_file} must be a JSON object")
    return options


def _choice(options_file, key, default, choices):
    value = _options(options_file).get(key, default)
    value = str(value)
    if value not in choices:
        allowed = ", ".join(choices)
        raise ValueError(f"{key} must be one of: {allowed}")
    return value


def configured_airplay_output_rate(options_file, detected_rate):
    choice = _choice(options_file, "airplay_output_rate", "device",
                     ("device", "auto", "44100", "48000", "88200", "96000", "176400", "192000"))
    if choice == "device":
        return detected_rate if type(detected_rate) is int else "auto"
    return int(choice) if choice.isdigit() else choice


def configured_airplay_backend_buffer(options_file):
    choice = _choice(options_file, "airplay_backend_buffer_seconds", "0.35",
                     ("0.15", "0.20", "0.35", "0.50", "0.75", "1.00"))
    return float(choice)


def configured_airplay_interpolation(options_file):
    return _choice(options_file, "airplay_interpolation", "auto",
                   ("auto", "vernier", "soxr"))


def configured_cast_backend_buffer(options_file):
    choice = _choice(options_file, "cast_backend_buffer_seconds", "0.10",
                     ("0.10", "0.15", "0.20", "0.35", "0.50"))
    return float(choice)


def configured_cast_decoded_buffer(options_file):
    choice = _choice(options_file, "cast_decoded_buffer_seconds", "0.35",
                     ("0.10", "0.20", "0.35", "0.50", "1.00"))
    return float(choice)


def configured_cast_output_rate(options_file, default=48000):
    return int(_choice(options_file, "cast_output_rate", str(default), ("44100", "48000")))


def nqptp_shared_memory_name(kind, identifier):
    """Create a deterministic, valid NQPTP IPC name unique to one instance."""
    if kind not in ("sink", "cast"):
        raise ValueError("NQPTP instance kind must be 'sink' or 'cast'")
    digest = hashlib.sha256(str(identifier).encode("utf-8")).hexdigest()[:16]
    return f"/{kind}-{digest}"


def configured_lossless_mode(options_file):
    """Return the validated Shairport Sync AirPlay 2 lossless mode option."""
    return _choice(options_file, "airplay_lossless_mode", "on", ("on", "off"))
