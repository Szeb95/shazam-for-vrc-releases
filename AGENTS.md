# Shazam for VRC

## Goal

Detect media played inside VRChat and identify the current song on demand. Prefer
capturing clean audio from the player stream so voices and world audio do not
interfere with recognition.

## Primary workflow

VRChat logs -> active player detection -> stream URL resolution -> short FFmpeg
audio sample -> Shazam recognition -> notification.

If no usable player URL exists, local mixed-audio capture may be offered later as
an explicit fallback.

## Development rules

- Windows 11 is the primary platform.
- Use Python 3.12 or newer.
- Keep subsystems modular and communicate through typed interfaces.
- VRChat log parsing must not depend on recognition, UI, or output code.
- Stream resolution and capture must not depend on the GUI.
- Do not hardcode user-specific paths; use `pathlib` and platform APIs.
- Never retain captured audio longer than needed unless the user opts in.
- Never commit VRChat logs, captured audio, secrets, or local configuration.
- Log actionable errors instead of silently ignoring failures.
- Add tests for parsers, URL classification, player selection, and configuration.
- Prefer small, focused changes and run the relevant tests before handoff.
- Keep **Always on top** disabled by default for new installations; users may enable it in
  Settings when they want it.

## Package ownership

- `vrchat/`: log discovery, log parsing, world/session state, player tracking.
- `streams/`: URL classification/resolution and FFmpeg audio capture.
- `recognition/`: recognition providers and normalized track results.
- `input/`: hotkey and later SteamVR button triggers.
- `output/`: desktop/VR notifications, OSC, and recognition history.
- `ui/`: settings and application UI only; no core business logic.

## Planned delivery stages

1. Livestream recognition from URLs found in VRChat logs.
2. Better active-player selection and prerecorded media positioning.
3. SteamVR input, VR output, UI, packaging, and optional audio fallback.
