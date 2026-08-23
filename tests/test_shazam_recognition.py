import asyncio
import os
import warnings
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from shazam_for_vrc.recognition.models import RecognitionStatus
from shazam_for_vrc.recognition.shazam import (
    InvalidAudioSampleError,
    InvalidShazamResponseError,
    RecognitionBusyError,
    ShazamDependencyError,
    ShazamNetworkError,
    ShazamRecognizer,
    ShazamServiceError,
    ShazamTimeoutError,
    ShazamUnexpectedResponseError,
    _configure_media_tools,
)


def audio_sample(tmp_path: Path) -> Path:
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00" * 16_000)
    return path


class FakeShazamClient:
    def __init__(self, responses: list[Mapping[str, Any] | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Any]] = []

    async def recognize(self, data: str | bytes | bytearray) -> Mapping[str, Any]:
        return await self._respond("recognize", data)

    async def recognize_song(self, data: Any) -> Mapping[str, Any]:
        return await self._respond("recognize_song", data)

    async def _respond(
        self,
        method: str,
        data: Any,
    ) -> Mapping[str, Any]:
        self.calls.append((method, data))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_normalizes_a_track_match(tmp_path: Path) -> None:
    sample = audio_sample(tmp_path)
    client = FakeShazamClient(
        [
            {
                "matches": [{"id": "match-id"}],
                "track": {
                    "key": "12345",
                    "title": "  Around the World ",
                    "subtitle": "Daft Punk",
                    "url": "https://www.shazam.com/track/12345",
                    "images": {"coverart": "https://images.example/cover.jpg"},
                    "genres": {"primary": "Electronic"},
                    "sections": [
                        {
                            "metadata": [
                                {"title": "Released", "text": "1997"},
                                {"title": "Album", "text": "Homework"},
                            ]
                        }
                    ],
                },
            }
        ]
    )

    result = asyncio.run(ShazamRecognizer(client=client).recognize(sample))

    assert result.status is RecognitionStatus.MATCHED
    assert result.is_match
    assert result.provider == "shazam"
    assert result.attempt_count == 1
    assert result.track is not None
    assert result.track.title == "Around the World"
    assert result.track.artist == "Daft Punk"
    assert result.track.album == "Homework"
    assert result.track.genre == "Electronic"
    assert result.track.artwork_url == "https://images.example/cover.jpg"
    assert result.track.track_url == "https://www.shazam.com/track/12345"
    assert result.track.provider_track_id == "12345"
    assert client.calls == [("recognize", str(sample))]


def test_retries_no_match_once_and_then_returns_no_match(tmp_path: Path) -> None:
    sample = audio_sample(tmp_path)
    client = FakeShazamClient([{"matches": []}, {"matches": []}])

    result = asyncio.run(ShazamRecognizer(client=client).recognize(sample))

    assert result.status is RecognitionStatus.NO_MATCH
    assert not result.is_match
    assert result.track is None
    assert result.attempt_count == 2
    _assert_legacy_fallback_calls(client, sample)


def test_legacy_fingerprint_fallback_can_recover_a_match(tmp_path: Path) -> None:
    sample = audio_sample(tmp_path)
    client = FakeShazamClient(
        [
            {"matches": []},
            {
                "matches": [{"id": "legacy-match"}],
                "track": {"title": "Magnetude", "subtitle": "Mantis"},
            },
        ]
    )

    result = asyncio.run(ShazamRecognizer(client=client).recognize(sample))

    assert result.is_match
    assert result.attempt_count == 2
    assert result.track is not None
    assert result.track.title == "Magnetude"
    assert result.track.artist == "Mantis"
    _assert_legacy_fallback_calls(client, sample)


def test_contains_legacy_client_deprecation_warning(tmp_path: Path) -> None:
    sample = audio_sample(tmp_path)

    class WarningClient(FakeShazamClient):
        async def recognize_song(
            self,
            data: Any,
        ) -> Mapping[str, Any]:
            warnings.simplefilter("always", DeprecationWarning)
            warnings.warn("upstream deprecation", DeprecationWarning, stacklevel=2)
            warnings.simplefilter("default", DeprecationWarning)
            return await self._respond("recognize_song", data)

    client = WarningClient([{"matches": []}, {"matches": []}])

    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        result = asyncio.run(ShazamRecognizer(client=client).recognize(sample))

    assert result.status is RecognitionStatus.NO_MATCH
    assert emitted == []


