# AirPlay Add-on Consolidation Design

## Goal

Maintain one AirPlay 2 multiroom add-on by promoting Beta's supported functionality into the existing Stable add-on and removing the separate Beta add-on.

## Approved direction

Keep the existing `airplay_2_multiroom_audio` slug and add-on identity. Merge Beta's standalone Bluetooth A2DP receiver support and its newer shared runtime behavior into that add-on. Remove `airplay_2_multiroom_audio_beta` as an installable add-on. Advance the canonical add-on version from `1.09` to `1.10`.

This minimizes disruption for current Stable installations. The Beta slug will no longer receive updates, and Beta users will need to move to the canonical add-on.

## Functional scope

- The consolidated add-on supports the current Stable behavior, including remap sinks, Cast receivers, receiver naming, automatic/manual network interface selection, sample-rate selection, lossless mode, logging controls, and per-zone custom configuration.
- It also supports Beta's standalone Bluetooth A2DP sinks. If a Bluetooth sink has a remap sink on top of it, only the remap sink becomes a receiver.
- Use Beta's newer receiver management behavior where it is more complete, including shared port-map locking and per-process log handling, while preserving existing Stable settings and paths.
- Preserve the Stable add-on slug and configuration paths. Carry over Beta's icon and logo to the consolidated add-on.
- Keep one Dockerfile and one configuration schema for the active add-on. Remove Beta-only Dockerfile copies and duplicate source trees.

## Configuration and identity

The canonical UI retains the current Stable options and defaults, including `airplay_interface`, `airplay_name`, `airplay_decoded_buffer_seconds`, `airplay_lossless_mode`, and `log_level`. Stable receiver IDs, stable port-map semantics, and the Stable add-on configuration directory remain unchanged.

The add-on release version becomes `1.10`. The visible add-on name remains `Airplay2 Multiroom Audio` without a Beta suffix.

## Beta-user migration

Home Assistant stores add-on options and add-on configuration data by slug. Therefore removing the Beta add-on cannot automatically transfer its options or files to the Stable slug. The consolidated README will provide a manual migration procedure that asks Beta users to:

1. Record their Beta UI option values and stop the Beta add-on.
2. Back up and copy any persistent zone custom files and port-map data from the Beta add-on configuration directory to the corresponding Stable directory.
3. Install or update `airplay_2_multiroom_audio`, restore the option values in its configuration UI, and verify receiver zones.
4. Keep the Beta backup until the consolidated add-on has been verified, then uninstall the obsolete Beta add-on.

Stable users can update in place and do not need to change their add-on slug or data paths.

## Repository changes

- Merge Beta's sink-zone manager implementation into Stable and revise its Stable-only behavior/comments so standalone Bluetooth sinks are managed.
- Merge Beta's more complete service startup and Cast bridge management behavior into Stable without changing receiver names, network selection, audio routing, NQPTP isolation, or existing custom configuration semantics.
- Update the canonical `config.yaml`, translations, and README for the consolidated feature set, name, version, and migration steps.
- Move the Beta icon/logo assets into the canonical add-on directory.
- Remove the Beta add-on directory, including its duplicate Dockerfile, `Dockerfile.txt`, source files, metadata, and assets after they are merged.
- Rewrite tests to target the one canonical add-on and add coverage for standalone Bluetooth sink discovery, remap precedence, and merged service behavior.
- Leave the dated historical playback-reliability plan intact as a historical record; remove Beta references from active configuration, code, tests, and user-facing documentation except the migration instructions.

## Verification

After implementation, run the repository unit tests, Python compilation checks, shell syntax checks, YAML parsing, Dockerfile consistency checks, and `git diff --check`. Where Home Assistant OS, PulseAudio Bluetooth hardware, or a live AirPlay client is required, report that limitation rather than claiming runtime validation.

## Risks and constraints

- Beta users must manually recreate UI options because Home Assistant does not transfer options between different add-on slugs.
- Beta users' custom zone files and port maps live under the Beta-specific add-on configuration directory and require backup/copy for continuity.
- Bluetooth discovery depends on PulseAudio reporting connected A2DP sinks with the expected BlueZ sink identifiers; the existing Beta behavior and tests define the supported detection rules.
- Keep the migration instructions explicit so users do not remove their Beta backup before confirming receiver identity, zone settings, and playback.
