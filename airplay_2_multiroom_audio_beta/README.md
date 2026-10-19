# AirPlay 2 Multiroom Audio

Turn your existing PulseAudio-backed audio hardware **and** your Google Cast (Chromecast/Google Home) speakers into AirPlay 2 receivers, so you can play to all of them — individually or grouped — from any AirPlay 2 source (iPhone, iPad, Mac).

This addon merges what used to be two separate things — AirPlay 2 receivers for local sound cards, and a bridge that relays AirPlay 2 audio to Google Cast devices — into a single Home Assistant addon.

## What it does

- Scans your system's PulseAudio sinks and creates an [AirPlay 2](https://github.com/mikebrady/shairport-sync) receiver (via `shairport-sync`) for each one, so each physical output (a zone, a room, a card's channel pair) shows up as its own AirPlay 2 speaker.
- Scans your network for Google Cast devices (speakers, speaker groups/stereo pairs, Cast-enabled soundbars) and creates a matching AirPlay 2 receiver for each one too, relaying the audio through to Cast — so devices that don't natively speak AirPlay 2 still show up as AirPlay 2 speakers.
- Skips any Cast device that's already advertising its own, real AirPlay 2 receiver (some TVs and soundbars have this built in) — it's detected automatically, not configured by name, so you never end up with two entries for the same physical device.
- All AirPlay 2 zones — sink-backed and Cast-backed alike — support AirPlay 2's native multi-room grouping, so you can select several speakers at once from your source device.
- This addon doesn't limit you to just the zones it creates: any other AirPlay 2 receiver already on your network (a HomePod, a smart TV, a soundbar with AirPlay 2 built in, another addon's receivers) shows up in your source's AirPlay picker right alongside this addon's zones, and can be grouped together with them the same way, since AirPlay 2 multi-room isn't specific to this addon — it's a standard feature of the protocol itself.

## Requirements

- A Home Assistant host with PulseAudio already set up and your sound hardware's sinks configured (see **Setting up a new zone** below).
- `host_network: true` — this addon needs real access to your LAN for AirPlay 2 (mDNS/Bonjour) and Cast device discovery to work.
- Google Cast devices must be on the same network/VLAN as the Home Assistant host; discovery relies on mDNS, which typically doesn't cross VLANs without an mDNS reflector.

## Network interface

The `airplay_interface` add-on option defaults to `auto`. On startup the add-on chooses the eligible IPv4 default-route interface with the lowest metric, falling back to IPv6 when no eligible IPv4 default route exists. It then uses that same interface for Avahi/mDNS and every Shairport Sync receiver. Multiple active interfaces are normal; equal-priority default routes on different eligible interfaces are reported as ambiguous and require a manual choice. Loopback, common container interfaces, and tunnel/VPN interfaces are excluded.

To select an interface manually, set `airplay_interface` to its Linux interface name, for example `eth0` or `enp3s0`, then restart the add-on. The interface must exist in the add-on's host network namespace, be up, and have a LAN address. Startup logs and `/config/shairport-sync/logs/generate_airplay2_services.log` report the selected interface and whether it was automatic or manual. If selection fails, choose a suitable interface or correct the host's default route before restarting. Per-zone `.custom` files must not define `interface`; startup reports a clear error if one does because the add-on writes that setting itself.

## AirPlay receiver name

The `airplay_name` add-on option controls the name advertised to Apple devices. Leave it as `auto` to preserve the current per-zone names (derived from each sink or Cast device), or set it to a custom name such as `Living Room`, `USB DAC`, or `JCH Hi-Fi`. The value supports spaces and Unicode and must be non-empty, contain no line breaks/control characters, and fit within Shairport Sync's 50-character and mDNS's 63-byte limits. A plain custom name is used for every receiver, so it can make multiple zones indistinguishable in the picker. In a multi-zone setup, include `{name}` to retain each zone's unique current name, for example `Living Room - {name}`; `auto` also keeps the existing names distinct. Restart the add-on after changing the option. The name in the Shairport Sync configuration and its `-a` argument are updated together, so both AirPlay and RAOP mDNS records are refreshed when the receiver restarts. A rename can cause Apple devices or Music Assistant to treat a receiver as a new entry; the add-on's internal sink/Cast keys and saved port mappings remain unchanged. Per-zone `.custom` files must not define `name`; remove that setting if startup reports a conflict.

