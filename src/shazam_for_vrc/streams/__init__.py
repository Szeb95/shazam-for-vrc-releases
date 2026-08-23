"""Media URL selection, resolution, and clean audio capture."""

from shazam_for_vrc.streams.audio_capture import AudioSample, capture_audio
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
from shazam_for_vrc.streams.stream_detector import (
    PlaybackType,
    Provider,
    Transport,
    classify_url,
)
from shazam_for_vrc.streams.system_audio_capture import (
    SystemAudioCaptureError,
    capture_system_audio,
)

__all__ = [
    "AudioSample",
    "ContentKind",
    "MediaMetadata",
    "PlaybackType",
    "PlaybackPositionEstimate",
    "Provider",
    "ResolvedStream",
    "SystemAudioCaptureError",
    "Transport",
    "capture_audio",
    "capture_system_audio",
    "classify_url",
    "estimate_playback_position",
    "format_media_time",
    "resolve_stream",
]
