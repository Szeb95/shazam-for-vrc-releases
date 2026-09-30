"""Release notes displayed inside the application."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReleaseNotes:
    """One shipped version and its user-facing changes."""

    version: str
    title: str
    changes: tuple[str, ...]


RELEASE_NOTES = (
    ReleaseNotes(
        version="1.3.0",
        title="System tray, responsive overlay, and VRChat-only audio",
        changes=(
            "Added an optional Hide in system tray when minimized setting with Open and Exit "
            "tray actions; configured inputs and outputs stay active while hidden.",
            "Added the Shazam for VRC icon to the window, taskbar, system tray, and packaged "
            "executable.",
            "Reduced work during window resizing by coalescing repeated layout and scrolling "
            "updates.",
            "Kept track-row action buttons visible when the overlay becomes narrow; long "
            "track names are clipped first and remain available as a hover tooltip.",
            "Added a VRChat-only choice for the optional final local-audio attempt so audio "
            "from other programs is excluded.",
            "Kept Entire Windows output as a separate explicit choice for users who need the "
            "broader compatibility fallback.",
            "Preserved the whole-output source for existing 1.2 installations that had its "
            "computer-audio setting enabled; new configurations recommend VRChat only.",
            "Changed Always on top to off by default for new installations; existing saved "
            "preferences are preserved.",
            "Reduced the download and installed size by using the verified FFmpeg Essentials "
            "build and compiling dependencies without their raw source trees.",
            "Expanded the offline release self-test to verify Shazam, YouTube and Twitch "
            "resolution, SteamVR, tray support, both audio-capture modes, and required media "
            "formats before an installer is accepted.",
        ),
    ),
    ReleaseNotes(
        version="1.2.0",
        title="Avatar button and final audio fallback",
        changes=(
            "Added an optional third recognition attempt that records the current Windows "
            "output after two clean-stream attempts return no match.",
            "Unknown-position YouTube mixes can now use the current VRChat/computer audio "
            "instead of stopping at the unavailable player time.",
            "Computer-audio capture is explicit, warns that voices and other applications may "
            "be included, and deletes its temporary recording after recognition.",
            "Added a Modular Avatar prefab that installs a Listen for song Expressions Menu "
            "Button and the ShazamListen OSC parameter by dragging it onto an avatar.",
            "Added VCC package, Unity package, and GitHub release builds for the avatar button.",
            "Enabled the local VRChat avatar OSC listener by default for new installations so "
            "the prefab works without changing desktop settings.",
            "Kept the avatar parameter local-only and non-saved so it uses no network parameter "
            "budget and resets after every press.",
        ),
    ),
    ReleaseNotes(
        version="1.1.1",
        title="Avatar OSC and track-output fixes",
        changes=(
            "Added an optional VRChat avatar OSC input for Buttons, Toggles, and Contact "
            "Receivers.",
            "Added configurable avatar parameter and local receive-port settings with "
            "automatic port-conflict retrying.",
            "Avatar OSC starts listening only on an off-to-on change and cannot queue "
            "overlapping requests.",
            "Fixed VRChat chatbox labels for normal YouTube tracks, live streams, identified "
            "mix tracks, and unknown-position mixes; decorative quotation marks were removed.",
            "Copy whole list now matches the other Track Log controls, turns green after "
            "copying, and resets when the visible list changes.",
        ),
    ),
    ReleaseNotes(
        version="1.1.0",
        title="Listen and track-log improvements",
        changes=(
            "Added signed update checks and a bottom-pinned Updates & changes page.",
            "Added Track, Mix, Live, expandable-detail, and Error controls to the track log.",
            "Added clear-log and copy-whole-list actions; errors are never copied.",
            "Unknown-position YouTube mixes now appear as complete Mix entries and outputs.",
            "Copy track now copies Artist — Title exactly as shown in the track log.",
            "Copied track-log entries can show a persistent ✓ Copied indicator.",
            "Added settings for automatic update checks and copied indicators.",
            "Always on top is enabled by default for new installations.",
            "Raw grp_ identifiers are replaced by a friendly Group instance label and link.",
            "VRChat chatbox results now identify Twitch, YouTube, VRCDN, or world media.",
            "Mix information explains when VRChat does not expose player time.",
        ),
    ),
    ReleaseNotes(
        version="1.0.3",
        title="Reliable Windows interface runtime",
        changes=(
            "Bundled the complete Tkinter/Tcl/Tk runtime.",
            "Added packaged startup and media-tool self-tests.",
        ),
    ),
    ReleaseNotes(
        version="1.0.2",
        title="Invisible media helpers",
        changes=(
            "Stopped FFmpeg and FFprobe command windows from flashing while listening.",
        ),
    ),
    ReleaseNotes(
        version="1.0.1",
        title="Install fixes",
        changes=(
            "Bundled FFprobe for clean-PC recognition.",
            "Fixed mouse-wheel scrolling across complete pages and controls.",
            "Added clearer recognition and network error messages.",
        ),
    ),
)


def notes_for_version(version: str) -> ReleaseNotes | None:
    """Return the bundled notes for one exact version."""

    return next((notes for notes in RELEASE_NOTES if notes.version == version), None)
