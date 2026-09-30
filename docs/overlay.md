# Basic Overlay

## Purpose

Part 05 adds a small Windows desktop overlay around the existing clean-stream
pipeline. The UI stays separate from media detection, capture, and recognition so
future hotkeys, SteamVR controller actions, notifications, and OSC outputs can call
the same `ListeningService` without simulating a button click.

## Pages

Navigation is shown on the left in a dark Discord/ChatGPT-inspired layout.
The header includes a **Minimize** control. Minimizing leaves the application
running, so keyboard, SteamVR, and avatar OSC inputs and configured outputs remain
active. With **Hide in the system tray when minimized** enabled, the taskbar button
is hidden and the tray menu provides **Open Shazam for VRC** and **Exit**. Otherwise
the app remains as a normal taskbar icon. The packaged executable uses the Shazam
for VRC icon in the window, taskbar, and tray.

### Listen & Track Log

- **Listen** starts one on-demand recognition operation.
- **Check current player** opens a non-modal debug window with the world, best
  current item/title or chapter, provider, playback type, content classification,
  duration, AVPro open time, and estimated minute/second position.
- The status area reports player discovery, stream resolution, recording,
  recognition, fresh-sample retries, and completion.
- Successful matches, whole unknown-position mixes, live results, and failed
  attempts are stored in one newest-first track log. **Tracks**, **Mixes**, and
  **Live** are enabled by default; **Error** reveals failures and their pipeline
  stage. **Expandable details** shows timestamp, world, friendly group-instance
  context, instance access type, provider, recognition source, and attempts.
- **Copy track** copies the visible `Artist — Title` and can show a persistent
  **✓ Copied** indicator. **Open link** opens the public Twitch
  or YouTube source page when one is safe to retain, otherwise the Shazam track
  page when Shazam provided one.
- On narrow windows, the right-side actions remain visible and the track name is
  clipped first. Hovering the name shows its complete text.
- Raw `grp_` IDs are hidden. **Open group** opens VRChat's group page without the
  application storing VRChat credentials.
- **Clear log** removes all local entries after confirmation. **Copy whole list**
  copies the currently visible Track, Mix, and Live entries and never copies errors.
- When a YouTube mix has no usable playhead time, the complete mix is added with a
  circled information marker explaining why its current track cannot be identified.

The current-player window rereads the local VRChat log every two seconds only
while that window is open. It resolves provider metadata on the first read and
again only when the URL or AVPro opening changes. This makes URL/player changes
visible without repeatedly querying YouTube. Livestreams show “Live now.”
Prerecorded positions are labeled low confidence because VRChat logs do not expose
pause, seek, or world-sync updates. If elapsed time exceeds the reported duration,
the display explicitly says it is assuming a loop.

When no estimate is possible, the app says **Player time unavailable** and explains
that VRChat exposed the video URL but not the current playhead minute/second. It
also identifies the evidence needed for an estimate—an AVPro open timestamp or a
start time in the URL—rather than suggesting that more frequent log polling can
recover a time that the player never logged.

### Settings

- **Record seconds** accepts 3-60 seconds and defaults to 12.
- **Clean retries (when fallback is off)** accepts 0-5. Every retry captures a
  fresh clean-stream sample.
- **Use local audio for the final attempt** is off by default. When enabled, Listen
  uses two clean attempts and one selected local-audio attempt. **VRChat only** is
  recommended and excludes audio from other programs. **Entire Windows output**
  is the broader compatibility choice. Unknown-position mixes go directly to the
  selected source because their current clean-stream position is unavailable.
- The SteamVR button can use right A, B, thumbstick click, or trigger click.
- The controller gesture can be a configurable long press or double press.
- The Windows global shortcut can be changed from F1 through F12.
- One local VRChat avatar OSC parameter can trigger listening on an off-to-on change.
- XSOverlay and VRChat OSC chatbox outputs remain optional.
- Copied indicators and automatic startup update checks can be disabled.
- The track log and debug-recording folder can use custom absolute paths.
- **Always on top** controls the overlay behavior and is disabled by default for new
  installations. Existing saved preferences are kept.
