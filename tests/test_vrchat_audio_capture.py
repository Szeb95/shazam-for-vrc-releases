from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from shazam_for_vrc.streams.vrchat_audio_capture import (
    SilentVRChatAudioError,
    VRChatAudioUnavailableError,
    capture_vrchat_audio,
)


@dataclass
class _FakeProcess:
    pid: int
    name: str


class _FakeCapture:
    created: list[_FakeCapture] = []
    level = -12.0

    def __init__(self, *, pid: int, output_path: str, level_callback) -> None:
        self.pid = pid
        self.output_path = Path(output_path)
        self.level_callback = level_callback
        self.started = False
        self.stopped = False
        self.created.append(self)

    def start(self) -> None:
        self.started = True
        with wave.open(str(self.output_path), "wb") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48_000)
            wav_file.writeframes((1_000).to_bytes(2, "little", signed=True) * 200)
        self.level_callback(self.level)

    def stop(self) -> None:
        self.stopped = True


class _FakeCaptureType(_FakeCapture):
    supported = True
    processes = [
        _FakeProcess(99, "music.exe"),
        _FakeProcess(42, "VRChat.exe"),
    ]

    @classmethod
    def is_supported(cls) -> bool:
        return cls.supported

    @classmethod
    def enumerate_audio_processes(cls) -> list[_FakeProcess]:
        return cls.processes


class _FakeBackend:
    ProcessAudioCapture = _FakeCaptureType


def test_captures_only_vrchat_process_audio_and_removes_temporary_file() -> None:
    waits: list[float] = []
    sample_path: Path
    sample_directory: Path

    with capture_vrchat_audio(
        duration_seconds=0.25,
        backend_loader=lambda: _FakeBackend,
        sleeper=waits.append,
    ) as sample:
        sample_path = sample.path
        sample_directory = sample.path.parent
        capture = _FakeCapture.created[-1]
        assert capture.pid == 42
        assert capture.started
        assert capture.stopped
        assert sample.path.is_file()
        assert sample.sample_rate == 48_000
        assert sample.channel_count == 2
        assert sample.requested_duration_seconds == 0.25

    assert waits == [0.25]
    assert not sample_path.exists()
    assert not sample_directory.exists()


def test_reports_when_process_loopback_is_not_supported() -> None:
    original = _FakeCaptureType.supported
    _FakeCaptureType.supported = False
    try:
        with pytest.raises(
            VRChatAudioUnavailableError,
            match="does not support",
        ), capture_vrchat_audio(
            backend_loader=lambda: _FakeBackend,
            sleeper=lambda _seconds: None,
        ):
            pass
    finally:
        _FakeCaptureType.supported = original


def test_reports_when_vrchat_has_no_audio_session() -> None:
    original = _FakeCaptureType.processes
    _FakeCaptureType.processes = [_FakeProcess(99, "music.exe")]
    try:
        with pytest.raises(
            VRChatAudioUnavailableError,
            match="No VRChat audio session",
        ), capture_vrchat_audio(
            backend_loader=lambda: _FakeBackend,
            sleeper=lambda _seconds: None,
        ):
            pass
    finally:
        _FakeCaptureType.processes = original


def test_rejects_silent_vrchat_process_audio() -> None:
    original = _FakeCaptureType.level
    _FakeCaptureType.level = -60.0
    try:
        with pytest.raises(
            SilentVRChatAudioError,
            match="recorded silence",
        ), capture_vrchat_audio(
            backend_loader=lambda: _FakeBackend,
            sleeper=lambda _seconds: None,
        ):
            pass
    finally:
        _FakeCaptureType.level = original


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan"), True])
def test_rejects_invalid_duration(duration: float) -> None:
    with pytest.raises(ValueError, match="finite, positive"), capture_vrchat_audio(
        duration_seconds=duration,
        backend_loader=lambda: _FakeBackend,
        sleeper=lambda _seconds: None,
    ):
        pass
