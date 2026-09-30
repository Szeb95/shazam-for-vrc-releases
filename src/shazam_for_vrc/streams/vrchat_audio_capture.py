"""Explicit Windows process-loopback capture for VRChat-only audio."""

from __future__ import annotations

import math
import struct
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from shazam_for_vrc.streams.audio_capture import AudioSample
from shazam_for_vrc.streams.system_audio_capture import SystemAudioCaptureError

DEFAULT_DURATION_SECONDS = 12.0
MINIMUM_WAVE_BYTES = 45
MINIMUM_AUDIBLE_DB = -55.0
VRCHAT_PROCESS_NAMES = frozenset({"vrchat.exe"})


class FallbackAudioSource(StrEnum):
    """User-selectable source for the explicit final local-audio attempt."""

    VRCHAT = "vrchat"
    WINDOWS_OUTPUT = "windows_output"


class VRChatAudioCaptureError(SystemAudioCaptureError):
    """Base error for failures while recording only the VRChat process tree."""


class VRChatAudioUnavailableError(VRChatAudioCaptureError):
    """Raised when process-loopback capture cannot target a running VRChat session."""


class SilentVRChatAudioError(VRChatAudioCaptureError):
    """Raised when VRChat process capture produces no audible sample."""


class _AudioProcess(Protocol):
    pid: int
    name: str


BackendLoader = Callable[[], Any]
Sleeper = Callable[[float], None]


@contextmanager
def capture_vrchat_audio(
    *,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    backend_loader: BackendLoader | None = None,
    sleeper: Sleeper = time.sleep,
) -> Iterator[AudioSample]:
    """Record only audio rendered by VRChat and its child processes."""

    duration = _capture_duration(duration_seconds)
    backend = (backend_loader or _load_backend)()
    capture_type = backend.ProcessAudioCapture
    if not capture_type.is_supported():
        raise VRChatAudioUnavailableError(
            "This Windows version does not support VRChat-only audio capture. Choose Entire "
            "Windows output in Settings if you accept capturing the complete output mix."
        )

    try:
        processes = capture_type.enumerate_audio_processes()
    except Exception:
        raise VRChatAudioUnavailableError(
            "Windows could not inspect the active audio sessions. Restart VRChat, or choose "
            "Entire Windows output in Settings."
        ) from None
    process = _select_vrchat_process(processes)
    if process is None:
        raise VRChatAudioUnavailableError(
            "No VRChat audio session was found. Start VRChat and make sure it is producing "
            "sound, or choose Entire Windows output in Settings."
        )

    levels: list[float] = []
    with tempfile.TemporaryDirectory(prefix="shazam-for-vrc-process-audio-") as directory:
        output_path = Path(directory) / "vrchat-only.wav"
        capture = capture_type(
            pid=process.pid,
            output_path=str(output_path),
            level_callback=lambda level: levels.append(float(level)),
        )
        started = False
        operation_failed = False
        try:
            capture.start()
            started = True
            sleeper(duration)
        except Exception:
            operation_failed = True
            raise VRChatAudioCaptureError(
                "Windows could not record the VRChat audio session. Make sure VRChat is "
                "audible, or choose Entire Windows output in Settings."
            ) from None
        finally:
            if started:
                try:
                    capture.stop()
                except Exception:
                    if not operation_failed:
                        raise VRChatAudioCaptureError(
                            "Windows could not finish the VRChat-only recording."
                        ) from None

        sample_rate, channel_count, data_bytes = _read_wave_summary(output_path)
        if data_bytes <= 0 or (levels and max(levels) < MINIMUM_AUDIBLE_DB):
            raise SilentVRChatAudioError(
                "The VRChat-only attempt recorded silence. Make sure the world player is "
                "audible in VRChat, then try again."
            )
        try:
            file_size = output_path.stat().st_size
        except OSError:
            raise VRChatAudioCaptureError(
                "The VRChat-only attempt did not produce a usable recording."
            ) from None
        yield AudioSample(
            path=output_path,
            requested_duration_seconds=duration,
            sample_rate=sample_rate,
            channel_count=channel_count,
            file_size_bytes=file_size,
        )


def _load_backend() -> Any:
    if sys.platform != "win32":
        raise VRChatAudioUnavailableError(
            "VRChat-only audio capture is available only on Windows."
        )
    try:
        import process_audio_capture
    except (ImportError, OSError) as error:
        raise VRChatAudioUnavailableError(
            "The VRChat-only audio component is unavailable. Reinstall Shazam for VRC "
            "1.3.0 or newer."
        ) from error
    return process_audio_capture


def _select_vrchat_process(processes: Sequence[_AudioProcess]) -> _AudioProcess | None:
    matches = [
        process
        for process in processes
        if Path(str(process.name)).name.casefold() in VRCHAT_PROCESS_NAMES
    ]
    return min(matches, key=lambda process: int(process.pid), default=None)


def _read_wave_summary(path: Path) -> tuple[int, int, int]:
    try:
        raw = path.read_bytes()
    except OSError:
        raise VRChatAudioCaptureError(
            "The VRChat-only attempt did not produce a usable recording."
        ) from None
    if len(raw) < MINIMUM_WAVE_BYTES or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise VRChatAudioCaptureError(
            "The VRChat-only attempt produced an invalid audio file."
        )

    sample_rate: int | None = None
    channel_count: int | None = None
    data_bytes = 0
    offset = 12
    while offset + 8 <= len(raw):
        chunk_id = raw[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", raw, offset + 4)[0]
        chunk_start = offset + 8
        chunk_end = min(len(raw), chunk_start + chunk_size)
        if chunk_id == b"fmt " and chunk_end - chunk_start >= 16:
            _format, channel_count, sample_rate = struct.unpack_from(
                "<HHI",
                raw,
                chunk_start,
            )
        elif chunk_id == b"data":
            data_bytes += max(0, chunk_end - chunk_start)
        offset = chunk_start + chunk_size + (chunk_size % 2)

    if not sample_rate or not channel_count:
        raise VRChatAudioCaptureError(
            "The VRChat-only attempt produced an audio file without a usable format."
        )
    return sample_rate, channel_count, data_bytes


def _capture_duration(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("duration_seconds must be a finite, positive number")
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration_seconds must be a finite, positive number")
    return duration
