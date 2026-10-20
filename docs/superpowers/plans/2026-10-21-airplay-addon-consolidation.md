# AirPlay Add-on Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate Stable and Beta into one AirPlay add-on while keeping the Stable slug and adding Beta's supported features.

**Architecture:** The existing Stable add-on remains canonical. Promote Beta's standalone Bluetooth zone logic and more complete process/port management into Stable, carry over its visual assets, then remove the Beta add-on tree and update tests and migration documentation.

**Tech Stack:** Home Assistant add-on YAML, Bash, Python, Markdown, unittest.

**Spec:** `docs/superpowers/specs/2026-10-21-airplay-addon-consolidation-design.md`

## Global Constraints

- Keep the canonical slug `airplay_2_multiroom_audio` and its current persistent paths.
- Preserve current receiver names, interface resolution, Shairport Sync/NQPTP settings, PulseAudio routing, and custom zone configuration.
- Add standalone Bluetooth A2DP zones; prefer the remap zone when one sits on the Bluetooth sink.
- Set the consolidated add-on version to `1.10` and remove the Beta add-on metadata/tree.
- Document that Beta options and slug-scoped data require manual migration.

## Review Focus

- A Bluetooth sink with a remap sink above it must not create duplicate AirPlay zones; retain and adapt the existing Beta test.
- PulseAudio query failures must not tear down existing Bluetooth zones; retain Beta manager recovery behavior.
- Sink and Cast managers must continue sharing a locked port map; preserve Beta's flock logic.
- Cast and sink receivers must continue honoring log level and generated receiver names/settings.
- Removing Beta must leave no active test or add-on source reference to a deleted path; keep historical planning notes and migration instructions explicit.

---

### Task 1: Promote Beta runtime behavior into Stable

**Files:**
- Modify: `airplay_2_multiroom_audio/sink_zone_manager.py`
- Modify: `airplay_2_multiroom_audio/cast_bridge_manager.py`
- Modify: `airplay_2_multiroom_audio/generate_airplay2_services.sh`
- Modify: `airplay_2_multiroom_audio/README.md`

- [ ] Promote the Beta sink manager, preserving Stable option paths and name/network/audio settings while enabling standalone A2DP discovery.
- [ ] Promote Beta's shared port-map locking, log lifecycle handling, and startup log preparation into the canonical runtime.
- [ ] Remove variant-specific wording and document standalone Bluetooth zone behavior in the canonical README.
- [ ] Review that Beta functionality and Stable custom configuration semantics are both present.

### Task 2: Consolidate add-on metadata and assets

**Files:**
- Modify: `airplay_2_multiroom_audio/config.yaml`
- Modify: `airplay_2_multiroom_audio/en.yaml`
- Create: `airplay_2_multiroom_audio/icon.png`
- Create: `airplay_2_multiroom_audio/logo.svg`
- Modify: `airplay_2_multiroom_audio/README.md`
- Delete: `airplay_2_multiroom_audio_beta/`

- [ ] Set the canonical add-on version to `1.10`, keep its Stable slug, and remove Beta wording from the visible name.
- [ ] Carry over the Beta icon and logo.
- [ ] Add manual Beta migration steps covering UI options and slug-scoped custom files/port maps.
- [ ] Remove the Beta add-on directory and its duplicate Dockerfile, `Dockerfile.txt`, and metadata.

### Task 3: Retarget repository tests to the canonical add-on

**Files:**
- Modify: `tests/test_airplay_name.py`
- Modify: `tests/test_network_interface.py`
- Modify: `tests/test_playback_reliability.py`

- [ ] Remove loops and path checks that require a second add-on directory.
- [ ] Retarget the existing Beta Bluetooth discovery/remap-precedence assertions to the canonical sink manager.
- [ ] Retain coverage for automatic/manual interface, receiver name, lossless mode, buffer and rate configuration, shared-memory naming, cast/sink config generation, and startup paths.
- [ ] Remove obsolete version and duplicate-file expectations.

### Task 4: Review repository references and validation

**Files:**
- Inspect: root `README.md`, `repository.yaml`, active `docs/`, and all tracked files.

- [ ] Ensure active add-on metadata and tests target only the canonical slug; retain only necessary Beta migration references and historical records.
- [ ] Run the repository test suite, Python compile checks, shell syntax checks, YAML validation, Dockerfile checks, and `git diff --check` if permitted by the task's validation instructions.
- [ ] Report actual results and limitations, including no Home Assistant OS or physical Bluetooth/AirPlay runtime claim.