## Buffered AirPlay audio

The `airplay_decoded_buffer_seconds` option defaults to `5` seconds (allowed range: `1`–`10`). It sets Shairport Sync's decoded-audio buffer capacity for buffered AirPlay 2 audio, which can help tolerate bursty senders. Larger values use more memory. This setting does not affect realtime AirPlay streams or Cast pipe-zone buffering. Restart the add-on after changing it. A per-zone `.custom` file can override it by setting `audio_decoded_buffer_desired_length_in_seconds`; when present, that custom value takes precedence for that zone.

This add-on builds the upstream Shairport Sync `5.6` release with NQPTP `1.2.9`; both use Shared Memory Interface version `smi11`. Shairport Sync 5.6 includes fixes for OS 27 buffered audio handling and AirPlay 2 sessions interrupted by periodic connections from other players. The exact Shairport Sync and NQPTP build versions are written to `generate_airplay2_services.log` at startup.

## Setting up a new zone

**Sink-backed zones (local sound cards, USB DACs, HDMI, Bluetooth speakers, etc.):** this addon only creates an AirPlay 2 zone for a PulseAudio sink that's a `module-remap-sink` — and it names the zone after that remap sink's own name, so the remap sink's name *is* what shows up in your AirPlay picker. A raw hardware sink on its own (even a plain stereo card with nothing multi-channel about it) won't be picked up until it has a remap sink built on top of it — this applies even when the remap sink would just be a 1:1, all-channels passthrough of the card. A Bluetooth speaker works the same way once it's paired and PulseAudio shows it as a sink; see **Using a Bluetooth speaker as a zone** under **Advanced**.

The recommended way to create these remap sinks is with the **Multi-room Audio Controller** addon, rather than creating them by hand with `pactl`. This add-on monitors PulseAudio sink changes and adds, removes, or restarts the matching AirPlay 2 zone automatically. Beta also creates zones for standalone Bluetooth A2DP sinks; when a remap sink sits on a Bluetooth sink, the remap sink remains the zone.

**Cast-backed zones (Google/Nest speakers, Cast groups, Cast-enabled soundbars):** nothing to configure — every Cast device found on the network gets its own AirPlay 2 zone automatically, unless it's already got a native AirPlay 2 receiver of its own (see above).

## Restarting

Restart this addon whenever you:
- Add or remove a Google Cast device on your network.
- Change add-on options or per-zone configuration.

A restart re-scans the network and regenerates Cast receivers. PulseAudio-backed zones are monitored while the add-on runs. Zone names and their AirPlay 2 ports are persisted, so a zone's identity in your AirPlay picker stays stable across restarts.

If PulseAudio restarts or a configured sink disappears, the zone manager stops the affected Shairport Sync process and starts it again when PulseAudio and the exact sink are available. It does not switch the zone to another output sink. If the physical DAC or HDMI output remains silent while PulseAudio reports its sink as present, inspect the PulseAudio and Shairport Sync logs; restarting the add-on can reinitialize a hardware sink that PulseAudio has left stuck.

## Troubleshooting

Logs live under `/config/shairport-sync/logs/`:

- `generate_airplay2_services.log` records startup interface and option checks.
- `sink_zone_manager.log` records PulseAudio sink changes, Shairport Sync exits, and bounded restart attempts.
- Per-zone `*-ap2.log` files contain Shairport Sync's session, timing, and volume diagnostics.

