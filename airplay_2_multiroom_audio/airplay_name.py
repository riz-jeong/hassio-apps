#!/usr/bin/env python3
"""Resolve and validate the optional AirPlay receiver display name."""

import json
import os
import sys
import unicodedata
from pathlib import Path


MAX_NAME_BYTES = 63
MAX_NAME_CHARACTERS = 50


def validate_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("airplay_name must be 'auto' or a non-empty receiver name")
    name = value.strip()
    if any(unicodedata.category(character) == "Cc" for character in name):
        raise ValueError("airplay_name cannot contain control characters or line breaks")
    if len(name) > MAX_NAME_CHARACTERS:
        raise ValueError("airplay_name must be 50 characters or fewer for Shairport Sync")
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise ValueError("airplay_name must be 63 UTF-8 bytes or fewer for mDNS")
    return name


def configured_name(default_name, options_file=None):
    options_file = Path(options_file or os.environ.get("AIRPLAY_OPTIONS_FILE", "/data/options.json"))
    try:
        options = json.loads(Path(options_file).read_text())
    except FileNotFoundError:
        options = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read add-on options from {options_file}: {exc}") from exc
    if not isinstance(options, dict):
        raise ValueError(f"add-on options in {options_file} must be a JSON object")
    configured = options.get("airplay_name", "auto")
    if isinstance(configured, str) and configured.strip().lower() == "auto":
        return default_name
    if isinstance(configured, str) and "{name}" in configured:
        configured = configured.replace("{name}", default_name)
    return validate_name(configured)


def main():
    default_name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        print(configured_name(default_name))
    except ValueError as exc:
        print(f"AirPlay receiver name selection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
