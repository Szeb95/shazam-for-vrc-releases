import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from shazam_for_vrc.application import (
    ListeningProgress,
    ListeningResultKind,
    ListeningService,
    ListeningStage,
    ListenOptions,
    PlayerDebugService,
    _shareable_source_url,
    delete_last_sample,
    retain_last_sample,
)
from shazam_for_vrc.recognition.models import (
    RecognitionResult,
    RecognitionStatus,
    RecognizedTrack,
)
from shazam_for_vrc.streams.audio_capture import AudioSample
from shazam_for_vrc.streams.playback_position import (
    PlaybackPositionEstimate,
    PositionSource,
)
from shazam_for_vrc.streams.resolver import (
    ContentKind,
    MediaMetadata,
    ResolutionSource,
    ResolvedStream,
)
from shazam_for_vrc.streams.stream_detector import PlaybackType, Provider, Transport
from shazam_for_vrc.streams.vrchat_audio_capture import FallbackAudioSource
from shazam_for_vrc.vrchat.log_reader import LogSnapshot, MediaInfo, WorldInfo
from shazam_for_vrc.vrchat.player_tracker import (
    MediaCandidate,
    SelectionReason,
    select_active_media,
)


def no_match() -> RecognitionResult:
    return RecognitionResult(
        status=RecognitionStatus.NO_MATCH,
        track=None,
        provider="test",
        attempt_count=1,
    )


def match() -> RecognitionResult:
    return RecognitionResult(
        status=RecognitionStatus.MATCHED,
        track=RecognizedTrack(title="Around the World", artist="Daft Punk"),
        provider="test",
        attempt_count=1,
    )


class FakeRecognizer:
    def __init__(self, results: list[RecognitionResult]) -> None:
        self.results = results
        self.sample_paths: list[Path] = []

    async def recognize(self, sample_path: Path) -> RecognitionResult:
        assert sample_path.is_file()
        self.sample_paths.append(sample_path)
        return self.results.pop(0)


def snapshot() -> LogSnapshot:
    return LogSnapshot(
        world=WorldInfo(name="Example Club", world_id="wrld_example", instance_id="1"),
        media=MediaInfo(original_url="https://example.test/live"),
    )


def resolved_stream() -> ResolvedStream:
    return ResolvedStream(
        original_url="https://example.test/live",
        stream_url="https://example.test/live.m3u8",
        provider=Provider.DIRECT,
        transport=Transport.HLS,
        playback_type=PlaybackType.LIVE,
        resolution_source=ResolutionSource.YT_DLP,
    )


def test_retries_with_fresh_recordings_and_retains_only_when_enabled(tmp_path: Path) -> None:
    capture_durations: list[float] = []
    capture_paths: list[Path] = []
    retained_sources: list[Path] = []
    progress: list[ListeningProgress] = []
    recognizer = FakeRecognizer([no_match(), match()])

    @contextmanager
    def capture(
        _stream: object,
        *,
        duration_seconds: float,
        ffmpeg_executable: str,
    ) -> Iterator[AudioSample]:
        assert ffmpeg_executable == "fake-ffmpeg"
        capture_durations.append(duration_seconds)
        path = tmp_path / f"temporary-{len(capture_paths) + 1}.wav"
        path.write_bytes(b"RIFF-fake-audio")
        capture_paths.append(path)
        try:
            yield AudioSample(path, duration_seconds, 44_100, 1, path.stat().st_size)
        finally:
            path.unlink()

    def retain(path: Path, destination: Path | None) -> Path:
        assert path.is_file()
        retained_sources.append(path)
        target = destination or (tmp_path / "last-sample.wav")
        target.write_bytes(path.read_bytes())
        return target

    service = ListeningService(
        snapshot_reader=snapshot,
        media_selector=select_active_media,
        stream_resolver=lambda _candidate: resolved_stream(),
        capture_factory=capture,
        recognizer=recognizer,
        sample_retainer=retain,
        ffmpeg_executable="fake-ffmpeg",
    )

    outcome = asyncio.run(
        service.listen(
            ListenOptions(record_seconds=12, retry_count=2, keep_last_sample=True),
            progress.append,
        )
    )

    assert outcome.recognition.is_match
    assert outcome.world.name == "Example Club"
    assert outcome.provider is Provider.DIRECT
    assert outcome.result_kind is ListeningResultKind.LIVE
    assert outcome.source_url is None
    assert outcome.recording_attempts == 2
    assert outcome.retained_sample_path == tmp_path / "last-sample.wav"
    assert capture_durations == [12, 12]
    assert len(set(capture_paths)) == 2
    assert retained_sources == capture_paths
    assert all(not path.exists() for path in capture_paths)
    assert [item.stage for item in progress] == [
        ListeningStage.FINDING_PLAYER,
        ListeningStage.RESOLVING_STREAM,
        ListeningStage.RECORDING,
        ListeningStage.RECOGNIZING,
        ListeningStage.RETRYING,
        ListeningStage.RECORDING,
        ListeningStage.RECOGNIZING,
        ListeningStage.COMPLETE,
    ]


