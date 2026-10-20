"""Helpers for stable, per-instance Shairport Sync configuration."""

import hashlib
import json
from pathlib import Path


def nqptp_shared_memory_name(kind, identifier):
    """Create a deterministic, valid NQPTP IPC name unique to one instance."""
    if kind not in ("sink", "cast"):
        raise ValueError("NQPTP instance kind must be 'sink' or 'cast'")
    digest = hashlib.sha256(str(identifier).encode("utf-8")).hexdigest()[:16]
    return f"/{kind}-{digest}"


def configured_lossless_mode(options_file):
    """Return the validated Shairport Sync AirPlay 2 lossless mode option."""
    try:
        options = json.loads(Path(options_file).read_text())
    except FileNotFoundError:
        options = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read add-on options from {options_file}: {exc}") from exc
    if not isinstance(options, dict):
        raise ValueError(f"add-on options in {options_file} must be a JSON object")
    mode = options.get("airplay_lossless_mode", "on")
    if mode not in ("on", "off"):
        raise ValueError("airplay_lossless_mode must be on or off")
    return mode
