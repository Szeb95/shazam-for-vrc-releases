"""Non-networked runtime checks for the packaged Windows application."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from shazam_for_vrc.application import _default_ffmpeg_executable
from shazam_for_vrc.recognition.shazam import _configure_media_tools, _load_legacy_audio
from shazam_for_vrc.updates import UpdateClient


def run_packaged_self_test() -> None:
    """Verify the packaged GUI, loopback audio, media tools, and decoder path."""

    _check_gui_runtime()
    _check_tray_runtime()
    _check_system_audio_runtime()
    _check_vrchat_audio_runtime()
    _check_openvr_runtime()
    _check_media_resolution_runtime()
    _check_recognition_runtime()
    UpdateClient()

    ffmpeg = _resolve_executable(_default_ffmpeg_executable())
    ffprobe = ffmpeg.with_name("ffprobe.exe")
    if not ffprobe.is_file():
        raise RuntimeError("Bundled FFprobe is missing.")

    _check_executable(ffmpeg)
    _check_executable(ffprobe)
    _check_ffmpeg_capabilities(ffmpeg)
    _configure_media_tools(str(ffmpeg))

    with tempfile.TemporaryDirectory(prefix="shazam-for-vrc-self-test-") as directory:
        sample_path = Path(directory) / "sample.wav"
        _write_silent_wav(sample_path)
        decoded = _load_legacy_audio(sample_path)
        if decoded.frame_rate <= 0 or decoded.channels <= 0 or len(decoded) <= 0:
            raise RuntimeError("ShazamIO could not decode the packaged self-test audio.")


def _check_gui_runtime() -> None:
    try:
        import tkinter
    except (ImportError, OSError) as error:
        raise RuntimeError("The packaged Tkinter GUI runtime is unavailable.") from error

    try:
        root = tkinter.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
    except (OSError, RuntimeError, tkinter.TclError) as error:
        raise RuntimeError("The packaged Tkinter GUI runtime is unavailable.") from error


def _check_system_audio_runtime() -> None:
    """Verify the optional Windows loopback dependency can load without recording."""

    try:
        import pyaudiowpatch
    except (ImportError, OSError) as error:
        raise RuntimeError(
            "The packaged Windows computer-audio component is unavailable."
        ) from error
    if not hasattr(pyaudiowpatch.PyAudio, "get_default_wasapi_loopback"):
        raise RuntimeError(
            "The packaged Windows computer-audio component does not support loopback."
        )


def _check_vrchat_audio_runtime() -> None:
    """Verify the process-loopback component can load without starting a recording."""

    try:
        from shazam_for_vrc.streams.vrchat_audio_capture import _load_backend

        capture_type = _load_backend().ProcessAudioCapture
    except (AttributeError, ImportError, OSError, RuntimeError) as error:
        raise RuntimeError(
            "The packaged VRChat-only audio component is unavailable."
        ) from error
    try:
        supported = capture_type.is_supported()
    except Exception as error:
        raise RuntimeError(
            "The packaged VRChat-only audio component could not initialize."
        ) from error
    if not supported:
        raise RuntimeError(
            "This Windows version does not support packaged VRChat-only audio capture."
        )


def _check_tray_runtime() -> None:
    """Verify the tray libraries and bundled icon without showing an icon."""

    try:
        from shazam_for_vrc.ui.tray import _load_backend, application_icon_path

        pystray, image_module = _load_backend()
        icon_path = application_icon_path()
        with image_module.open(icon_path) as source:
            source.verify()
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        raise RuntimeError("The packaged system-tray component is unavailable.") from error
    if not hasattr(pystray, "Icon") or not hasattr(pystray, "Menu"):
        raise RuntimeError("The packaged system-tray component is incomplete.")


def _check_openvr_runtime() -> None:
    """Verify OpenVR and its correct 64-bit Windows DLL without opening SteamVR."""

    try:
        import openvr
    except (ImportError, OSError) as error:
        raise RuntimeError("The packaged SteamVR component is unavailable.") from error
    required = ("VRApplication_Background", "VRActiveActionSet_t", "VRInput", "_openvr")
    if any(not hasattr(openvr, name) for name in required):
        raise RuntimeError("The packaged SteamVR component is incomplete.")


def _check_media_resolution_runtime() -> None:
    """Verify the two media extractors used by VRChat links without networking."""

    try:
        from yt_dlp import YoutubeDL

        resolver = YoutubeDL({"quiet": True, "no_warnings": True})
        extractors = (
            resolver.get_info_extractor("Youtube"),
            resolver.get_info_extractor("TwitchStream"),
        )
    except Exception as error:
        raise RuntimeError("The packaged media-link resolver is unavailable.") from error
    if any(extractor is None for extractor in extractors):
        raise RuntimeError("The packaged media-link resolver is incomplete.")


def _check_recognition_runtime() -> None:
    """Verify ShazamIO and its native fingerprint engine without sending audio."""

    try:
        from shazamio import Shazam

        client = Shazam(language="en-US", endpoint_country="GB")
    except Exception as error:
        raise RuntimeError("The packaged Shazam recognition component is unavailable.") from error
    if not callable(getattr(client, "recognize", None)) or not hasattr(
        client, "core_recognizer"
    ):
        raise RuntimeError("The packaged Shazam recognition component is incomplete.")


def _resolve_executable(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_file():
        return candidate.resolve()
    discovered = shutil.which(value)
    if discovered:
        return Path(discovered).resolve()
    raise RuntimeError(f"Required media tool is missing: {candidate.name}")


def _check_executable(path: Path) -> None:
    try:
        completed = subprocess.run(  # noqa: S603 - verified local executable, never a shell
            [str(path), "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Bundled media tool could not start: {path.name}") from error
    if completed.returncode != 0:
        raise RuntimeError(f"Bundled media tool failed its version check: {path.name}")


def _check_ffmpeg_capabilities(path: Path) -> None:
    """Reject reduced FFmpeg builds that cannot capture common VRChat media."""

    required = {
        "-protocols": ("http", "https", "tcp", "tls", "rtmp"),
        "-demuxers": (
            "hls",
            "dash",
            "flv",
            "matroska",
            "mov",
            "mpegts",
            "ogg",
            "rtsp",
            "wav",
        ),
        "-decoders": ("aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le"),
        "-encoders": ("pcm_s16le",),
        "-filters": ("aresample",),
    }
    for option, names in required.items():
        listing = _read_executable_listing(path, option).casefold()
        missing = [name for name in names if not _listing_contains(listing, name)]
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(f"Bundled FFmpeg is missing required {option[1:]}: {joined}")


def _read_executable_listing(path: Path, option: str) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - verified local executable, never a shell
            [str(path), "-hide_banner", option],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Bundled media tool could not report {option[1:]}.") from error
    if completed.returncode != 0:
        raise RuntimeError(f"Bundled media tool failed its {option[1:]} check.")
    return completed.stdout


def _listing_contains(listing: str, name: str) -> bool:
    """Match a capability name as a listing token, including comma aliases."""

    normalized = listing.replace(",", " ")
    return name.casefold() in normalized.split()


def _write_silent_wav(path: Path) -> None:
    sample_rate = 16_000
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * sample_rate)
