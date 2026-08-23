# Shazam for VRC by Szeb

Shazam for VRC is a Windows overlay that identifies music playing through video
players inside VRChat.

When possible, it listens directly to the media stream instead of recording your
microphone or the mixed VRChat audio. This helps keep voices and world sounds out
of the recognition sample.

## Download

- [Download the latest Windows version](https://github.com/Szeb95/shazam-for-vrc-releases/releases/latest)
- [Add the avatar button with VRChat Creator Companion](https://szeb95.github.io/shazam-for-vrc-releases/)

For the Windows app, download `Shazam-for-VRC-Setup-x.x.x.exe` and follow the
setup wizard. Everything it needs is included.

For the avatar button, open the Creator Companion link above, add the package to
your avatar project, and drag the included prefab onto your avatar. It adds a
**Listen for song** button that triggers Shazam for VRC through OSC.

## Main features

- Identifies music playing through VRChat video players.
- Uses clean stream audio whenever a usable media URL is available.
- Includes a simple overlay, track history, keyboard and VR controller shortcuts.
- Supports the drag-and-drop VRChat avatar OSC button.
- Can show results through XSOverlay or the VRChat chatbox.
- Includes securely verified in-app updates.

## Windows security warning

The installer is not currently code-signed, so Windows may show an **Unknown
publisher** or **Windows protected your PC** warning. Only continue when the file
came from this official repository. A matching SHA-256 checksum is included with
every release if you want to verify the download.

## Privacy

Audio samples are temporary and deleted after recognition unless you explicitly
enable debug-audio retention. The app does not need or store your VRChat password
or authentication cookies.

## Requirements

- Windows 11
- VRChat in desktop or VR mode
- An internet connection

## Disclaimer

Shazam for VRC is an independent community project. It is not affiliated with or
endorsed by VRChat Inc., Shazam, or the supported media providers.