def test_legacy_fallback_does_not_launch_media_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydub import audio_segment

    def must_not_start(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("A WAV capture should be decoded without FFmpeg or FFprobe")

    monkeypatch.setattr(audio_segment.subprocess, "Popen", must_not_start)
    client = FakeShazamClient([{"matches": []}, {"matches": []}])

    result = asyncio.run(ShazamRecognizer(client=client).recognize(audio_sample(tmp_path)))

    assert result.status is RecognitionStatus.NO_MATCH


def _assert_legacy_fallback_calls(client: FakeShazamClient, sample: Path) -> None:
    from pydub import AudioSegment

    assert len(client.calls) == 2
    assert client.calls[0] == ("recognize", str(sample))
    method, decoded = client.calls[1]
    assert method == "recognize_song"
    assert isinstance(decoded, AudioSegment)
    assert decoded.frame_rate == 16_000
    assert decoded.channels == 1


def test_retry_can_be_disabled(tmp_path: Path) -> None:
    sample = audio_sample(tmp_path)
    client = FakeShazamClient([{"matches": []}])

    result = asyncio.run(ShazamRecognizer(client=client, no_match_retries=0).recognize(sample))

    assert result.status is RecognitionStatus.NO_MATCH
    assert result.attempt_count == 1
    assert client.calls == [("recognize", str(sample))]


def test_labels_a_connection_failure_without_exposing_private_details(tmp_path: Path) -> None:
    sample = audio_sample(tmp_path)
    client = FakeShazamClient([ConnectionError("private provider diagnostics")])

    with pytest.raises(ShazamNetworkError, match="firewall.*VPN.*DNS") as caught:
        asyncio.run(ShazamRecognizer(client=client).recognize(sample))

    assert "private provider diagnostics" not in str(caught.value)
    assert client.calls == [("recognize", str(sample))]


def test_labels_a_missing_bundled_media_tool(tmp_path: Path) -> None:
    sample = audio_sample(tmp_path)
    client = FakeShazamClient([FileNotFoundError("private local path")])

    with pytest.raises(ShazamDependencyError, match="media-tool-missing") as caught:
        asyncio.run(ShazamRecognizer(client=client).recognize(sample))

    assert "private local path" not in str(caught.value)


def test_labels_a_non_json_provider_response(tmp_path: Path) -> None:
    class FailedDecodeJson(Exception):
        pass

    sample = audio_sample(tmp_path)
    client = FakeShazamClient([FailedDecodeJson("private provider response")])

    with pytest.raises(
        ShazamUnexpectedResponseError,
        match="unexpected response.*VPN.*ad-blocker",
    ) as caught:
        asyncio.run(ShazamRecognizer(client=client).recognize(sample))

    assert "private provider response" not in str(caught.value)


def test_generic_service_failure_includes_only_a_safe_error_code(tmp_path: Path) -> None:
    sample = audio_sample(tmp_path)
    client = FakeShazamClient([RuntimeError("private provider diagnostics")])

    with pytest.raises(ShazamServiceError, match="error code: RuntimeError") as caught:
        asyncio.run(ShazamRecognizer(client=client).recognize(sample))

    assert "private provider diagnostics" not in str(caught.value)


def test_bundled_ffmpeg_directory_is_available_to_legacy_recognition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydub import AudioSegment

    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"test executable placeholder")
    ffprobe.write_bytes(b"test executable placeholder")
    original_converter = AudioSegment.converter
    monkeypatch.setenv("PATH", "")

    try:
        _configure_media_tools(str(ffmpeg))

        assert AudioSegment.converter == str(ffmpeg)
        assert Path(os.environ["PATH"].split(os.pathsep)[0]) == tmp_path
        assert ffprobe.is_file()
    finally:
        AudioSegment.converter = original_converter


def test_total_timeout_stops_recognition(tmp_path: Path) -> None:
    sample = audio_sample(tmp_path)

    class SlowClient:
        async def recognize(self, _data: str | bytes | bytearray) -> Mapping[str, Any]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def recognize_song(
            self,
            _data: str | bytes | bytearray,
        ) -> Mapping[str, Any]:
            raise AssertionError("fallback must not start after a timeout")

    with pytest.raises(ShazamTimeoutError, match="total timeout"):
        asyncio.run(ShazamRecognizer(client=SlowClient(), timeout_seconds=0.01).recognize(sample))


def test_rejects_overlapping_requests(tmp_path: Path) -> None:
    sample = audio_sample(tmp_path)

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingClient:
            async def recognize(self, _data: str | bytes | bytearray) -> Mapping[str, Any]:
                started.set()
                await release.wait()
                return {"matches": []}

            async def recognize_song(
                self,
                _data: str | bytes | bytearray,
            ) -> Mapping[str, Any]:
                raise AssertionError("retry is disabled in this test")

        recognizer = ShazamRecognizer(
            client=BlockingClient(),
            no_match_retries=0,
        )
        first = asyncio.create_task(recognizer.recognize(sample))
        await started.wait()
        with pytest.raises(RecognitionBusyError, match="already in progress"):
            await recognizer.recognize(sample)
        release.set()
        assert (await first).status is RecognitionStatus.NO_MATCH

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"matches": [{"id": "match-without-track"}]},
        {"matches": "not-a-list"},
        {"track": {"title": "Missing artist"}},
    ],
)
def test_rejects_malformed_provider_responses(
    tmp_path: Path,
    response: Mapping[str, Any],
) -> None:
    sample = audio_sample(tmp_path)

    with pytest.raises(InvalidShazamResponseError):
        asyncio.run(ShazamRecognizer(client=FakeShazamClient([response])).recognize(sample))


@pytest.mark.parametrize("contents", [None, b""])
def test_rejects_missing_or_empty_audio_before_calling_provider(
    tmp_path: Path,
    contents: bytes | None,
) -> None:
    sample = tmp_path / "sample.wav"
    if contents is not None:
        sample.write_bytes(contents)
    client = FakeShazamClient([{"matches": []}])

    with pytest.raises(InvalidAudioSampleError, match="missing, empty"):
        asyncio.run(ShazamRecognizer(client=client).recognize(sample))

    assert client.calls == []


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_rejects_invalid_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="finite, positive"):
        ShazamRecognizer(timeout_seconds=timeout)


@pytest.mark.parametrize("retries", [-1, 2, True])
def test_allows_at_most_one_no_match_retry(retries: int) -> None:
    with pytest.raises(ValueError, match="zero or one"):
        ShazamRecognizer(no_match_retries=retries)
