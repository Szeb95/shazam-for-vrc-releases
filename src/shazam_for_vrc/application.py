"""Reusable orchestration for one on-demand listening operation."""

from __future__ import annotations

import math
import shutil
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from platformdirs import user_data_path

from shazam_for_vrc.recognition import (
    RecognitionProvider,
    RecognitionResult,
    RecognitionStatus,
    RecognizedTrack,
    ShazamRecognizer,
)
from shazam_for_vrc.streams.audio_capture import (
    AudioSample,
    UnsupportedPlaybackError,
    capture_audio,
)
from shazam_for_vrc.streams.playback_position import (
    PlaybackPositionEstimate,
    estimate_playback_position,
    format_media_time,
)
from shazam_for_vrc.streams.resolver import (
    ContentKind,
    MediaMetadata,
    ResolvedStream,
    resolve_stream,
)
from shazam_for_vrc.streams.stream_detector import PlaybackType, Provider, classify_url
from shazam_for_vrc.streams.system_audio_capture import capture_system_audio
from shazam_for_vrc.vrchat.log_reader import LogSnapshot, WorldInfo, read_current_state
from shazam_for_vrc.vrchat.player_tracker import MediaCandidate, select_active_media

APP_NAME = "Shazam for VRC"
MAX_FRESH_RETRIES = 5


class ListeningStage(StrEnum):
    """Stable progress stages that later inputs and outputs can also consume."""

    FINDING_PLAYER = "finding_player"
    RESOLVING_STREAM = "resolving_stream"
    RECORDING = "recording"
    RECORDING_SYSTEM_AUDIO = "recording_system_audio"
    RECOGNIZING = "recognizing"
    RETRYING = "retrying"
    COMPLETE = "complete"


class ListeningResultKind(StrEnum):
    """User-facing category for one completed listening operation."""

    TRACK = "track"
    MIX = "mix"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class ListeningProgress:
    """One user-facing update emitted during listening."""

    stage: ListeningStage
    message: str
    attempt: int = 1
    maximum_attempts: int = 1


@dataclass(frozen=True, slots=True)
class ListenOptions:
    """Settings that affect a single listening operation."""

    record_seconds: float = 12.0
    retry_count: int = 1
    use_system_audio_fallback: bool = False
    keep_last_sample: bool = False
    debug_sample_path: Path | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.record_seconds, bool)
            or not isinstance(self.record_seconds, (int, float))
            or not math.isfinite(float(self.record_seconds))
            or float(self.record_seconds) <= 0
        ):
            raise ValueError("record_seconds must be a finite, positive number")
        if (
            isinstance(self.retry_count, bool)
            or not isinstance(self.retry_count, int)
            or not 0 <= self.retry_count <= MAX_FRESH_RETRIES
        ):
            raise ValueError(f"retry_count must be between 0 and {MAX_FRESH_RETRIES}")
        if not isinstance(self.keep_last_sample, bool):
            raise ValueError("keep_last_sample must be true or false")
        if not isinstance(self.use_system_audio_fallback, bool):
            raise ValueError("use_system_audio_fallback must be true or false")
        if self.debug_sample_path is not None and not isinstance(
            self.debug_sample_path,
            Path,
        ):
            raise ValueError("debug_sample_path must be a pathlib.Path or None")


@dataclass(frozen=True, slots=True)
class ListeningOutcome:
    """A recognition result paired with its world and workflow metadata."""

    recognition: RecognitionResult
    world: WorldInfo
    recording_attempts: int
    retained_sample_path: Path | None = None
    provider: Provider = Provider.UNKNOWN
    source_url: str | None = None
    result_kind: ListeningResultKind = ListeningResultKind.TRACK
    media_title: str | None = None
    notice: str | None = None
    used_system_audio_fallback: bool = False


class DebugSampleError(RuntimeError):
    """Raised when an explicitly requested debug sample cannot be managed."""


class PlayerTimeUnavailableError(UnsupportedPlaybackError):
    """Raised when VRChat exposes a video URL but not its current playhead time."""


SnapshotReader = Callable[[], LogSnapshot]
MediaSelector = Callable[[LogSnapshot], MediaCandidate]
StreamResolver = Callable[[MediaCandidate], ResolvedStream]
CaptureFactory = Callable[..., AbstractContextManager[AudioSample]]
SystemAudioCaptureFactory = Callable[..., AbstractContextManager[AudioSample]]
ProgressCallback = Callable[[ListeningProgress], None]
SampleRetainer = Callable[[Path, Path | None], Path]
PositionEstimator = Callable[[MediaCandidate, ResolvedStream], PlaybackPositionEstimate | None]