`airplay_volume = -144.000000` is Shairport Sync reporting the AirPlay source's mute value; `software mute is enabled` alone does not show that the add-on forced mute. Correlate these lines with later source volume updates, `Play started`/`Play stopped`, and the PulseAudio sink input's `Corked`/`Mute` values. `buffered audio port closed by remote end` indicates that the remote side closed that transport; by itself it does not identify why. A transient NQPTP clock warning also needs timestamp correlation before treating it as the cause. During playback, use `pactl list sinks short` to confirm the configured sink is present and `pactl list sink-inputs` to inspect the Shairport Sync sink input and its target, mute, and cork state.

- `generate_airplay2_services.log` — what happened on the most recent sink scan/zone generation pass (which sinks were found, which ports were assigned, any sinks that had to be bounced/recreated).
- `<zone name>-ap2.log` — each zone's own `shairport-sync` (AirPlay 2 receiver) log.
- `cast_bridge_manager.log` — Cast device discovery and the overall status of each Cast Bridge zone's shairport-sync/relay process pair.
- `<zone name>-bridge.log` (under `/config/cast-bridge/logs/`) — a Cast-backed zone's own relay log (its connection to the Cast device, playback start/stop).

**A zone doesn't show up in your AirPlay picker:**
- Sink-backed: confirm it's a `module-remap-sink` sink (`pactl list sinks short`) and check `generate_airplay2_services.log` for its name.
- Cast-backed: check `cast_bridge_manager.log` for whether it was found on the last scan, and whether it was excluded as already having its own AirPlay 2 receiver.

**No sound from a specific zone, but it shows up fine:** check that zone's own log (`-ap2.log` for sink zones, `-bridge.log` for Cast zones) for connection errors, then restart the addon.

**A zone plays briefly then goes silent, or never recovers after a receiver/TV is power-cycled:** restart the addon — this forces PulseAudio to reopen the underlying hardware device.

## Known limitations

- The iOS Spotify app's own in-app device picker initially shows these zones as classic AirPlay-1-only, regardless of what they actually support. If you hit this, use iOS's system AirPlay picker instead (tap the AirPlay icon, or the status-bar clock/Dynamic Island while something is playing) to select the zone as full AirPlay 2 at least once — after that first play, Spotify's own picker seems to pick up on its real AirPlay 2 capability and shows it correctly from then on.
- A brand-new Cast device or a renamed/removed one is only picked up on the next addon restart — there's no live rescanning while it's running.
- Grouping a Cast-backed zone together with a sink-backed (true AirPlay 2) zone likely won't play in tight sync. Sink-backed zones stay tightly synced with each other because they share AirPlay 2's own timing protocol (PTP, via nqptp) directly. A Cast-backed zone's audio, by contrast, is relayed through to the Cast device rather than played by an AirPlay 2 receiver itself, so it picks up its own separate buffering/latency along the way — it should stay in sync with other Cast-backed zones reasonably well, but not with the sink-backed ones. If you're grouping across both kinds regularly, placing AP2 (sink-backed) speakers and Cast-backed speakers in different, separated areas of the house helps mask any drift between them.

## Advanced

### Custom `general` configuration per zone

Each sink-backed zone's `shairport-sync` configuration is regenerated on every start, so edits made directly to `<zone name>-ap2.conf` are overwritten. To keep per-zone settings across restarts and updates, use the zone's custom file instead. It's what makes per-device tweaks possible, such as the latency offset a Bluetooth speaker or a Cast device needs to stay in sync with your other rooms (for Bluetooth, see **Using a Bluetooth speaker as a zone** below):

```
/config/shairport-sync/config/<zone name>-ap2.custom
```