def test_returns_no_match_after_configured_number_of_fresh_attempts(tmp_path: Path) -> None:
    capture_count = 0

    @contextmanager
    def capture(
        _stream: object,
        *,
        duration_seconds: float,
        ffmpeg_executable: str,
    ) -> Iterator[AudioSample]:
        nonlocal capture_count
        capture_count += 1
        path = tmp_path / f"sample-{capture_count}.wav"
        path.write_bytes(b"RIFF-fake-audio")
        try:
            yield AudioSample(path, duration_seconds, 44_100, 1, path.stat().st_size)
        finally:
            path.unlink()

    recognizer = FakeRecognizer([no_match(), no_match(), no_match()])
    service = ListeningService(
        snapshot_reader=snapshot,
        media_selector=select_active_media,
        stream_resolver=lambda _candidate: resolved_stream(),
        capture_factory=capture,
        recognizer=recognizer,
        ffmpeg_executable="fake-ffmpeg",
    )

    outcome = asyncio.run(service.listen(ListenOptions(retry_count=2)))

    assert not outcome.recognition.is_match
    assert outcome.recording_attempts == 3
    assert capture_count == 3
    assert outcome.retained_sample_path is None


def test_uses_computer_audio_only_after_two_clean_no_matches(tmp_path: Path) -> None:
    clean_capture_count = 0
    system_capture_count = 0
    progress: list[ListeningProgress] = []

    @contextmanager
    def capture_clean(
        _stream: object,
        *,
        duration_seconds: float,
        ffmpeg_executable: str,
    ) -> Iterator[AudioSample]:
        nonlocal clean_capture_count
        clean_capture_count += 1
        assert ffmpeg_executable == "fake-ffmpeg"
        path = tmp_path / f"clean-{clean_capture_count}.wav"
        path.write_bytes(b"RIFF-clean-audio")
        try:
            yield AudioSample(path, duration_seconds, 44_100, 1, path.stat().st_size)
        finally:
            path.unlink()

    @contextmanager
    def capture_system(*, duration_seconds: float) -> Iterator[AudioSample]:
        nonlocal system_capture_count
        system_capture_count += 1
        path = tmp_path / "system.wav"
        path.write_bytes(b"RIFF-system-audio")
        try:
            yield AudioSample(path, duration_seconds, 48_000, 1, path.stat().st_size)
        finally:
            path.unlink()

    service = ListeningService(
        snapshot_reader=snapshot,
        media_selector=select_active_media,
        stream_resolver=lambda _candidate: resolved_stream(),
        capture_factory=capture_clean,
        system_audio_capture_factory=capture_system,
        recognizer=FakeRecognizer([no_match(), no_match(), match()]),
        ffmpeg_executable="fake-ffmpeg",
    )

    outcome = asyncio.run(
        service.listen(
            ListenOptions(
                record_seconds=12,
                retry_count=5,
                use_system_audio_fallback=True,
                fallback_audio_source=FallbackAudioSource.WINDOWS_OUTPUT,
            ),
            progress.append,
        )
    )

    assert outcome.recognition.is_match
    assert outcome.recording_attempts == 3
    assert outcome.used_system_audio_fallback
    assert outcome.notice is not None
    assert "voices" in outcome.notice
    assert clean_capture_count == 2
    assert system_capture_count == 1
    assert [item.stage for item in progress] == [
        ListeningStage.FINDING_PLAYER,
        ListeningStage.RESOLVING_STREAM,
        ListeningStage.RECORDING,
        ListeningStage.RECOGNIZING,
        ListeningStage.RETRYING,
        ListeningStage.RECORDING,
        ListeningStage.RECOGNIZING,
        ListeningStage.RETRYING,
        ListeningStage.RECORDING_SYSTEM_AUDIO,
        ListeningStage.RECOGNIZING,
        ListeningStage.COMPLETE,
    ]
    assert all(item.maximum_attempts == 3 for item in progress)


