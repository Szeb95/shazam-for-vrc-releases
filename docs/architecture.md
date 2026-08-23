# Architecture

## Objective

Identify the track currently playing in a VRChat media player while excluding
speech, avatar sounds, and other mixed VRChat audio.

## Processing pipeline

1. `vrchat.log_reader` follows the current VRChat `output_log` without modifying it.
2. `vrchat.player_tracker` records media events for the current world and chooses
   the most likely active player.
3. `streams.stream_detector` classifies the selected URL as live, prerecorded, or
   unsupported.
4. `streams.resolver` resolves pages such as Twitch or YouTube to a playable audio
   stream; direct HLS URLs can pass through.
5. `streams.audio_capture` asks FFmpeg for a short, temporary audio-only sample.
6. If two clean samples return no match and the user enabled it,
   `streams.system_audio_capture` records one temporary sample from the default
   Windows output through WASAPI loopback.
7. `recognition.shazam` fingerprints the temporary sample through ShazamIO and
   returns a normalized match or no-match result.
8. Output adapters show the result and may persist metadata in local history.

Core modules must not import `ui`. Inputs trigger an application service; they do
not directly invoke FFmpeg or Shazam. Output adapters receive normalized results
and do not know how recognition was performed.

URL classification distinguishes transport from playback type. In particular,
HLS is not automatically considered live. Provider metadata confirms whether
independent capture represents the current playback moment. See
[`stream-resolution.md`](stream-resolution.md) for the part 02 interfaces and
fallback behavior.

## Scope by release

### Version 1: core recognition

- Follow VRChat logs and extract media URLs.
- Track recent URLs within the current world/session.
- Support direct HLS plus URLs resolvable by yt-dlp.
- Capture roughly 5-10 seconds of stream audio with FFmpeg.
- Recognize the sample and display a desktop result.
- Provide a keyboard hotkey first, with clear errors and safe cleanup.
- Use exact provider track metadata or automatic seek strategies for prerecorded
  YouTube media without manual calibration.

### Version 2: player intelligence

- Improve selection when a world has multiple video players.
- Estimate prerecorded position from AVPro log timing (implemented, low confidence).
- Replace the estimate with exact player time if VRChat exposes a stable signal in
  a future release.
- Add configurable retries, history, and provider fallbacks.

### Version 3: VR integration and distribution

- SteamVR controller input and XSOverlay VR notification output (implemented).
- Optional VRChat chatbox OSC track output (implemented).
- Settings UI, startup integration, and a Windows package.
- Explicit, optional mixed-output capture after two clean no-matches, or when an
  unknown-position mix cannot be sampled at its audible moment (implemented).

## Privacy and storage

VRChat logs are read locally. Audio samples are temporary and should be deleted
immediately after recognition. System-output capture is disabled by default
because it can include voices, world sounds, notifications, and other programs.
Logs, audio, secrets, and local configuration are excluded from version control.

See [`shazam-recognition.md`](shazam-recognition.md) for the part 04 provider
boundary, retry and timeout policy, Windows setup, and test procedures.
