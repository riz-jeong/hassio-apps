# Cast Adaptive Sample Rate Design

## Goal

Allow Cast relay zones to follow Shairport Sync's negotiated PCM sample rate
without making FFmpeg interpret raw PCM using a stale, fixed rate. Improve
reliability and audio quality while preserving the existing AirPlay 2, local
PulseAudio sink, receiver naming, interface selection, and multiroom behavior.

## Current behavior and confirmed limitation

Cast zones run one Shairport Sync process configured with the pipe backend and
one persistent Python FIFO reader. The FIFO contains S32_LE stereo PCM. The
manager configures Shairport Sync and FFmpeg with the same fixed 44.1 or 48 kHz
rate. Shairport Sync 5.6's pipe backend writes raw PCM and does not signal format
changes when automatic or multiple output rates are selected. Consequently,
setting only Shairport's `output_rate` to `auto` would leave FFmpeg's raw input
rate stale and can corrupt playback speed or pitch.

## Proposed architecture

Keep a single Shairport Sync process and the existing FIFO/HTTP/Cast topology.
Apply a small, version-pinned patch to Shairport Sync 5.6's pipe backend so its
pipe stream is framed. Each PCM block carries a compact header containing a
protocol magic/version, negotiated sample rate, sample format, channel count,
and payload length. A format change is therefore ordered in-band before the PCM
block that uses it; the receiver never tries to synchronize a separate control
channel with the audio FIFO.

Update the persistent Python FIFO reader to validate and decode frames, publish
format changes with their PCM data, and fail closed with a clear error on
malformed or unsupported frames. The Cast worker will initialize/reinitialize
its FFmpeg raw-PCM input from each frame's format. The encoded response remains
on the existing HTTP connection when the selected output format permits
continuous stream output. If a format transition cannot be represented safely
in the current Cast stream, end that response cleanly so the existing Cast
watchdog/reconnect path starts a fresh FFmpeg stream; do not restart Shairport
for ordinary track transitions.

Add `auto` to `cast_output_rate`. In auto mode, Shairport selects its output
rate and the framed protocol communicates each selection. Explicit 44100 and
48000 modes remain fixed and use the same framing protocol. Keep 48000 as the
default to preserve current behavior. Sink-backed PulseAudio zones keep their
existing `device`/`auto` behavior and are not moved to the Cast pipe protocol.

## Failure handling and diagnostics

- The protocol has an explicit version and strict bounds for rate, format,
  channels, and payload size.
- A reader encountering an invalid frame logs the zone and reason, closes the
  affected relay stream, and does not pass header bytes to FFmpeg as audio.
- FFmpeg startup, format transitions, and bounded recovery outcomes are logged
  at zone level without per-packet logging.
- Build failure to apply or compile the Shairport patch must fail the image
  build; there is no fallback to an unframed binary or silent fixed-rate mode.
- Explicit fixed-rate configuration remains available if a user encounters a
  Cast receiver that cannot handle a rate transition.

## Compatibility and scope

Only the consolidated `airplay_2_multiroom_audio` add-on is active. The removed
beta add-on is not reintroduced. Existing stable add-on config files, sink
customization, Cast receiver names, port assignments, PulseAudio routing,
network interface resolution, and multiroom behavior remain unchanged.
Shairport Sync stays at 5.6; the patch is tied to that pinned source version
and must be reviewed when upgrading Shairport Sync.

## Validation

Automated tests will cover frame serialization/parsing, format changes between
consecutive PCM frames, invalid/truncated/oversized frames, propagation of
`auto` and explicit rates into Shairport configuration, FFmpeg input arguments
per format, FIFO reconnect behavior, and preservation of current receiver
configuration. Validation will include Python tests, syntax and YAML checks,
patch applicability and compilation through the addon Docker build when the
required builder/network are available. Actual AirPlay sender and Cast hardware
track transitions must be reported separately from simulated tests.

## Risks

The Shairport patch adds a private internal protocol and must be maintained
against the pinned upstream source. Cast decoders may differ in their tolerance
for MP3 sample-rate changes within one HTTP response; if tests cannot establish
safe continuity, the implementation must close the HTTP stream at the format
boundary and rely on bounded Cast reconnection rather than send malformed or
misclocked audio. This feature cannot be claimed to improve YouTube Music
playback until tested with that client.