def test_uses_vrchat_process_audio_without_using_windows_output(tmp_path: Path) -> None:
    clean_capture_count = 0
    vrchat_capture_count = 0
    progress: list[ListeningProgress] = []

    @contextmanager
    def capture_clean(
        _stream: object,
        *,
        duration_seconds: float,
        ffmpeg_executable: str,
    ) -> Iterator[AudioSample]:
        nonlocal clean_capture_count
        clean_capture_count += 1
        path = tmp_path / f"clean-vrchat-{clean_capture_count}.wav"
        path.write_bytes(b"RIFF-clean-audio")
        try:
            yield AudioSample(path, duration_seconds, 44_100, 1, path.stat().st_size)
        finally:
            path.unlink()

    @contextmanager
    def capture_vrchat(*, duration_seconds: float) -> Iterator[AudioSample]:
        nonlocal vrchat_capture_count
        vrchat_capture_count += 1
        path = tmp_path / "vrchat-only.wav"
        path.write_bytes(b"RIFF-vrchat-audio")
        try:
            yield AudioSample(path, duration_seconds, 48_000, 2, path.stat().st_size)
        finally:
            path.unlink()

    def must_not_capture_windows_output(**_kwargs: object) -> object:
        raise AssertionError("VRChat-only mode must not record the complete Windows output")

    service = ListeningService(
        snapshot_reader=snapshot,
        media_selector=select_active_media,
        stream_resolver=lambda _candidate: resolved_stream(),
        capture_factory=capture_clean,
        system_audio_capture_factory=must_not_capture_windows_output,  # type: ignore[arg-type]
        vrchat_audio_capture_factory=capture_vrchat,
        recognizer=FakeRecognizer([no_match(), no_match(), match()]),
        ffmpeg_executable="fake-ffmpeg",
    )

    outcome = asyncio.run(
        service.listen(
            ListenOptions(
                use_system_audio_fallback=True,
                fallback_audio_source=FallbackAudioSource.VRCHAT,
            ),
            progress.append,
        )
    )

    assert outcome.recognition.is_match
    assert outcome.recording_attempts == 3
    assert outcome.used_system_audio_fallback
    assert outcome.used_vrchat_audio_fallback
    assert outcome.fallback_audio_source is FallbackAudioSource.VRCHAT
    assert outcome.notice is not None and "not other applications" in outcome.notice
    assert clean_capture_count == 2
    assert vrchat_capture_count == 1
    assert ListeningStage.RECORDING_VRCHAT_AUDIO in [item.stage for item in progress]
    assert ListeningStage.RECORDING_SYSTEM_AUDIO not in [item.stage for item in progress]


