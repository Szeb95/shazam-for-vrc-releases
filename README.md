# Shazam for VRC

Identify music playing in a VRChat video player with a button press, without
recording the mixed VRChat output when a clean media stream is available. An
explicit fallback can use the audio currently playing through Windows as a final
attempt.

Current release: **1.2.0**.

## Status

The repository contains an on-demand VRChat log reader, player/stream detection,
temporary FFmpeg audio capture, bounded ShazamIO recognition, and a basic Windows
overlay for running the complete livestream workflow, including optional
XSOverlay VR notifications.

## Architecture

```text
VRChat output logs
        |
        v
media URL + player tracking
        |
        v
stream resolution (yt-dlp / direct HLS)
        |
        v
short clean audio sample (FFmpeg)
        |
        v
music recognition
        |
        +--> after two no-matches: optional Windows-output sample
        |
        +--> desktop / SteamVR notification
        +--> optional VRChat OSC output
        +--> local track history
```

Livestreams use the current stream edge. Prerecorded YouTube media now has an
automatic best-effort path: exact provider track metadata is preferred, short
song videos are sampled at representative sections, and long mixes use a clearly
labeled low-confidence position estimate derived from the AVPro open time. No
manual calibration is required.

An opt-in **VRChat/computer audio** fallback makes exactly two clean-stream
attempts and then records the default Windows output once. This third attempt can
identify the audible moment in a YouTube mix even when VRChat never logs its
playhead time. Because it records the complete output mix, it may also contain
voices, world sounds, notifications, and other applications. The WAV is temporary
and deleted immediately unless debug retention is separately enabled.

## Install the Windows release

Run `Shazam-for-VRC-Setup-1.2.0.exe`, choose an install folder, and finish the
wizard. The installer contains the Python runtime, FFmpeg, and FFprobe, so friends
do not need to install them separately. It creates Start menu shortcuts and can
optionally create a desktop shortcut.

Uninstall from Windows **Settings > Apps > Installed apps**, the Start menu
uninstall shortcut, or `unins000.exe` in the selected application folder.

The release is not code-signed, so Windows SmartScreen may show an unknown
publisher warning. Share the generated SHA-256 checksum alongside the installer
so recipients can verify that the file arrived unchanged.

See [docs/windows-installer.md](docs/windows-installer.md) for release building,
safe local testing, clean-machine testing, sharing, and uninstall details.

## Add the button to an avatar

The GitHub release includes `Shazam-for-VRC-Avatar-1.2.0.unitypackage`, and the
same prefab is available as the **Shazam for VRC Avatar Button** VCC package.
Install Modular Avatar, add the package to the avatar project, then drag
`Shazam for VRC Avatar Button.prefab` directly under the avatar root. It adds a
top-level **Listen for song** Expressions Menu Button and the local-only
`ShazamListen` Bool automatically.

The VCC repository URL is
`https://szeb95.github.io/shazam-for-vrc-releases/index.json`. See
[docs/avatar-setup.md](docs/avatar-setup.md) for VCC, Unity package, upload, and
VRChat OSC setup.

## Development setup

Requirements: Windows 11, Python 3.12+, and FFmpeg/FFprobe available on `PATH`.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
```

Run the overlay with:

```powershell
shazam-for-vrc
```

The dark desktop overlay uses left-side **Listen & Track Log**, **Settings**, and
**About** pages, plus a bottom-pinned **Updates & changes** page. It includes manual
listening and progress, a native SteamVR
controller action with selectable right-hand buttons and long/double-press
gestures, a configurable F1-F12 global shortcut, a VRChat avatar OSC
trigger, advanced outputs, portable history/debug paths, and opt-in debug audio
retention. The avatar button, SteamVR gesture, keyboard shortcut, and desktop
button all use the same recognition and optional final-fallback workflow.

The Listen page also has **Check current player**. Its separate window shows the
current media title or chapter, provider, detected content type, duration, and an
estimated minute/second position. It rereads the local VRChat log every two
seconds while open, but reuses provider metadata until the media changes.

Track-log rows can be filtered as **Tracks**, **Mixes**, **Live**, or **Error**, with
optional expandable details for time, world, friendly group-instance context,
instance access type, media provider, recognition source, and attempts. **Copy
track** copies the visible `Artist — Title`; **Copy whole list** copies the visible
non-error entries, and a persistent copied indicator can be disabled in Settings.
Unknown-position YouTube mixes are retained as whole **Mix** entries with an
explanatory hover icon when the optional computer-audio fallback is disabled or
also returns no track.
VRChat logs expose the group ID but not its display name, so the UI hides the raw
`grp_` value and offers **Open group** instead of storing VRChat account credentials.

The update page checks the public `Szeb95/shazam-for-vrc-releases` repository.
Every automatic installer must match an Ed25519 signature made with a private key
stored outside this repository. Update checks are enabled by default and can be
disabled; downloading and installation always require confirmation.

XSOverlay notifications are enabled by default and show **Start listening**, the
recognized `Artist — Title`, no-match results, and actionable listening errors in
VR. They use XSOverlay's local notification API and do not require a notification
relay. See [`docs/advanced-outputs.md`](docs/advanced-outputs.md).

An opt-in **VRChat chatbox track results** output can also post successful
source-labelled results such as `Twitch stream song: Title — Artist` or
`VRCDN stream song: Title — Artist` for live media. Prerecorded results use
`YouTube song: Title — Artist`, `Song in mix: Title — Artist`, or `Mix: Mix name`
according to playback context. It is disabled by default because other players
may see chatbox messages.

Touch/Quest and Index controllers default to right B, but Settings can generate
default bindings for right A, B, thumbstick click, or trigger click. SteamVR input
reconnects automatically if SteamVR starts later or restarts. See
[`docs/advanced-inputs.md`](docs/advanced-inputs.md) for setup and behavior.
The same settings panel receives one local avatar parameter such as
`/avatar/parameters/ShazamListen` so an Expressions Menu Button or Toggle starts
listening. It is ready by default on new installs, and an avatar Contact Receiver
can drive the same parameter.

Build the complete standalone app and installer with:

```powershell
.\scripts\build_release.ps1
```

The friend-ready file is written to
`dist\installer\Shazam-for-VRC-Setup-1.2.0.exe`, with a matching SHA-256 checksum,
signed update manifest, and signature. FFmpeg and FFprobe are mandatory at build time and are bundled with the
private Python runtime; none of these dependencies is installed system-wide on
the recipient's PC. The same build also writes the VCC zip, legacy `.unitypackage`,
release `package.json`, and checksums to `dist\avatar`.

See [docs/architecture.md](docs/architecture.md) for component boundaries and
the implementation roadmap. Part 02 behavior is documented in
[docs/stream-resolution.md](docs/stream-resolution.md), and Part 03 behavior is
documented in [docs/audio-capture.md](docs/audio-capture.md). Part 04 behavior,
Windows setup, and testing are documented in
[docs/shazam-recognition.md](docs/shazam-recognition.md). Advanced controller,
keyboard, and avatar OSC input are documented in
[docs/advanced-inputs.md](docs/advanced-inputs.md).
The drag-and-drop avatar prefab is documented in
[docs/avatar-setup.md](docs/avatar-setup.md).
XSOverlay notification output is documented in
[docs/advanced-outputs.md](docs/advanced-outputs.md).
Part 05 UI behavior and
packaging are documented in [docs/overlay.md](docs/overlay.md).