@dataclass(frozen=True, slots=True)
class PlayerDebugInfo:
    """Safe current-player details for the desktop debug view."""

    world: WorldInfo
    stream: ResolvedStream
    candidate: MediaCandidate = field(repr=False)
    position: PlaybackPositionEstimate | None = None

    @property
    def current_item(self) -> str:
        """Return the best available human-readable item at the estimate."""

        metadata = self.stream.metadata
        if metadata is None:
            return "Provider metadata unavailable"
        if metadata.track and metadata.artist:
            return f"{metadata.artist} — {metadata.track}"
        if self.position is not None:
            for chapter in metadata.chapters:
                if chapter.start_seconds <= self.position.seconds and (
                    chapter.end_seconds is None or self.position.seconds < chapter.end_seconds
                ):
                    return chapter.title
        return metadata.title or "Provider metadata unavailable"


class PlayerDebugService:
    """Inspect log state repeatedly while resolving only changed media URLs."""

    def __init__(
        self,
        *,
        snapshot_reader: SnapshotReader = read_current_state,
        media_selector: MediaSelector = select_active_media,
        stream_resolver: StreamResolver = resolve_stream,
        position_estimator: PositionEstimator = estimate_playback_position,
    ) -> None:
        self._snapshot_reader = snapshot_reader
        self._media_selector = media_selector
        self._stream_resolver = stream_resolver
        self._position_estimator = position_estimator
        self._cached_key: tuple[str | None, str | None, str | None] | None = None
        self._cached_stream: ResolvedStream | None = None

    def inspect(self) -> PlayerDebugInfo:
        """Read current log state and return safe display information."""

        snapshot = self._snapshot_reader()
        candidate = self._media_selector(snapshot)
        cache_key = (
            candidate.original_url,
            candidate.resolved_url,
            candidate.opened_at,
        )
        if cache_key != self._cached_key or self._cached_stream is None:
            self._cached_stream = self._stream_resolver(candidate)
            self._cached_key = cache_key
        stream = self._cached_stream
        return PlayerDebugInfo(
            world=snapshot.world,
            stream=stream,
            candidate=candidate,
            position=self._position_estimator(candidate, stream),
        )


def default_debug_sample_path() -> Path:
    """Return the single per-user WAV path used by the opt-in debug feature."""

    return user_data_path(APP_NAME, appauthor=False) / "debug" / "last-sample.wav"


