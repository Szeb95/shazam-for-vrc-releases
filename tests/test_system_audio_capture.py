import wave
from pathlib import Path

import pytest

from shazam_for_vrc.streams.system_audio_capture import (
    SilentSystemAudioError,
    SystemAudioCaptureError,
    capture_system_audio,
)


class FakeStream:
    def __init__(self, *, sample_value: int = 1_000) -> None:
        self.sample_value = sample_value
        self.read_sizes: list[int] = []

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, frame_count: int, *, exception_on_overflow: bool) -> bytes:
        assert not exception_on_overflow
        self.read_sizes.append(frame_count)
        left = self.sample_value.to_bytes(2, "little", signed=True)
        right = (self.sample_value // 2).to_bytes(2, "little", signed=True)
        return (left + right) * frame_count


class FakeManager:
    def __init__(self, stream: FakeStream) -> None:
        self.stream = stream
        self.open_arguments: dict[str, object] = {}

    def __enter__(self) -> "FakeManager":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get_default_wasapi_loopback(self) -> dict[str, object]:
        return {"index": 7, "defaultSampleRate": 48_000.0, "maxInputChannels": 2}

    def open(self, **kwargs: object) -> FakeStream:
        self.open_arguments = kwargs
        return self.stream


class FakeBackend:
    paInt16 = 8

    def __init__(self, *, sample_value: int = 1_000) -> None:
        self.stream = FakeStream(sample_value=sample_value)
        self.manager = FakeManager(self.stream)

    def PyAudio(self) -> FakeManager:  # noqa: N802 - mirrors the dependency API
        return self.manager


def test_captures_default_windows_output_as_temporary_mono_wav() -> None:
    backend = FakeBackend()
    sample_path: Path
    sample_directory: Path

    with capture_system_audio(
        duration_seconds=0.01,
        backend_loader=lambda: backend,
    ) as sample:
        sample_path = sample.path
        sample_directory = sample.path.parent
        assert sample.path.is_file()
        assert sample.sample_rate == 48_000
        assert sample.channel_count == 1
        assert sample.requested_duration_seconds == 0.01
        with wave.open(str(sample.path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 48_000
            assert wav_file.getnframes() == 480

    assert not sample_path.exists()
    assert not sample_directory.exists()
    assert backend.manager.open_arguments["input_device_index"] == 7
    assert backend.manager.open_arguments["channels"] == 2
    assert backend.stream.read_sizes == [480]


def test_rejects_silent_system_output() -> None:
    backend = FakeBackend(sample_value=0)

    with pytest.raises(SilentSystemAudioError, match="recorded silence"), capture_system_audio(
        duration_seconds=0.01,
        backend_loader=lambda: backend,
    ):
        pass


def test_maps_backend_failure_to_actionable_error() -> None:
    class BrokenBackend:
        paInt16 = 8

        @staticmethod
        def PyAudio() -> object:  # noqa: N802 - mirrors the dependency API
            raise OSError("private device details")

    with (
        pytest.raises(SystemAudioCaptureError, match="default Windows output") as caught,
        capture_system_audio(
            duration_seconds=0.01,
            backend_loader=lambda: BrokenBackend(),
        ),
    ):
        pass

    assert "private" not in str(caught.value)


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan"), True])
def test_rejects_invalid_duration(duration: float) -> None:
    with pytest.raises(ValueError, match="finite, positive"), capture_system_audio(
        duration_seconds=duration,
        backend_loader=lambda: FakeBackend(),
    ):
        pass
