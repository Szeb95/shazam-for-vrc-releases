import logging
import subprocess
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from shazam_for_vrc.streams import audio_capture
from shazam_for_vrc.streams.audio_capture import (
    CaptureTimeoutError,
    EmptyAudioOutputError,
    FFmpegCaptureError,
    FFmpegNotFoundError,
    InvalidAudioOutputError,
    InvalidCaptureDurationError,
    InvalidCaptureStartError,
    InvalidCaptureTimeoutError,
    NoAudioStreamError,
    UnsafeHttpHeadersError,
    UnsupportedPlaybackError,
    capture_audio,
)
from shazam_for_vrc.streams.resolver import ResolutionSource, ResolvedStream
from shazam_for_vrc.streams.stream_detector import PlaybackType, Provider, Transport


def resolved_stream(
    *,
    stream_url: str = "https://cdn.example/live/index.m3u8?token=private-token",
    transport: Transport = Transport.HLS,
    playback_type: PlaybackType = PlaybackType.LIVE,
    http_headers: dict[str, str] | None = None,
    rtsp_transport: str | None = None,
) -> ResolvedStream:
    return ResolvedStream(
        original_url="https://provider.example/watch?private=original-token",
        stream_url=stream_url,
        provider=Provider.DIRECT,
        transport=transport,
        playback_type=playback_type,
        resolution_source=ResolutionSource.YT_DLP,
        http_headers=http_headers or {},
        rtsp_transport=rtsp_transport,
    )


def write_valid_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(44_100)
        wav_file.writeframes(b"\x00\x00" * 100)


class CompletedProcess:
    def __init__(
        self,
        command: list[str],
        *,
        writer: Callable[[Path], None] | None = write_valid_wav,
        returncode: int = 0,
        stderr: str = "",
    ) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        self.timeouts: list[float | None] = []
        self.terminated = False
        self.killed = False
        if writer is not None:
            writer(Path(command[-1]))

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.timeouts.append(timeout)
        return "", self.stderr

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def install_completed_process(
    monkeypatch: pytest.MonkeyPatch,
    *,
    writer: Callable[[Path], None] | None = write_valid_wav,
    returncode: int = 0,
    stderr: str = "",
) -> tuple[list[list[str]], list[dict[str, Any]], list[CompletedProcess]]:
    commands: list[list[str]] = []
    keyword_arguments: list[dict[str, Any]] = []
    processes: list[CompletedProcess] = []

    def popen(command: list[str], **kwargs: Any) -> CompletedProcess:
        commands.append(command)
        keyword_arguments.append(kwargs)
        process = CompletedProcess(
            command,
            writer=writer,
            returncode=returncode,
            stderr=stderr,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(audio_capture.subprocess, "Popen", popen)
    return commands, keyword_arguments, processes


def test_captures_hls_to_temporary_shazam_wav_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands, keyword_arguments, processes = install_completed_process(monkeypatch)
    signed_url = "https://cdn.example/live/index.m3u8?token=a%2Fb&expires=123"
    sample_path: Path
    sample_directory: Path

    with capture_audio(resolved_stream(stream_url=signed_url)) as sample:
        sample_path = sample.path
        sample_directory = sample.path.parent
        assert sample.path.exists()
        assert sample.requested_duration_seconds == 8.0
        assert sample.sample_rate == 44_100
        assert sample.channel_count == 1
        assert sample.file_size_bytes == sample.path.stat().st_size

    assert not sample_path.exists()
    assert not sample_directory.exists()
    assert len(commands) == 1
    command = commands[0]
    assert isinstance(command, list)
    assert command[command.index("-i") + 1] == signed_url
    assert command[command.index("-t") + 1] == "8"
    assert command[command.index("-c:a") :][:2] == ["-c:a", "pcm_s16le"]
    assert command[command.index("-ac") :][:2] == ["-ac", "1"]
    assert command[command.index("-ar") :][:2] == ["-ar", "44100"]
    assert "-vn" in command
    assert "shell" not in keyword_arguments[0]
    assert keyword_arguments[0]["creationflags"] == getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )
    assert processes[0].timeouts == [23.0]