def retain_last_sample(source: Path, destination: Path | None = None) -> Path:
    """Replace the previous opt-in debug sample with the current temporary WAV."""

    target = destination or default_debug_sample_path()
    temporary_target = target.with_suffix(".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, temporary_target)
        temporary_target.replace(target)
    except OSError as error:
        with suppress(OSError):
            temporary_target.unlink(missing_ok=True)
        raise DebugSampleError("The latest debug recording could not be saved.") from error
    return target


def delete_last_sample(path: Path | None = None) -> None:
    """Delete the retained debug sample if it exists."""

    target = path or default_debug_sample_path()
    try:
        target.unlink(missing_ok=True)
    except OSError as error:
        raise DebugSampleError("The latest debug recording could not be deleted.") from error


class ListeningService:
    """Run the clean-stream pipeline without depending on a GUI or input device."""

    def __init__(
        self,
        *,
        snapshot_reader: SnapshotReader = read_current_state,
        media_selector: MediaSelector = select_active_media,
        stream_resolver: StreamResolver = resolve_stream,
        capture_factory: CaptureFactory = capture_audio,
        system_audio_capture_factory: SystemAudioCaptureFactory = capture_system_audio,
        recognizer: RecognitionProvider | None = None,
        sample_retainer: SampleRetainer = retain_last_sample,
        ffmpeg_executable: str | None = None,
        position_estimator: PositionEstimator = estimate_playback_position,
    ) -> None:
        self._snapshot_reader = snapshot_reader
        self._media_selector = media_selector
        self._stream_resolver = stream_resolver
        self._capture_factory = capture_factory
        self._system_audio_capture_factory = system_audio_capture_factory
        self._sample_retainer = sample_retainer
        self._ffmpeg_executable = ffmpeg_executable or _default_ffmpeg_executable()
        self._recognizer = recognizer or ShazamRecognizer(ffmpeg_executable=self._ffmpeg_executable)
        self._position_estimator = position_estimator

    async def listen(
        self,
        options: ListenOptions,
        progress_callback: ProgressCallback | None = None,
    ) -> ListeningOutcome:
        """Capture and recognize current audio, retrying with fresh samples on no match."""

        clean_attempts = 2 if options.use_system_audio_fallback else options.retry_count + 1
        maximum_attempts = clean_attempts + int(options.use_system_audio_fallback)
        _emit(
            progress_callback,
            ListeningStage.FINDING_PLAYER,
            "Finding the active VRChat player...",
            maximum_attempts=maximum_attempts,
        )
        snapshot = self._snapshot_reader()
        candidate = self._media_selector(snapshot)

        _emit(
            progress_callback,
            ListeningStage.RESOLVING_STREAM,
            "Resolving the player stream...",
            maximum_attempts=maximum_attempts,
        )
        stream = self._stream_resolver(candidate)
        source_url = _shareable_source_url(candidate, stream.provider)
        retained_sample: Path | None = None

        mix_title = _unpositioned_mix_title(
            stream,
            candidate,
            position_estimator=self._position_estimator,
        )
        if mix_title and not options.use_system_audio_fallback:
            notice = (
                "The current track cannot be identified because the playback position "
                "in this mix is unknown."
            )
            _emit(
                progress_callback,
                ListeningStage.COMPLETE,
                "The complete mix was added because its current playback position is unknown.",
                maximum_attempts=maximum_attempts,
            )
            return ListeningOutcome(
                recognition=RecognitionResult(
                    status=RecognitionStatus.NO_MATCH,
                    track=None,
                    provider="media_metadata",
                    attempt_count=1,
                ),
                world=snapshot.world,
                recording_attempts=0,
                provider=stream.provider,
                source_url=source_url,
                result_kind=ListeningResultKind.MIX,
                media_title=mix_title,
                notice=notice,
            )

        if metadata_result := _exact_metadata_result(stream):
            _emit(
                progress_callback,
                ListeningStage.COMPLETE,
                "Track identified from the media provider metadata.",
                maximum_attempts=maximum_attempts,
            )
            return ListeningOutcome(
                recognition=metadata_result,
                world=snapshot.world,
                recording_attempts=0,
                provider=stream.provider,
                source_url=source_url,
                result_kind=_result_kind(stream),
                media_title=_identified_mix_title(stream),
            )

        if mix_title:
            _emit(
                progress_callback,
                ListeningStage.RETRYING,
                "The clean stream cannot be matched to the player's current time. Using the "
                "explicit computer-audio fallback now...",
                attempt=maximum_attempts,
                maximum_attempts=maximum_attempts,
            )
            return await self._listen_with_system_audio(
                options,
                snapshot=snapshot,
                stream=stream,
                source_url=source_url,
                progress_callback=progress_callback,
                attempt=maximum_attempts,
                maximum_attempts=maximum_attempts,
                prior_recordings=0,
                retained_sample=retained_sample,
                unpositioned_mix_title=mix_title,
            )

        for attempt in range(1, clean_attempts + 1):
            capture_arguments: dict[str, object] = {
                "duration_seconds": options.record_seconds,
                "ffmpeg_executable": self._ffmpeg_executable,
            }
            recording_location = ""
            if stream.playback_type is PlaybackType.PRERECORDED:
                start_seconds, location_label = _prerecorded_capture_start(
                    stream,
                    candidate,
                    attempt=attempt,
                    duration_seconds=options.record_seconds,
                    position_estimator=self._position_estimator,
                )
                capture_arguments.update(
                    {
                        "allow_unsupported_playback": True,
                        "start_seconds": start_seconds,
                    }
                )
                recording_location = f" at {location_label}"
            _emit(
                progress_callback,
                ListeningStage.RECORDING,
                f"Recording {float(options.record_seconds):g} seconds{recording_location} "
                f"(attempt {attempt}/{maximum_attempts})...",
                attempt=attempt,
                maximum_attempts=maximum_attempts,
            )
            with self._capture_factory(
                stream,
                **capture_arguments,
            ) as sample:
                if options.keep_last_sample:
                    retained_sample = self._sample_retainer(
                        sample.path,
                        options.debug_sample_path,
                    )
                _emit(
                    progress_callback,
                    ListeningStage.RECOGNIZING,
                    f"Recognizing music (attempt {attempt}/{maximum_attempts})...",
                    attempt=attempt,
                    maximum_attempts=maximum_attempts,
                )
                result = await self._recognizer.recognize(sample.path)

            if result.is_match or (
                attempt == clean_attempts and not options.use_system_audio_fallback
            ):
                message = (
                    "Song recognized."
                    if result.is_match
                    else f"No song recognized after {clean_attempts} recording attempt(s)."
                )
                _emit(
                    progress_callback,
                    ListeningStage.COMPLETE,
                    message,
                    attempt=attempt,
                    maximum_attempts=maximum_attempts,
                )
                return ListeningOutcome(
                    recognition=result,
                    world=snapshot.world,
                    recording_attempts=attempt,
                    retained_sample_path=retained_sample,
                    provider=stream.provider,
                    source_url=source_url,
                    result_kind=_result_kind(stream),
                    media_title=_identified_mix_title(stream),
                )

            _emit(
                progress_callback,
                ListeningStage.RETRYING,
                (
                    "No clean-stream match after two attempts. Preparing the final "
                    "computer-audio attempt..."
                    if attempt == clean_attempts
                    else "No match. Preparing a fresh clean-stream recording..."
                ),
                attempt=attempt,
                maximum_attempts=maximum_attempts,
            )

        return await self._listen_with_system_audio(
            options,
            snapshot=snapshot,
            stream=stream,
            source_url=source_url,
            progress_callback=progress_callback,
            attempt=maximum_attempts,
            maximum_attempts=maximum_attempts,
            prior_recordings=clean_attempts,
            retained_sample=retained_sample,
        )

    async def _listen_with_system_audio(
        self,
        options: ListenOptions,
        *,
        snapshot: LogSnapshot,
        stream: ResolvedStream,
        source_url: str | None,
        progress_callback: ProgressCallback | None,
        attempt: int,
        maximum_attempts: int,
        prior_recordings: int,
        retained_sample: Path | None,
        unpositioned_mix_title: str | None = None,
    ) -> ListeningOutcome:
        """Run the single explicit final attempt against Windows output audio."""

        _emit(
            progress_callback,
            ListeningStage.RECORDING_SYSTEM_AUDIO,
            f"Recording {float(options.record_seconds):g} seconds from the default Windows "
            f"output (final attempt {attempt}/{maximum_attempts})...",
            attempt=attempt,
            maximum_attempts=maximum_attempts,
        )
        with self._system_audio_capture_factory(
            duration_seconds=options.record_seconds,
        ) as sample:
            if options.keep_last_sample:
                retained_sample = self._sample_retainer(
                    sample.path,
                    options.debug_sample_path,
                )
            _emit(
                progress_callback,
                ListeningStage.RECOGNIZING,
                f"Recognizing computer audio (final attempt {attempt}/{maximum_attempts})...",
                attempt=attempt,
                maximum_attempts=maximum_attempts,
            )
            result = await self._recognizer.recognize(sample.path)

        recording_attempts = prior_recordings + 1
        message = (
            "Song recognized from the final computer-audio attempt."
            if result.is_match
            else f"No song recognized after {recording_attempts} recording attempt(s)."
        )
        _emit(
            progress_callback,
            ListeningStage.COMPLETE,
            message,
            attempt=attempt,
            maximum_attempts=maximum_attempts,
        )
        notice = (
            "Recognition used the final computer-audio fallback. That recording may include "
            "VRChat voices, world sounds, and audio from other applications."
        )
        result_kind = _result_kind(stream)
        media_title = _identified_mix_title(stream)
        if unpositioned_mix_title and not result.is_match:
            result_kind = ListeningResultKind.MIX
            media_title = unpositioned_mix_title
            notice = (
                "VRChat did not expose the mix position, and the computer-audio fallback also "
                "returned no track match."
            )
        return ListeningOutcome(
            recognition=result,
            world=snapshot.world,
            recording_attempts=recording_attempts,
            retained_sample_path=retained_sample,
            provider=stream.provider,
            source_url=source_url,
            result_kind=result_kind,
            media_title=media_title,
            notice=notice,
            used_system_audio_fallback=True,
        )


def _exact_metadata_result(stream: ResolvedStream) -> RecognitionResult | None:
    metadata = stream.metadata
    if (
        stream.provider is not Provider.YOUTUBE
        or stream.playback_type is not PlaybackType.PRERECORDED
        or metadata is None
        or not metadata.track
        or not metadata.artist
    ):
        return None
    return RecognitionResult(
        status=RecognitionStatus.MATCHED,
        track=RecognizedTrack(
            title=metadata.track,
            artist=metadata.artist,
            album=metadata.album,
        ),
        provider="youtube_metadata",
        attempt_count=1,
    )


def _unpositioned_mix_title(
    stream: ResolvedStream,
    candidate: MediaCandidate,
    *,
    position_estimator: PositionEstimator,
) -> str | None:
    """Return the whole-mix title when its current playhead cannot be estimated."""

    metadata = stream.metadata
    if (
        stream.provider is not Provider.YOUTUBE
        or stream.playback_type is not PlaybackType.PRERECORDED
        or metadata is None
        or metadata.content_kind is not ContentKind.LONG_FORM
        or position_estimator(candidate, stream) is not None
    ):
        return None
    return metadata.title or "YouTube mix"


def _result_kind(stream: ResolvedStream) -> ListeningResultKind:
    if stream.playback_type is PlaybackType.LIVE:
        return ListeningResultKind.LIVE
    return ListeningResultKind.TRACK


def _identified_mix_title(stream: ResolvedStream) -> str | None:
    """Return a YouTube mix title without changing a recognized track result."""

    metadata = stream.metadata
    if (
        stream.provider is Provider.YOUTUBE
        and metadata is not None
        and metadata.content_kind is ContentKind.LONG_FORM
    ):
        return metadata.title
    return None


def _prerecorded_capture_start(
    stream: ResolvedStream,
    candidate: MediaCandidate,
    *,
    attempt: int,
    duration_seconds: float,
    position_estimator: PositionEstimator,
) -> tuple[float, str]:
    metadata = stream.metadata or MediaMetadata()
    if metadata.content_kind is ContentKind.SINGLE_TRACK:
        if stream.duration_seconds is None:
            raise UnsupportedPlaybackError(
                "This prerecorded song has no reported duration, so a safe sample section "
                "could not be selected automatically."
            )
        start = _representative_song_position(
            stream.duration_seconds,
            duration_seconds,
            attempt,
        )
        return start, f"representative song section {format_media_time(start)}"

    estimate = position_estimator(candidate, stream)
    if estimate is None:
        raise PlayerTimeUnavailableError(
            "Player time is unavailable for this YouTube mix. VRChat reported the video, "
            "but this player did not expose which minute and second is currently playing. "
            "Without an AVPro open time or a start time in the URL, Shazam for VRC cannot "
            "sample the matching part of the mix. Open 'Check current player' after the "
            "video reloads to see whether an estimate becomes available."
        )
    start = _clamp_capture_start(
        estimate.seconds,
        stream.duration_seconds,
        duration_seconds,
    )
    qualifier = "estimated position"
    if estimate.past_reported_duration:
        qualifier = "estimated loop position"
    return start, f"{qualifier} {format_media_time(start)} (low confidence)"


def _representative_song_position(
    media_duration_seconds: float,
    sample_duration_seconds: float,
    attempt: int,
) -> float:
    ratios = (0.20, 0.45, 0.70, 0.30, 0.60, 0.80)
    ratio = ratios[(attempt - 1) % len(ratios)]
    preferred = max(15.0, media_duration_seconds * ratio)
    return _clamp_capture_start(
        preferred,
        media_duration_seconds,
        sample_duration_seconds,
    )


def _clamp_capture_start(
    requested_seconds: float,
    media_duration_seconds: float | None,
    sample_duration_seconds: float,
) -> float:
    start = max(0.0, requested_seconds)
    if media_duration_seconds is None:
        return start
    latest_start = max(0.0, media_duration_seconds - sample_duration_seconds)
    return min(start, latest_start)


def _emit(
    callback: ProgressCallback | None,
    stage: ListeningStage,
    message: str,
    *,
    attempt: int = 1,
    maximum_attempts: int = 1,
) -> None:
    if callback is not None:
        callback(
            ListeningProgress(
                stage=stage,
                message=message,
                attempt=attempt,
                maximum_attempts=maximum_attempts,
            )
        )


def _default_ffmpeg_executable() -> str:
    """Prefer FFmpeg bundled by PyInstaller, otherwise use the system PATH."""

    bundle_root_value = getattr(sys, "_MEIPASS", None)
    if bundle_root_value:
        bundled = Path(bundle_root_value) / "ffmpeg.exe"
        if bundled.is_file():
            return str(bundled)
    executable_sibling = Path(sys.executable).resolve().parent / "ffmpeg.exe"
    if getattr(sys, "frozen", False) and executable_sibling.is_file():
        return str(executable_sibling)
    return "ffmpeg"


def _shareable_source_url(
    candidate: MediaCandidate,
    provider: Provider,
) -> str | None:
    """Keep public provider pages, never signed direct stream URLs."""

    if provider not in {Provider.TWITCH, Provider.YOUTUBE}:
        return None
    url = candidate.original_url
    if not url:
        return None
    try:
        if classify_url(url).provider is provider:
            return url
    except ValueError:
        pass
    return None
