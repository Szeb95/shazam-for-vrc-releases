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
    _check_system_audio_runtime()
    UpdateClient()

    ffmpeg = _resolve_executable(_default_ffmpeg_executable())
    ffprobe = ffmpeg.with_name("ffprobe.exe")
    if not ffprobe.is_file():
        raise RuntimeError("Bundled FFprobe is missing.")

    _check_executable(ffmpeg)
    _check_executable(ffprobe)
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


def _write_silent_wav(path: Path) -> None:
    sample_rate = 16_000
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * sample_rate)
