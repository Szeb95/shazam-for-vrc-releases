"""Music-recognition providers and normalized results."""

from shazam_for_vrc.recognition.models import (
    RecognitionResult,
    RecognitionStatus,
    RecognizedTrack,
)
from shazam_for_vrc.recognition.provider import RecognitionProvider
from shazam_for_vrc.recognition.shazam import (
    InvalidAudioSampleError,
    InvalidShazamResponseError,
    RecognitionBusyError,
    ShazamDependencyError,
    ShazamNetworkError,
    ShazamRecognitionError,
    ShazamRecognizer,
    ShazamServiceError,
    ShazamTimeoutError,
    ShazamUnavailableError,
    ShazamUnexpectedResponseError,
)

__all__ = [
    "InvalidAudioSampleError",
    "InvalidShazamResponseError",
    "RecognitionBusyError",
    "RecognitionProvider",
    "RecognitionResult",
    "RecognitionStatus",
    "RecognizedTrack",
    "ShazamDependencyError",
    "ShazamNetworkError",
    "ShazamRecognitionError",
    "ShazamRecognizer",
    "ShazamServiceError",
    "ShazamTimeoutError",
    "ShazamUnavailableError",
    "ShazamUnexpectedResponseError",
]