def test_does_not_record_computer_audio_when_second_clean_attempt_matches(
    tmp_path: Path,
) -> None:
    clean_capture_count = 0

    @contextmanager
    def capture_clean(
        _stream: object,
        *,
        duration_seconds: float,
        ffmpeg_executable: str,
    ) -> Iterator[AudioSample]:
        nonlocal clean_capture_count
        clean_capture_count += 1
        path = tmp_path / f"clean-{clean_capture_count}.wav"
        path.write_bytes(b"RIFF-clean-audio")
        try:
            yield AudioSample(path, duration_seconds, 44_100, 1, path.stat().st_size)
        finally:
            path.unlink()

    def must_not_capture_system_audio(**_kwargs: object) -> object:
        raise AssertionError("Computer audio must remain unused after a clean match")

    service = ListeningService(
        snapshot_reader=snapshot,
        media_selector=select_active_media,
        stream_resolver=lambda _candidate: resolved_stream(),
        capture_factory=capture_clean,
        system_audio_capture_factory=must_not_capture_system_audio,  # type: ignore[arg-type]
        recognizer=FakeRecognizer([no_match(), match()]),
        ffmpeg_executable="fake-ffmpeg",
    )

    outcome = asyncio.run(
        service.listen(
            ListenOptions(
                use_system_audio_fallback=True,
                fallback_audio_source=FallbackAudioSource.WINDOWS_OUTPUT,
            )
        )
    )

    assert outcome.recognition.is_match
    assert outcome.recording_attempts == 2
    assert not outcome.used_system_audio_fallback
    assert clean_capture_count == 2