def test_passes_http_headers_as_input_options_without_changing_signed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands, _kwargs, _processes = install_completed_process(monkeypatch)
    signed_url = "https://cdn.example/audio.m4a?Policy=exact%2Bvalue&Signature=secret"
    stream = resolved_stream(
        stream_url=signed_url,
        transport=Transport.DIRECT_MEDIA,
        http_headers={
            "Referer": "https://provider.example/",
            "Authorization": "Bearer private-credential",
        },
    )

    with capture_audio(stream, duration_seconds=3.25, timeout_seconds=9):
        pass

    command = commands[0]
    header_index = command.index("-headers")
    input_index = command.index("-i")
    assert header_index < input_index
    assert command[header_index + 1] == (
        "Referer: https://provider.example/\r\nAuthorization: Bearer private-credential\r\n"
    )
    assert command[input_index + 1] == signed_url
    assert command[command.index("-t") + 1] == "3.25"


def test_places_vrcdn_rtsp_tcp_option_before_input(monkeypatch: pytest.MonkeyPatch) -> None:
    commands, _kwargs, _processes = install_completed_process(monkeypatch)
    stream = resolved_stream(
        stream_url="rtsp://stream.vrcdn.live/live/private-stream-name",
        transport=Transport.RTSP,
        rtsp_transport="tcp",
    )

    with capture_audio(stream):
        pass

    command = commands[0]
    rtsp_index = command.index("-rtsp_transport")
    input_index = command.index("-i")
    assert command[rtsp_index + 1] == "tcp"
    assert rtsp_index < input_index
    assert command[input_index + 1].startswith("rtsp://")


