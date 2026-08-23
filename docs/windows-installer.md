# Windows installer and release testing

The release installer is a single friend-ready `.exe`. It contains the Shazam for
VRC application, a private Python runtime, the Windows loopback-audio component,
FFmpeg, and FFprobe. A recipient does
not need Python, FFmpeg/FFprobe, or Inno Setup and does not need administrator access for the
default per-user installation.

## What the installer does

- shows a destination-folder page, defaulting to the current user's local Apps folder
- installs the complete standalone application without changing the system Python or PATH
- creates Start menu shortcuts and offers an optional desktop shortcut
- registers Shazam for VRC in Windows Installed apps
- places `unins000.exe` in the selected application folder
- supports installing a newer build over the existing app by using a stable app ID

Settings and history are stored in the user's local application-data area, not
the install folder. Uninstalling deliberately leaves that personal data in place
so an upgrade or reinstall does not erase the track log.

## One-time setup on the build PC

Use Windows 11 with Python 3.12 or newer. From the repository root, create the
development environment if it is not already present:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Install an FFmpeg distribution containing both `ffmpeg.exe` and `ffprobe.exe` and
make them available on `PATH`. The build also accepts explicit paths when the
tools are stored elsewhere:

```powershell
.\scripts\build_release.ps1 `
    -FFmpegPath "C:\path\to\ffmpeg.exe" `
    -FFprobePath "C:\path\to\ffprobe.exe" `
    -FFmpegLicensePath "C:\path\to\FFmpeg-LICENSE.txt"
```

Install Inno Setup 6 or 7 from its official download page:

<https://jrsoftware.org/isdl.php>

The scripts locate a standard Inno Setup installation automatically. A custom
compiler location can be supplied with `-InnoCompilerPath`.

## Build the file to send

Run the tests, then build the complete release:

```powershell
.venv\Scripts\python.exe -m pytest -q
.\scripts\build_release.ps1
```

The automatic-update release files are:

```text
dist\installer\Shazam-for-VRC-Setup-1.2.0.exe
dist\installer\Shazam-for-VRC-Setup-1.2.0.exe.sha256.txt
dist\installer\Shazam-for-VRC-update.json
dist\installer\Shazam-for-VRC-update.json.sig
dist\installer\GITHUB-RELEASE-NOTES-1.2.0.md
dist\avatar\com.szeb95.shazam-for-vrc-avatar-1.2.0.zip
dist\avatar\Shazam-for-VRC-Avatar-1.2.0.unitypackage
dist\avatar\Shazam-for-VRC-Avatar-1.2.0-SHA256.txt
dist\avatar\package.json
```

Send the setup `.exe`. Send the small checksum text file as well, preferably in a
separate message. The recipient can verify it in PowerShell:

```powershell
Get-FileHash ".\Shazam-for-VRC-Setup-1.2.0.exe" -Algorithm SHA256
```

The displayed hash should match the value in the `.sha256.txt` file. The setup
file is already compressed, so putting it in a ZIP is optional. A cloud-storage
link is usually more reliable than chat or email for a large executable.

The manifest and signature are required on the GitHub Release. The manifest is
signed with the private Ed25519 key stored at
`%LOCALAPPDATA%\Shazam for VRC Release Signing\update-private-key.pem`. Never
upload, commit, or send that private key. Back it up securely; losing it means
already-installed apps cannot trust installers signed with a replacement key.

## Automated local installer test

The test script installs into a uniquely named temporary folder, verifies the app
and bundled FFmpeg/FFprobe, starts the app briefly, runs the generated
uninstaller, and checks that the app was removed. It does not recognize a song
or alter VRChat.

```powershell
.\scripts\test_installer.ps1 `
    -SetupPath ".\dist\installer\Shazam-for-VRC-Setup-1.2.0.exe"
```

A passing run ends with `Installer test passed`. The temporary test folder is
removed even if a check fails. The normal per-user settings and history location
is intentionally not deleted. For safety, the script refuses to run when Shazam
for VRC is already installed because an isolated test shares the release's
Windows app identity. Uninstall the existing copy first or use Windows Sandbox.

## Manual release checklist

Test the final file, not only the unpacked app folder:

1. Run the installer and choose a different folder on the destination page.
2. Confirm the Start menu shortcut opens Shazam for VRC 1.2.0.
3. With VRChat playing supported media, use **Check current player** and then **Listen**.
4. Confirm a recognition result or an actionable no-match/error message appears.
5. With non-private test audio, enable **Use VRChat/computer audio for the third
   attempt**, force or wait for two clean no-matches, and confirm the final-attempt
   status appears. Disable the setting again if mixed-output capture is unwanted.
6. If used, test the F-key shortcut, SteamVR controller action, avatar OSC input,
   XSOverlay, and VRChat chatbox output.
7. Close the app and run `unins000.exe` from the install folder.
8. Confirm the application files and shortcuts are gone and Windows Installed apps no longer lists it.

## Publish a GitHub update

1. Update the version in `pyproject.toml`, `src\shazam_for_vrc\__init__.py`, and
   `packaging\windows\ShazamForVRC.iss`.
2. Add that version and every user-visible change at the top of
   `src\shazam_for_vrc\release_notes.py`. Tests reject mismatched versions.
3. Run all tests and `scripts\build_release.ps1`.
4. In `Szeb95/shazam-for-vrc-releases`, create a draft release with tag `v1.2.0`.
5. Paste `GITHUB-RELEASE-NOTES-1.2.0.md`; it is generated from the exact notes
   shown inside the app.
6. Upload the installer, checksum, update manifest, signature, four avatar package
   files, and avatar checksum listed above. The **Build Avatar Package** GitHub
   action can upload those four files to the draft when given tag `v1.2.0`.
7. Verify the filenames and publish the draft. Release immutability protects the
   published assets from later replacement.
8. Confirm the **Build VPM Listing** action succeeds and that
   `https://szeb95.github.io/shazam-for-vrc-releases/index.json` lists version
   `1.2.0`. GitHub Pages must use **GitHub Actions** as its source.

Version 1.1.0 must still be installed manually because older versions do not contain
the updater. Releases after 1.1.0 can be offered inside the application.

For the strongest check, repeat this in Windows Sandbox, a virtual machine, or a
separate Windows account that has no developer Python or FFmpeg installation.
That proves the release is using its bundled dependencies. A full VRChat and
SteamVR input test is best done on the real PC after the isolated install test.

## SmartScreen and signing

The installer is currently unsigned. Windows can therefore show **Unknown
publisher** or a Microsoft Defender SmartScreen warning even when the checksum is
correct. Do not tell recipients to disable antivirus protection. Explain that it
is your unsigned build and provide the checksum.

Avoid renaming or editing the setup file after generating its checksum. For a
more polished public release, obtain a Windows code-signing certificate and sign
both the app executable and final installer; that is optional for a small trusted
friends-only release.

## Uninstall options for recipients

Recipients can remove the app in any of these ways:

- Windows **Settings > Apps > Installed apps > Shazam for VRC > Uninstall**
- Start menu > **Shazam for VRC > Uninstall Shazam for VRC**
- run `unins000.exe` inside the selected install folder

If they also want to remove settings and history, they can delete the per-user
`Shazam for VRC` data folders afterward. This is intentionally separate from the
uninstaller to prevent accidental data loss.