def test_uses_computer_audio_when_long_mix_player_time_is_unavailable(
    tmp_path: Path,
) -> None:
    stream = ResolvedStream(
        original_url="https://www.youtube.com/watch?v=mix",
        stream_url="https://cdn.example/mix.m4a",
        provider=Provider.YOUTUBE,
        transport=Transport.DIRECT_MEDIA,
        playback_type=PlaybackType.PRERECORDED,
        resolution_source=ResolutionSource.YT_DLP,
        duration_seconds=3600,
        metadata=MediaMetadata(title="Long mix", content_kind=ContentKind.LONG_FORM),
    )
    system_capture_count = 0

    def must_not_capture_clean(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("A clean mix sample needs a player-time estimate")

    @contextmanager
    def capture_system(*, duration_seconds: float) -> Iterator[AudioSample]:
        nonlocal system_capture_count
        system_capture_count += 1
        path = tmp_path / "system-mix.wav"
        path.write_bytes(b"RIFF-system-audio")
        try:
            yield AudioSample(path, duration_seconds, 48_000, 1, path.stat().st_size)
        finally:
            path.unlink()

    service = ListeningService(
        snapshot_reader=snapshot,
        media_selector=select_active_media,
        stream_resolver=lambda _candidate: stream,
        capture_factory=must_not_capture_clean,  # type: ignore[arg-type]
        system_audio_capture_factory=capture_system,
        recognizer=FakeRecognizer([match()]),
        position_estimator=lambda _candidate, _stream: None,
    )

    outcome = asyncio.run(
        service.listen(
            ListenOptions(
                use_system_audio_fallback=True,
                fallback_audio_source=FallbackAudioSource.WINDOWS_OUTPUT,
            )
        )
    )

    assert outcome.recognition.is_match
    assert outcome.used_system_audio_fallback
    assert outcome.recording_attempts == 1
    assert outcome.media_title == "Long mix"
    assert system_capture_count == 1


def test_uses_exact_youtube_track_metadata_without_recording(tmp_path: Path) -> None:
    def must_not_capture(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Exact metadata should avoid audio capture")

    stream = ResolvedStream(
        original_url="https://www.youtube.com/watch?v=song",
        stream_url="https://cdn.example/song.m4a",
        provider=Provider.YOUTUBE,
        transport=Transport.DIRECT_MEDIA,
        playback_type=PlaybackType.PRERECORDED,
        resolution_source=ResolutionSource.YT_DLP,
        duration_seconds=240,
        metadata=MediaMetadata(
            title="Upload title",
            track="Digital Love",
            artist="Daft Punk",
            album="Discovery",
            content_kind=ContentKind.SINGLE_TRACK,
        ),
    )
    service = ListeningService(
        snapshot_reader=lambda: LogSnapshot(
            world=WorldInfo(name="Club"),
            media=MediaInfo(original_url=stream.original_url),
        ),
        media_selector=select_active_media,
        stream_resolver=lambda _candidate: stream,
        capture_factory=must_not_capture,  # type: ignore[arg-type]
        recognizer=FakeRecognizer([]),
    )

    outcome = asyncio.run(service.listen(ListenOptions()))

    assert outcome.recording_attempts == 0
    assert outcome.recognition.is_match
    assert outcome.recognition.provider == "youtube_metadata"
    assert outcome.recognition.track is not None
    assert outcome.recognition.track.artist == "Daft Punk"
    assert outcome.recognition.track.title == "Digital Love"


def test_samples_representative_sections_for_short_youtube_song(tmp_path: Path) -> None:
    starts: list[float] = []

    @contextmanager
    def capture(
        _stream: object,
        *,
        duration_seconds: float,
        ffmpeg_executable: str,
        allow_unsupported_playback: bool,
        start_seconds: float,
    ) -> Iterator[AudioSample]:
        assert ffmpeg_executable == "fake-ffmpeg"
        assert allow_unsupported_playback
        starts.append(start_seconds)
        path = tmp_path / f"song-{len(starts)}.wav"
        path.write_bytes(b"RIFF-fake-audio")
        try:
            yield AudioSample(path, duration_seconds, 44_100, 1, path.stat().st_size)
        finally:
            path.unlink()

    stream = ResolvedStream(
        original_url="https://www.youtube.com/watch?v=song",
        stream_url="https://cdn.example/song.m4a",
        provider=Provider.YOUTUBE,
        transport=Transport.DIRECT_MEDIA,
        playback_type=PlaybackType.PRERECORDED,
        resolution_source=ResolutionSource.YT_DLP,
        duration_seconds=240,
        metadata=MediaMetadata(
            title="Song upload",
            content_kind=ContentKind.SINGLE_TRACK,
        ),
    )
    service = ListeningService(
        snapshot_reader=snapshot,
        media_selector=select_active_media,
        stream_resolver=lambda _candidate: stream,
        capture_factory=capture,
        recognizer=FakeRecognizer([no_match(), match()]),
        ffmpeg_executable="fake-ffmpeg",
    )

    outcome = asyncio.run(service.listen(ListenOptions(record_seconds=12, retry_count=1)))

    assert outcome.recognition.is_match
    assert starts == pytest.approx([48, 108])


def test_seeks_to_estimated_current_position_for_long_mix(tmp_path: Path) -> None:
    starts: list[float] = []
    progress: list[ListeningProgress] = []

    @contextmanager
    def capture(
        _stream: object,
        *,
        duration_seconds: float,
        ffmpeg_executable: str,
        allow_unsupported_playback: bool,
        start_seconds: float,
    ) -> Iterator[AudioSample]:
        assert ffmpeg_executable == "fake-ffmpeg"
        assert allow_unsupported_playback
        starts.append(start_seconds)
        path = tmp_path / "mix.wav"
        path.write_bytes(b"RIFF-fake-audio")
        try:
            yield AudioSample(path, duration_seconds, 44_100, 1, path.stat().st_size)
        finally:
            path.unlink()

    stream = ResolvedStream(
        original_url="https://www.youtube.com/watch?v=mix",
        stream_url="https://cdn.example/mix.m4a",
        provider=Provider.YOUTUBE,
        transport=Transport.DIRECT_MEDIA,
        playback_type=PlaybackType.PRERECORDED,
        resolution_source=ResolutionSource.YT_DLP,
        duration_seconds=3600,
        metadata=MediaMetadata(title="Long mix", content_kind=ContentKind.LONG_FORM),
    )
    estimate = PlaybackPositionEstimate(
        seconds=125,
        source=PositionSource.AVPRO_OPEN,
        initial_offset_seconds=0,
        elapsed_seconds=125,
        unclamped_seconds=125,
    )
    service = ListeningService(
        snapshot_reader=snapshot,
        media_selector=select_active_media,
        stream_resolver=lambda _candidate: stream,
        capture_factory=capture,
        recognizer=FakeRecognizer([match()]),
        ffmpeg_executable="fake-ffmpeg",
        position_estimator=lambda _candidate, _stream: estimate,
    )

    outcome = asyncio.run(service.listen(ListenOptions(), progress.append))

    assert outcome.recognition.is_match
    assert starts == [125]
    assert outcome.media_title == "Long mix"
    recording = next(item for item in progress if item.stage is ListeningStage.RECORDING)
    assert "estimated position 02:05" in recording.message
    assert "low confidence" in recording.message


def test_logs_whole_mix_when_vrchat_player_time_is_unavailable() -> None:
    stream = ResolvedStream(
        original_url="https://www.youtube.com/watch?v=mix",
        stream_url="https://cdn.example/mix.m4a",
        provider=Provider.YOUTUBE,
        transport=Transport.DIRECT_MEDIA,
        playback_type=PlaybackType.PRERECORDED,
        resolution_source=ResolutionSource.YT_DLP,
        duration_seconds=3600,
        metadata=MediaMetadata(title="Long mix", content_kind=ContentKind.LONG_FORM),
    )

    def must_not_capture(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Capture must not start without a player-time estimate")

    service = ListeningService(
        snapshot_reader=snapshot,
        media_selector=select_active_media,
        stream_resolver=lambda _candidate: stream,
        capture_factory=must_not_capture,  # type: ignore[arg-type]
        recognizer=FakeRecognizer([]),
        position_estimator=lambda _candidate, _stream: None,
    )

    outcome = asyncio.run(service.listen(ListenOptions()))

    assert outcome.result_kind is ListeningResultKind.MIX
    assert outcome.media_title == "Long mix"
    assert outcome.recording_attempts == 0
    assert outcome.recognition.track is None
    assert outcome.notice is not None
    assert "playback position in this mix is unknown" in outcome.notice


def test_player_debug_service_resolves_only_when_log_media_changes() -> None:
    reads = 0
    resolutions = 0

    def read_snapshot() -> LogSnapshot:
        nonlocal reads
        reads += 1
        return LogSnapshot(
            world=WorldInfo(name="Club"),
            media=MediaInfo(
                original_url="https://www.youtube.com/watch?v=mix",
                opened_at="2026-08-21T10:00:00",
            ),
        )

    def resolve(_candidate: MediaCandidate) -> ResolvedStream:
        nonlocal resolutions
        resolutions += 1
        return resolved_stream()

    inspector = PlayerDebugService(
        snapshot_reader=read_snapshot,
        media_selector=select_active_media,
        stream_resolver=resolve,
        position_estimator=lambda _candidate, _stream: None,
    )

    first = inspector.inspect()
    second = inspector.inspect()

    assert first.world.name == "Club"
    assert second.stream is first.stream
    assert reads == 2
    assert resolutions == 1


def test_retained_debug_sample_replaces_previous_file_and_can_be_deleted(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.wav"
    destination = tmp_path / "debug" / "last-sample.wav"
    destination.parent.mkdir()
    destination.write_bytes(b"old audio")
    source.write_bytes(b"new audio")

    assert retain_last_sample(source, destination) == destination
    assert destination.read_bytes() == b"new audio"
    assert not destination.with_suffix(".tmp").exists()

    delete_last_sample(destination)
    assert not destination.exists()
    delete_last_sample(destination)


def test_keeps_only_public_provider_pages_as_shareable_source_links() -> None:
    twitch = MediaCandidate(
        original_url="https://www.twitch.tv/example",
        resolved_url="https://signed.ttvnw.net/live.m3u8?token=private",
        player_type="AVPro",
        requested_at=None,
        resolved_at=None,
        opened_at=None,
        opened_offset_seconds=None,
        reason=SelectionReason.LATEST_AVPRO_OPEN,
    )

    assert _shareable_source_url(twitch, Provider.TWITCH) == twitch.original_url
    assert _shareable_source_url(twitch, Provider.DIRECT) is None


@pytest.mark.parametrize(
    "options",
    [
        {"record_seconds": 0},
        {"record_seconds": float("nan")},
        {"retry_count": -1},
        {"retry_count": 6},
        {"use_system_audio_fallback": 1},
        {"fallback_audio_source": "microphone"},
        {"keep_last_sample": 1},
    ],
)
def test_listen_options_reject_invalid_values(options: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ListenOptions(**options)  # type: ignore[arg-type]
