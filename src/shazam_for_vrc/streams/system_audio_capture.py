"""Explicit Windows system-output capture for the final recognition fallback."""

from __future__ import annotations

import math
import sys
import tempfile
import wave
from array import array
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from shazam_for_vrc.streams.audio_capture import AudioSample

DEFAULT_DURATION_SECONDS = 12.0
FRAMES_PER_BUFFER = 2_048
SAMPLE_WIDTH_BYTES = 2
MINIMUM_AUDIBLE_RMS = 8.0


class SystemAudioCaptureError(RuntimeError):
    """Base error for failures while recording the current Windows output mix."""


class SystemAudioUnavailableError(SystemAudioCaptureError):
    """Raised when WASAPI loopback capture is unavailable on this computer."""


class SilentSystemAudioError(SystemAudioCaptureError):
    """Raised when the final fallback records no audible output."""


BackendLoader = Callable[[], Any]


@contextmanager
def capture_system_audio(
    *,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    backend_loader: BackendLoader | None = None,
) -> Iterator[AudioSample]:
    """Record the default Windows output as a temporary mono PCM WAV.

    This captures the complete output mix, which can include VRChat voices, world
    sounds, notifications, and other applications. The path exists only inside
    the context and is always removed afterward.
    """

    duration = _capture_duration(duration_seconds)
    loader = backend_loader or _load_backend
    backend = loader()

    with tempfile.TemporaryDirectory(prefix="shazam-for-vrc-system-audio-") as directory:
        output_path = Path(directory) / "system-output.wav"
        sample_rate, frame_count, sum_squares = _record_default_output(
            backend,
            output_path,
            duration_seconds=duration,
        )
        if frame_count <= 0:
            raise SilentSystemAudioError(
                "The final computer-audio attempt received no audio frames."
            )
        rms = math.sqrt(sum_squares / frame_count)
        if rms < MINIMUM_AUDIBLE_RMS:
            raise SilentSystemAudioError(
                "The final computer-audio attempt recorded silence. Make sure VRChat music "
                "is audible through the default Windows output device, then try again."
            )
        try:
            file_size = output_path.stat().st_size
        except OSError:
            raise SystemAudioCaptureError(
                "The final computer-audio attempt did not produce a usable recording."
            ) from None
        yield AudioSample(
            path=output_path,
            requested_duration_seconds=duration,
            sample_rate=sample_rate,
            channel_count=1,
            file_size_bytes=file_size,
        )


def _load_backend() -> Any:
    if sys.platform != "win32":
        raise SystemAudioUnavailableError(
            "The computer-audio fallback is available only on Windows."
        )
    try:
        import pyaudiowpatch
    except (ImportError, OSError) as error:
        raise SystemAudioUnavailableError(
            "The Windows computer-audio component is unavailable. Reinstall Shazam for VRC "
            "1.2.0 or newer."
        ) from error
    return pyaudiowpatch


def _record_default_output(
    backend: Any,
    output_path: Path,
    *,
    duration_seconds: float,
) -> tuple[int, int, int]:
    try:
        with backend.PyAudio() as audio_manager:
            raw_device = audio_manager.get_default_wasapi_loopback()
            device = _loopback_device(raw_device)
            sample_rate = _positive_integer(device.get("defaultSampleRate"))
            channel_count = _positive_integer(device.get("maxInputChannels"))
            device_index = _nonnegative_integer(device.get("index"))
            requested_frames = max(1, math.ceil(duration_seconds * sample_rate))
            with audio_manager.open(
                format=backend.paInt16,
                channels=channel_count,
                rate=sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=FRAMES_PER_BUFFER,
            ) as stream:
                return _write_mono_wav(
                    stream,
                    output_path,
                    requested_frames=requested_frames,
                    sample_rate=sample_rate,
                    input_channel_count=channel_count,
                )
    except SystemAudioCaptureError:
        raise
    except Exception:
        raise SystemAudioCaptureError(
            "The final computer-audio attempt could not record the default Windows output. "
            "Make sure VRChat is audible and Windows has an active default output device."
        ) from None


def _write_mono_wav(
    stream: Any,
    output_path: Path,
    *,
    requested_frames: int,
    sample_rate: int,
    input_channel_count: int,
) -> tuple[int, int, int]:
    frames_written = 0
    sum_squares = 0
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(sample_rate)
        while frames_written < requested_frames:
            requested = min(FRAMES_PER_BUFFER, requested_frames - frames_written)
            raw = stream.read(requested, exception_on_overflow=False)
            mono = _downmix_pcm16(raw, input_channel_count, requested)
            if not mono:
                break
            wav_file.writeframesraw(mono.tobytes())
            frames_written += len(mono)
            sum_squares += sum(sample * sample for sample in mono)
        wav_file.writeframes(b"")
    return sample_rate, frames_written, sum_squares


def _downmix_pcm16(raw: object, channel_count: int, maximum_frames: int) -> array[int]:
    if not isinstance(raw, bytes | bytearray | memoryview):
        raise SystemAudioCaptureError(
            "The Windows audio component returned an invalid recording buffer."
        )
    samples = array("h")
    usable_bytes = len(raw) - (len(raw) % SAMPLE_WIDTH_BYTES)
    samples.frombytes(bytes(raw[:usable_bytes]))
    if sys.byteorder != "little":
        samples.byteswap()
    complete_frames = min(len(samples) // channel_count, maximum_frames)
    mono = array("h")
    for frame in range(complete_frames):
        start = frame * channel_count
        value = sum(samples[start : start + channel_count]) // channel_count
        mono.append(max(-32_768, min(32_767, value)))
    return mono


def _capture_duration(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("duration_seconds must be a finite, positive number")
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration_seconds must be a finite, positive number")
    return duration


def _loopback_device(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SystemAudioUnavailableError(
            "Windows did not report a default output device for computer-audio capture."
        )
    return value


def _positive_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SystemAudioUnavailableError(
            "Windows reported an unusable default output-device format."
        )
    result = int(round(float(value)))
    if result <= 0:
        raise SystemAudioUnavailableError(
            "Windows reported an unusable default output-device format."
        )
    return result


def _nonnegative_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SystemAudioUnavailableError(
            "Windows did not report a usable default output device."
        )
    result = int(value)
    if result < 0:
        raise SystemAudioUnavailableError(
            "Windows did not report a usable default output device."
        )
    return result