(On Home Assistant this folder is the addon's config share, `addon_configs/<addon slug>/shairport-sync/config/`.)

- The file is created automatically on the first start after the zone exists, containing only comments, so it does nothing until you add settings to it. It is never overwritten once it exists, and one file is created per zone.
- It is spliced into that zone's `general` block with libconfig's `@include`, so put only bare `general` settings in it, each ending with a semicolon. Don't add a `general = { ... }` wrapper, and don't use it for other groups such as `sessioncontrol` or `pulseaudio`.
- Don't set anything the addon already writes into `general`: `name`, `port`, `interface`, `output_backend`, `udp_port_base`, or `mdns_backend`. A key defined twice stops that zone's `shairport-sync` from starting. The decoded audio buffer is an exception: setting `audio_decoded_buffer_desired_length_in_seconds` in this file overrides the add-on option for that zone.
- A syntax error in the file also stops that one zone from starting. The error appears in `<zone name>-ap2.log`.
- Restart the addon after editing the file.

**Example: compensating for a Bluetooth speaker or a Cast device.** Both add delay of their own that AirPlay 2 doesn't know about, so they play late when grouped with your other rooms. To make one zone play 250 ms early, put this in that zone's `.custom` file and restart:

```
audio_backend_latency_offset_in_seconds = -0.25;
```

The value is in seconds, and a negative value makes the zone play earlier. Because each zone has its own file, the offset applies only to that zone.

### Using a Bluetooth speaker as a zone

A Bluetooth speaker can be an AirPlay 2 zone like any other output, as long as PulseAudio sees it as a sink and a remap sink has been built on top of it.

1. **Pair and connect the speaker on the host** with `bluetoothctl` (`pair`, `trust`, `connect`). `trust` lets the speaker reconnect on its own after a reboot or power cycle. Pairing keys are stored by BlueZ and survive reboots. See **Pairing walkthrough** below for the full steps.
2. **Confirm PulseAudio created a sink for it.** `pactl list sinks short | grep bluez` should show something like `bluez_output.AA_BB_CC_DD_EE_FF.1` (PipeWire-pulse) or `bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink` (PulseAudio). If the speaker connects but no sink appears, check that its card is on the `a2dp-sink` profile (`pactl list cards short`, then `pactl set-card-profile <card> a2dp-sink`).
3. **Create a remap sink on top of the Bluetooth sink** in the **Multi-room Audio Controller** addon, exactly as you would for a sound card (see **Setting up a new zone**).
4. **Restart this addon** with the speaker connected. The zone is built from the remap sink, so it's only generated if the Bluetooth sink and its remap sink exist at that moment.

#### Pairing walkthrough

Pairing happens on the Home Assistant host itself, in a shell on the machine that has the Bluetooth adapter. It is not done inside this addon. The host needs a working Bluetooth adapter with BlueZ running, and PulseAudio's Bluetooth module (`module-bluetooth-discover` and `module-bluez5-discover`, or the PipeWire equivalent). You can confirm the modules are loaded with `pactl list modules short | grep blue`.

**1. Open `bluetoothctl` and get the controller ready.**

```bash
bluetoothctl
```

At the `[bluetoothctl]>` prompt:

```
power on
agent on
default-agent
scan on
```

If the controller shows up as `Powered: yes` and `Pairable: yes`, you're ready. If `power on` fails, check `rfkill list` for a blocked adapter (`sudo rfkill unblock bluetooth`).

**2. Put the speaker in pairing mode.** This is usually a long press on its Bluetooth button until a light flashes. Make sure the speaker isn't currently connected to a phone or another device, because a connected speaker stops advertising and won't show up in the scan.

**3. Find the speaker's address.** Watch the scan output for a line like:

```
[NEW] Device AA:BB:CC:DD:EE:FF Speaker Name
```

The `AA:BB:CC:DD:EE:FF` part is the speaker's address. If the scan is noisy, run `devices` once it settles to list what has been seen. Pressing Tab after `pair ` completes known addresses.

**4. Pair, trust, and connect.**

```
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
scan off
exit
```

Some speakers ask for confirmation or a PIN during `pair`. Answer `yes` to a confirmation request, or try `0000` (or `1234`) for a PIN.

**5. Check the result.**

```bash
bluetoothctl info AA:BB:CC:DD:EE:FF | grep -E "Paired|Trusted|Connected"
pactl list sinks short | grep bluez
```

You want `Paired: yes`, `Trusted: yes`, `Connected: yes`, and a `bluez_output...` or `bluez_sink...` entry in the sink list. The sink name contains the speaker's address and stays the same on every connection.

**If something goes wrong:**

- **The speaker never appears in the scan:** re-trigger pairing mode (the speaker may time out after a minute or two), disconnect it from any other device, and run `scan on` again.
- **`org.bluez.Error.AuthenticationFailed`, or pairing times out:** run `remove AA:BB:CC:DD:EE:FF`, put the speaker back in pairing mode, and start over from `scan on`.
- **It pairs but `connect` fails (for example `br-connection-profile-unavailable`):** PulseAudio's Bluetooth modules probably aren't loaded or aren't talking to BlueZ. Check `pactl list modules short | grep blue` and `journalctl -u bluetooth -b`.
- **It connects but there's no sink:** the card may be on the wrong profile. Run `pactl list cards short | grep bluez`, then `pactl set-card-profile <card name> a2dp-sink` (older versions call it `a2dp_sink`).
- **The adapter's address changes after a replug or reboot:** BlueZ ties pairing keys to the adapter's address, so a different address means the speaker has to be paired again. Compare `bluetoothctl list` across a reboot to check. Some inexpensive USB adapters report a default address.

For more detail on `bluetoothctl` and BlueZ, the Arch Linux wiki's Bluetooth page (https://wiki.archlinux.org/title/Bluetooth) is a good general reference, and `man bluetoothctl` lists every command.

**What happens when the speaker disconnects:** many speakers drop the link after a few minutes of silence, or when powered off or out of range. The Bluetooth sink disappears, and the remap sink built on it goes with it. When the speaker reconnects, the Bluetooth sink returns but the remap sink is not recreated automatically, and the zone's receiver is left pointing at a sink that no longer exists. Reconnect the speaker, make sure its remap sink is back, and restart this addon.

**Latency and sync:** Bluetooth (A2DP) adds roughly 100–250 ms of delay that AirPlay 2 doesn't know about, so a Bluetooth zone plays late relative to the other rooms when grouped. Compensate for it with a negative latency offset in the zone's custom configuration (see **Custom `general` configuration per zone**, above). The delay depends on the codec (SBC vs AAC, for example) and can differ slightly between connections, so re-tune if the speaker changes codec or you notice drift after a reconnect.

To measure the delay, play a click track (a short, sharp click once per second) to the Bluetooth zone and a non-Bluetooth zone at the same time and record both with your phone, standing about equidistant from the two. Zoom in on one click in an audio editor such as Audacity and measure the gap between the two onsets. Use that gap, as a negative number of seconds, as your starting offset, then re-record and adjust until the clicks merge into one.

### Using and synchronizing the zones in Music Assistant

Every zone this addon creates shows up in Music Assistant (MA) as an AirPlay player. MA chooses how to stream to it by default, but you can pick the streaming mode yourself, and that choice is the key to getting reliable playback and good sync when you play to several zones at once.

**1. Show the advanced settings.** Open each of this addon's players in MA and turn on **Show advanced settings**. The streaming mode option stays hidden until you do, so repeat it for every player you want to configure.

**2. Set the streaming mode.** In the player's **Configure AirPlay** section, make sure **Enable this protocol on this player** is ticked, then choose a **Streaming mode**:

- **Automatic (recommended) [default]**: MA picks the mode itself.
- **AirPlay 2 - PTP timing**
- **AirPlay 2 - NTP timing**
- **AirPlay 2 - compatibility mode**
- **AirPlay 1 (RAOP)**

For these zones, set the players to **AirPlay 2 - compatibility mode**, or use **AirPlay 1 (RAOP)** instead. Test a mode by playing to the zone, and by playing to a group of zones, before settling on it.

**3. Play to one zone or several.** Select a single player in MA to play to one zone, or group several players and play to the group to hear them together. You can also group the zones from an iPhone, iPad, or Mac with AirPlay 2's own multi-room selection, as described at the top of this README.

MA remembers each player's settings, including the streaming mode. Because this addon keeps each zone's name and AirPlay 2 port stable across restarts (see **Restarting**), the zone comes back as the same player and keeps its settings. If you rename a zone, MA treats it as a new player and its streaming mode returns to **Automatic**, so set it again.