- **Hide in the system tray when minimized** controls whether minimizing removes
  the taskbar button. It is off by default so the window remains easy to find.
- **Keep latest recording for debugging** copies the temporary sample to one local
  debug WAV. A later recording replaces it. The UI can play or delete that sample.

Changing history location switches the active history file. Existing history is
copied into a new file when the selected file does not exist; selecting an existing
history file loads its contents. Settings themselves stay in the normal per-user
AppData location.

### About

The in-app explanation describes the pipeline, privacy behavior, livestream scope,
and the dependency on Shazam's online service through ShazamIO. The app is not an
official Shazam or Apple product, and no-match or service failures remain possible.

### Updates & changes

This page is pinned to the bottom of the sidebar so future primary tabs can be
added above it. It shows the installed version and bundled changelog, checks the
public GitHub Releases page, and offers a newer installer only after validating a
private Ed25519 release signature. Downloads and installer launch require user
confirmation. The app never contains a GitHub password, token, or private signing
key.

## Output behavior

- **XSOverlay notifications** sends start, track/no-match, and error messages to
  XSOverlay on the local computer. It is enabled by default.
- **VRChat chatbox track results** sends only successful source-labelled results
  such as `Twitch stream song` or `VRCDN stream song` through local OSC. It is
  disabled by default because other players may see it.

ShazamIO may also use its existing bounded alternative-fingerprint fallback within
one recognition attempt. The overlay retry setting controls fresh recordings, not
that provider-internal fallback.

## Privacy and local data

Temporary capture audio is deleted when recognition finishes. Debug retention is
off by default. When enabled, only `last-sample.wav` is retained in the per-user
application data directory; disabling the setting and saving deletes it.

The local-audio fallback is a separate opt-in. VRChat-only capture may include
voices, world sounds, and media inside VRChat but excludes other applications. The
complete Windows-output choice may additionally capture notifications and other
applications. Both follow the same temporary deletion rule; enabling a fallback
does not automatically enable debug retention, and the app never silently changes
from VRChat-only to whole-output recording.

Settings use a standard per-user location supplied by `platformdirs`. History may
use the default location or the custom JSON path selected by the user. It stores
track metadata, world context, provider, and only a safe public provider/Shazam
link. Signed direct stream URLs, VRChat logs, and raw Shazam responses are not saved.
Copied timestamps are stored only in the history file. When automatic update checks
are enabled, GitHub receives a normal HTTPS request and can observe its IP and time.

## Running from source

After installing the development environment, start the GUI with:

```powershell
shazam-for-vrc
```

VRChat must be running in a world whose current media URL is present in its latest
output log. Livestream capture stays exact. Prerecorded YouTube support uses exact
track metadata where available, representative sections for short song videos,
and the explicit low-confidence estimate for longer media.

## Building the Windows executable

The build produces a windowed one-folder application. FFmpeg and FFprobe are
required on the build machine and are included with the application's private
Python runtime. The build stops instead of creating an incomplete release when
either tool or the FFmpeg license file cannot be found.

```powershell
.\scripts\build_exe.ps1
```

The executable is written to:

```text
dist\Shazam for VRC\Shazam for VRC.exe
```

The surrounding folder is part of the application and must be distributed with
the executable. Normally, distribute the installer instead of this folder:

```powershell
.\scripts\build_release.ps1
```

The installer is written to `dist\installer`, lets the recipient select the
destination folder, and creates its uninstall executable inside the installed
folder. It also creates a signed update manifest using the private key stored outside
the repository. Python, FFmpeg, and FFprobe are bundled and are not installed system-wide. See
[`windows-installer.md`](windows-installer.md) for the release and test workflow.

The build also includes the OpenVR runtime binding and the SteamVR action/default
binding JSON files required by the controller listener.

XSOverlay notification behavior and troubleshooting are documented in
[`advanced-outputs.md`](advanced-outputs.md).
