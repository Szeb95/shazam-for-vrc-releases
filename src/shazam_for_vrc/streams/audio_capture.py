"""Capture short-lived, Shazam-compatible audio samples through FFmpeg."""

from __future__ import annotations

import logging
import math
import subprocess
import tempfile
import wave
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from shazam_for_vrc.streams.resolver import ResolvedStream
from shazam_for_vrc.streams.stream_detector import Transport

logger = logging.getLogger(__name__)

DEFAULT_DURATION_SECONDS = 8.0
DEFAULT_STARTUP_ALLOWANCE_SECONDS = 15.0
SAMPLE_RATE = 44_100
CHANNEL_COUNT = 1
SAMPLE_WIDTH_BYTES = 2
_PROCESS_STOP_GRACE_SECONDS = 2.0


class AudioCaptureError(RuntimeError):
    """Base error for failures while capturing a temporary audio sample."""


class FFmpegNotFoundError(AudioCaptureError):
    """Raised when the configured FFmpeg executable cannot be started."""


class InvalidCaptureDurationError(AudioCaptureError):
    """Raised when the requested sample duration is not finite and positive."""


class InvalidCaptureTimeoutError(AudioCaptureError):
    """Raised when the total capture timeout is not finite and positive."""


class InvalidCaptureStartError(AudioCaptureError):
    """Raised when a requested media seek position is invalid."""


class CaptureTimeoutError(AudioCaptureError):
    """Raised when FFmpeg does not finish within the total timeout."""


class FFmpegCaptureError(AudioCaptureError):
    """Raised when FFmpeg cannot start or exits unsuccessfully."""


class NoAudioStreamError(FFmpegCaptureError):
    """Raised when FFmpeg reports that the input has no audio stream."""


class AudioOutputError(AudioCaptureError):
    """Base error for missing or unusable captured audio."""


class EmptyAudioOutputError(AudioOutputError):
    """Raised when FFmpeg produces no file, no bytes, or no audio frames."""


class InvalidAudioOutputError(AudioOutputError):
    """Raised when FFmpeg produces an unreadable or unexpected WAV format."""


class UnsupportedPlaybackError(AudioCaptureError):
    """Raised when capture cannot represent the current VRChat playback moment."""


class UnsafeHttpHeadersError(AudioCaptureError):
    """Raised when an HTTP header could inject another FFmpeg header or argument."""


@dataclass(frozen=True, slots=True)
class AudioSample:
    """Metadata for a temporary WAV that exists only inside its capture context."""

    path: Path
    requested_duration_seconds: float
    sample_rate: int
    channel_count: int
    file_size_bytes: int


@contextmanager
def capture_audio(
    stream: ResolvedStream,
    *,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    timeout_seconds: float | None = None,
    ffmpeg_executable: str = "ffmpeg",
    allow_unsupported_playback: bool = False,
    start_seconds: float | None = None,
) -> Iterator[AudioSample]:
    """Capture a temporary WAV from an already-resolved media stream.

    The returned path is valid only while the context is active. By default,
    capture is limited to streams known to represent the current VRChat moment.
    ``allow_unsupported_playback`` is an explicit experimental escape hatch for
    diagnostics; callers must not assume such a sample matches current playback.
    """
    duration = _positive_number(
        duration_seconds,
        InvalidCaptureDurationError,
        "The audio capture duration must be a finite, positive number of seconds.",
    )
    timeout = _capture_timeout(duration, timeout_seconds)
    start = _capture_start(start_seconds)
    if not stream.supports_current_audio and not allow_unsupported_playback:
        raise UnsupportedPlaybackError(
            "Audio capture was refused because the stream is not known to represent "
            "the current VRChat playback moment."
        )

    serialized_headers = _serialize_http_headers(stream.http_headers)
    with tempfile.TemporaryDirectory(prefix="shazam-for-vrc-") as temporary_directory:
        output_path = Path(temporary_directory) / "sample.wav"
        command = _ffmpeg_command(
            stream,
            output_path=output_path,
            duration_seconds=duration,
            ffmpeg_executable=ffmpeg_executable,
            serialized_headers=serialized_headers,
            start_seconds=start,
        )
        _run_ffmpeg(command, timeout_seconds=timeout)
        sample = _validate_wav(output_path, requested_duration_seconds=duration)
        logger.debug(
            "Captured a temporary audio sample (%d bytes, %.3f requested seconds).",
            sample.file_size_bytes,
            sample.requested_duration_seconds,
        )
        yield sample


def _capture_timeout(duration_seconds: float, timeout_seconds: float | None) -> float:
    if timeout_seconds is None:
        return duration_seconds + DEFAULT_STARTUP_ALLOWANCE_SECONDS
    return _positive_number(
        timeout_seconds,
        InvalidCaptureTimeoutError,
        "The audio capture timeout must be a finite, positive number of seconds.",
    )


