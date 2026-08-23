# Shazam for VRC by Szeb

Shazam for VRC is a Windows overlay that identifies music playing through video players inside VRChat.

When possible, it captures a short sample directly from the media stream instead of recording your microphone or the mixed VRChat audio. This helps prevent voices and world sounds from interfering with recognition.

[Download the latest version](https://github.com/sebihenk/shazam-for-vrc-releases/releases/latest)

## Installation

1. Open the latest release using the link above.
2. Download the file named `Shazam-for-VRC-Setup-x.x.x.exe`.
3. Optionally download the matching `.sha256.txt` file to verify the installer.
4. Open the installer and follow the setup wizard.

### Windows security warning

The installer is currently **not code-signed**, so Windows may display a **Windows protected your PC** or **Unknown publisher** warning.

Only continue if you downloaded the installer from this official repository:

1. Select **More info**.
2. Confirm that the file is the expected Shazam for VRC installer.
3. Select **Run anyway**.

The warning does not automatically mean the program is malicious. It appears because the installer does not currently have a paid Windows code-signing certificate.

You can verify the download in PowerShell:

```powershell
Get-FileHash ".\Shazam-for-VRC-Setup-x.x.x.exe" -Algorithm SHA256
```

Compare the displayed hash with the value inside the matching `.sha256.txt` file from the release.

## Main features

- Identifies music playing through VRChat video players.
- Uses clean stream audio when a usable media URL is available.
- Simple Windows overlay with manual listening and progress status.
- Track history containing the artist, title, time, world and media provider.
- Copy buttons for quickly copying `Artist — Title`.
- Supports livestreams, regular videos and long music mixes.
- Configurable keyboard and SteamVR controller shortcuts.
- Optional XSOverlay notifications inside VR.
- Cryptographically verified in-app updates.

## Other features

- Configurable recording duration and retry attempts.
- Global F1–F12 keyboard shortcuts.
- Long-press and double-press input options.
- SteamVR controller buttons for Touch, Quest and Index controllers.
- Optional VRChat avatar OSC input.
- Optional VRChat chatbox output.
- Provider labels for Twitch, YouTube, VRCDN and world media.
- Track, mix, livestream and error filters.
- Persistent indicator showing which tracks have already been copied.
- Configurable history and debug-data locations.
- Optional retention of the last recorded audio sample for debugging.
- Current-player information and estimated playback position.
- Always-on-top overlay enabled by default.
- Settings and recognition history are preserved during updates.
- Bundled Python, FFmpeg and FFprobe—no separate dependencies are required.

## Privacy

Audio samples are temporary and are deleted after recognition unless debug-audio retention is explicitly enabled.

The program does not need or store your VRChat password or authentication cookies. 

## Recognition limitations

Music recognition uses a Shazam-compatible recognition service. Recognition may occasionally fail or return an incorrect result because of:

- Very short or quiet samples
- Talking or sound effects inside the source video
- Modified, remixed or unreleased music
- Streams that cannot be accessed directly
- Temporary provider or network problems

## Updating

The program checks this public repository for new releases by default. Update installers are verified using a signature built into the application before they can be opened.

Automatic checks can be disabled in Settings. Downloading and installing an update always requires confirmation.

## Requirements

- Windows 11
- VRChat desktop or VR mode
- An internet connection lol
- A supported media URL visible in the local VRChat logs

## Disclaimer

Shazam for VRC is an independent community project. It is not affiliated with or endorsed by VRChat Inc., Shazam or the supported media providers.