@pytest.mark.parametrize(
    "headers",
    [
        {"Good\r\nInjected": "value"},
        {"Authorization": "safe\nX-Injected: unsafe"},
    ],
)
def test_rejects_header_line_injection_before_starting_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    def must_not_start(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("FFmpeg must not start with unsafe headers")

    monkeypatch.setattr(audio_capture.subprocess, "Popen", must_not_start)

    with (
        pytest.raises(UnsafeHttpHeadersError, match="prohibited line break"),
        capture_audio(resolved_stream(http_headers=headers)),
    ):
        pass


@pytest.mark.parametrize("playback_type", [PlaybackType.PRERECORDED, PlaybackType.UNKNOWN])
def test_refuses_media_that_is_not_known_to_be_current(
    monkeypatch: pytest.MonkeyPatch,
    playback_type: PlaybackType,
) -> None:
    def must_not_start(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("FFmpeg must not start for unsupported playback")

    monkeypatch.setattr(audio_capture.subprocess, "Popen", must_not_start)

    with (
        pytest.raises(UnsupportedPlaybackError, match="current VRChat playback moment"),
        capture_audio(resolved_stream(playback_type=playback_type)),
    ):
        pass


def test_experimental_override_is_explicit_and_allows_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_completed_process(monkeypatch)

    with capture_audio(
        resolved_stream(playback_type=PlaybackType.PRERECORDED),
        allow_unsupported_playback=True,
    ) as sample:
        assert sample.path.exists()


def test_places_prerecorded_seek_before_input(monkeypatch: pytest.MonkeyPatch) -> None:
    commands, _kwargs, _processes = install_completed_process(monkeypatch)

    with capture_audio(
        resolved_stream(playback_type=PlaybackType.PRERECORDED),
        allow_unsupported_playback=True,
        start_seconds=91.5,
    ):
        pass

    command = commands[0]
    assert command[command.index("-ss") + 1] == "91.5"
    assert command.index("-ss") < command.index("-i")


@pytest.mark.parametrize("start", [-1, float("inf"), float("nan"), True, "10"])
def test_rejects_invalid_capture_start(
    monkeypatch: pytest.MonkeyPatch,
    start: object,
) -> None:
    def must_not_start(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("FFmpeg must not start with an invalid seek position")

    monkeypatch.setattr(audio_capture.subprocess, "Popen", must_not_start)

    with (
        pytest.raises(InvalidCaptureStartError, match="finite, non-negative"),
        capture_audio(resolved_stream(), start_seconds=start),  # type: ignore[arg-type]
    ):
        pass


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan"), True])
def test_rejects_invalid_duration(
    monkeypatch: pytest.MonkeyPatch,
    duration: float,
) -> None:
    def must_not_start(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("FFmpeg must not start with an invalid duration")

    monkeypatch.setattr(audio_capture.subprocess, "Popen", must_not_start)

    with (
        pytest.raises(InvalidCaptureDurationError, match="finite, positive"),
        capture_audio(resolved_stream(), duration_seconds=duration),
    ):
        pass


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_rejects_invalid_timeout(
    monkeypatch: pytest.MonkeyPatch,
    timeout: float,
) -> None:
    def must_not_start(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("FFmpeg must not start with an invalid timeout")

    monkeypatch.setattr(audio_capture.subprocess, "Popen", must_not_start)

    with (
        pytest.raises(InvalidCaptureTimeoutError, match="finite, positive"),
        capture_audio(resolved_stream(), timeout_seconds=timeout),
    ):
        pass


def test_reports_missing_ffmpeg_without_exposing_media(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def missing(_command: list[str], **_kwargs: Any) -> None:
        raise FileNotFoundError("private-token Authorization private-credential")

    monkeypatch.setattr(audio_capture.subprocess, "Popen", missing)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(FFmpegNotFoundError) as caught, capture_audio(resolved_stream()):
        pass

    combined = str(caught.value) + caplog.text
    assert "private-token" not in combined
    assert "private-credential" not in combined
    assert "Authorization" not in combined


def test_classifies_no_audio_error_without_exposing_ffmpeg_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_diagnostics = (
        "https://cdn.example/?token=private-token Authorization: private-credential "
        "Stream map '0:a:0' matches no streams"
    )
    install_completed_process(
        monkeypatch,
        writer=None,
        returncode=1,
        stderr=private_diagnostics,
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(NoAudioStreamError) as caught, capture_audio(resolved_stream()):
        pass

    combined = str(caught.value) + caplog.text
    assert "private-token" not in combined
    assert "private-credential" not in combined
    assert "cdn.example" not in combined


def test_reports_generic_ffmpeg_failure_without_raw_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_completed_process(
        monkeypatch,
        writer=None,
        returncode=1,
        stderr="Input URL has token=private-token and Cookie: private-cookie",
    )

    with pytest.raises(FFmpegCaptureError) as caught, capture_audio(resolved_stream()):
        pass

    assert "private-token" not in str(caught.value)
    assert "private-cookie" not in str(caught.value)


def test_terminates_then_kills_timed_out_ffmpeg_and_removes_partial_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes: list[CompletedProcess] = []
    partial_paths: list[Path] = []

    class TimedOutProcess(CompletedProcess):
        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            self.timeouts.append(timeout)
            if len(self.timeouts) <= 2:
                raise subprocess.TimeoutExpired(self.command, timeout)
            return "", "private-token"

    def popen(command: list[str], **_kwargs: Any) -> TimedOutProcess:
        path = Path(command[-1])
        path.write_bytes(b"partial private audio")
        partial_paths.append(path)
        process = TimedOutProcess(command, writer=None)
        processes.append(process)
        return process

    monkeypatch.setattr(audio_capture.subprocess, "Popen", popen)

    with (
        pytest.raises(CaptureTimeoutError) as caught,
        capture_audio(resolved_stream(), timeout_seconds=0.01),
    ):
        pass

    assert "private-token" not in str(caught.value)
    assert processes[0].terminated
    assert processes[0].killed
    assert processes[0].timeouts == [0.01, 2.0, None]
    assert not partial_paths[0].exists()
    assert not partial_paths[0].parent.exists()


def test_rejects_missing_output_and_cleans_temporary_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands, _kwargs, _processes = install_completed_process(monkeypatch, writer=None)

    with (
        pytest.raises(EmptyAudioOutputError, match="did not produce"),
        capture_audio(resolved_stream()),
    ):
        pass

    output_path = Path(commands[0][-1])
    assert not output_path.exists()
    assert not output_path.parent.exists()


def test_rejects_invalid_wav_and_removes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def write_invalid(path: Path) -> None:
        path.write_bytes(b"not a wave file; token=private-token")

    commands, _kwargs, _processes = install_completed_process(monkeypatch, writer=write_invalid)

    with pytest.raises(InvalidAudioOutputError) as caught, capture_audio(resolved_stream()):
        pass

    assert "private-token" not in str(caught.value)
    output_path = Path(commands[0][-1])
    assert not output_path.exists()
    assert not output_path.parent.exists()


def test_rejects_wav_without_audio_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    def write_empty_wav(path: Path) -> None:
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(44_100)

    install_completed_process(monkeypatch, writer=write_empty_wav)

    with (
        pytest.raises(EmptyAudioOutputError, match="no audio frames"),
        capture_audio(resolved_stream()),
    ):
        pass


def test_cleans_sample_when_caller_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    install_completed_process(monkeypatch)
    sample_path: Path

    with (
        pytest.raises(LookupError, match="caller failed"),
        capture_audio(resolved_stream()) as sample,
    ):
        sample_path = sample.path
        raise LookupError("caller failed")

    assert not sample_path.exists()
    assert not sample_path.parent.exists()