def _capture_start(start_seconds: float | None) -> float | None:
    if start_seconds is None:
        return None
    if isinstance(start_seconds, bool) or not isinstance(start_seconds, (int, float)):
        raise InvalidCaptureStartError(
            "The audio capture start must be a finite, non-negative number of seconds."
        )
    normalized = float(start_seconds)
    if not math.isfinite(normalized) or normalized < 0:
        raise InvalidCaptureStartError(
            "The audio capture start must be a finite, non-negative number of seconds."
        )
    return normalized


def _positive_number(
    value: float,
    error_type: type[AudioCaptureError],
    message: str,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error_type(message)
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise error_type(message)
    return normalized


def _serialize_http_headers(headers: Mapping[str, str]) -> str | None:
    lines: list[str] = []
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or "\r" in name
            or "\n" in name
            or "\r" in value
            or "\n" in value
        ):
            raise UnsafeHttpHeadersError(
                "A media HTTP header is invalid or contains a prohibited line break."
            )
        lines.append(f"{name}: {value}\r\n")
    return "".join(lines) or None


def _ffmpeg_command(
    stream: ResolvedStream,
    *,
    output_path: Path,
    duration_seconds: float,
    ffmpeg_executable: str,
    serialized_headers: str | None,
    start_seconds: float | None,
) -> list[str]:
    command = [
        ffmpeg_executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
    ]
    if stream.transport is Transport.RTSP and stream.rtsp_transport == "tcp":
        command.extend(["-rtsp_transport", "tcp"])
    if serialized_headers is not None:
        command.extend(["-headers", serialized_headers])
    if start_seconds is not None:
        command.extend(["-ss", _format_seconds(start_seconds)])
    command.extend(
        [
            "-i",
            stream.stream_url,
            "-map",
            "0:a:0",
            "-vn",
            "-t",
            _format_seconds(duration_seconds),
            "-c:a",
            "pcm_s16le",
            "-ac",
            str(CHANNEL_COUNT),
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "wav",
            str(output_path),
        ]
    )
    return command


def _format_seconds(value: float) -> str:
    return format(value, ".15g")


def _run_ffmpeg(command: list[str], *, timeout_seconds: float) -> None:
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed executable plus argument list, never a shell
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise FFmpegNotFoundError(
            "FFmpeg was not found. Install FFmpeg and make sure it is available on PATH."
        ) from None
    except OSError:
        raise FFmpegCaptureError("FFmpeg could not be started for audio capture.") from None

    try:
        _stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _stop_process(process)
        raise CaptureTimeoutError(
            "FFmpeg audio capture exceeded its total timeout and was stopped."
        ) from None
    except OSError:
        _stop_process(process)
        raise FFmpegCaptureError("FFmpeg failed while capturing the audio sample.") from None
    except BaseException:
        _stop_process(process)
        raise

    if process.returncode != 0:
        if _reports_missing_audio(stderr):
            raise NoAudioStreamError("The resolved media input does not contain an audio stream.")
        raise FFmpegCaptureError("FFmpeg could not capture audio from the resolved media stream.")


def _stop_process(process: subprocess.Popen[str]) -> None:
    with suppress(OSError):
        process.terminate()
    try:
        process.communicate(timeout=_PROCESS_STOP_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        return
    with suppress(OSError):
        process.kill()
    with suppress(OSError):
        process.communicate()


def _reports_missing_audio(stderr: str | None) -> bool:
    normalized = (stderr or "").casefold()
    return any(
        marker in normalized
        for marker in (
            "matches no streams",
            "does not contain any stream",
            "does not contain an audio stream",
            "no audio stream",
            "no such stream",
        )
    )


def _validate_wav(path: Path, *, requested_duration_seconds: float) -> AudioSample:
    try:
        file_size = path.stat().st_size
    except (FileNotFoundError, OSError):
        raise EmptyAudioOutputError("FFmpeg did not produce an audio sample.") from None
    if file_size <= 0:
        raise EmptyAudioOutputError("FFmpeg produced an empty audio sample.")

    try:
        with wave.open(str(path), "rb") as wav_file:
            channel_count = wav_file.getnchannels()
            sample_rate = wav_file.getframerate()
            sample_width = wav_file.getsampwidth()
            compression_type = wav_file.getcomptype()
            frame_count = wav_file.getnframes()
    except (EOFError, OSError, wave.Error):
        raise InvalidAudioOutputError("FFmpeg produced an unreadable WAV audio sample.") from None

    if frame_count <= 0:
        raise EmptyAudioOutputError("FFmpeg produced a WAV sample with no audio frames.")
    if (
        channel_count != CHANNEL_COUNT
        or sample_rate != SAMPLE_RATE
        or sample_width != SAMPLE_WIDTH_BYTES
        or compression_type != "NONE"
    ):
        raise InvalidAudioOutputError(
            "FFmpeg produced a WAV sample with an unexpected audio format."
        )

    return AudioSample(
        path=path,
        requested_duration_seconds=requested_duration_seconds,
        sample_rate=sample_rate,
        channel_count=channel_count,
        file_size_bytes=file_size,
    )
