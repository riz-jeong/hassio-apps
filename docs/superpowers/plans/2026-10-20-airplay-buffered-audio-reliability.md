# AirPlay Buffered Audio Reliability Implementation Plan

> **For agentic workers:** Native implementation in this session, following the user's direct request.

**Goal:** Reduce buffered AirPlay 2 audio underruns for the observed iOS 27.2 beta sender while keeping the buffer tunable in Stable and Beta.

**Architecture:** Set Shairport Sync's `audio_decoded_buffer_desired_length_in_seconds` for PulseAudio sink zones from a validated add-on option. Default to 5 seconds, allow integer values 1–10, and let an existing per-zone custom setting take precedence to preserve custom configuration. Keep Cast pipe-zone buffering unchanged because it has a separate output path and existing latency settings.

**Tech Stack:** Python, Home Assistant add-on YAML, unittest.

**Spec:** User request to fix playback reliability from AirPlay logs, with Spotify working and intermittent YouTube Music transitions.

## Global Constraints

- Preserve network-interface selection, receiver naming, selected PulseAudio sink, custom settings, and multiroom behavior.
- Do not restart Shairport Sync for ordinary track transitions or force volume/mute changes.
- Do not claim YouTube Music is fixed without client runtime validation.

## Review Focus

- Invalid or absent option values must have clear, deterministic handling and a safe default.
- Custom per-zone decoded-buffer settings must not produce duplicate libconfig directives.
- Stable and Beta must generate the same setting for matching sink zones.
- Buffer tuning must not change the distinct Cast pipe-zone buffer.

### Task 1: Configure and test buffered audio depth

**Files:** `tests/test_playback_reliability.py`, both `sink_zone_manager.py`, both `config.yaml`, both `en.yaml`, both add-on READMEs.

- [x] Add failing tests for default, configured, invalid, generated, and custom-override behavior in both variants.
- [x] Run focused tests to confirm expected failures.
- [x] Implement validated option resolution and generate exactly one Shairport Sync directive per sink zone.
- [x] Document the option and its applicability to buffered AirPlay 2 audio.
- [x] Run final full tests, syntax/YAML checks, and diff validation.
