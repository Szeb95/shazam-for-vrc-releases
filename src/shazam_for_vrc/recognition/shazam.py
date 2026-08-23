"""ShazamIO-backed music recognition with normalized, bounded behavior."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from shazam_for_vrc.recognition.models import (
    RecognitionResult,
    RecognitionStatus,
    RecognizedTrack,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_NO_MATCH_RETRIES = 1
PROVIDER_NAME = "shazam"


class ShazamRecognitionError(RuntimeError):
    """Base error for failures while recognizing audio with Shazam."""


class InvalidAudioSampleError(ShazamRecognitionError):
    """Raised when the supplied audio path is missing or empty."""


class ShazamUnavailableError(ShazamRecognitionError):
    """Raised when ShazamIO is not installed or cannot be loaded."""


class RecognitionBusyError(ShazamRecognitionError):
    """Raised when recognition is already running on this provider instance."""


class ShazamTimeoutError(ShazamRecognitionError):
    """Raised when the complete recognition operation exceeds its deadline."""


class ShazamServiceError(ShazamRecognitionError):
    """Raised when ShazamIO or the remote recognition service fails."""


class ShazamNetworkError(ShazamServiceError):
    """Raised when the Shazam service cannot be reached from this computer."""


class ShazamDependencyError(ShazamServiceError):
    """Raised when a bundled recognition component cannot be started."""


class ShazamUnexpectedResponseError(ShazamServiceError):
    """Raised when the Shazam endpoint returns a non-recognition response."""


class InvalidShazamResponseError(ShazamRecognitionError):
    """Raised when Shazam returns a response that cannot be normalized safely."""


class ShazamClient(Protocol):
    """Small portion of ShazamIO used by this adapter."""

    async def recognize(self, data: str | bytes | bytearray) -> Mapping[str, Any]:
        """Recognize audio from a path or encoded byte sequence."""

        ...

    async def recognize_song(self, data: Any) -> Mapping[str, Any]:
        """Recognize audio with ShazamIO's legacy fingerprint generator."""

        ...


class ShazamRecognizer:
    """Recognize temporary samples through ShazamIO.

    One instance permits only one in-flight operation. A no-match response may
    fall back once to ShazamIO's legacy fingerprint generator, which currently
    succeeds for some audio missed by its newer Rust implementation. Service
    failures are returned immediately. The timeout covers all attempts, including
    ShazamIO's own internal HTTP retries.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        no_match_retries: int = DEFAULT_NO_MATCH_RETRIES,
        language: str = "en-US",
        endpoint_country: str = "GB",
        client: ShazamClient | None = None,
        ffmpeg_executable: str = "ffmpeg",
    ) -> None:
        self._timeout_seconds = _positive_timeout(timeout_seconds)
        self._no_match_retries = _retry_count(no_match_retries)
        self._language = language
        self._endpoint_country = endpoint_country
        self._client = client
        self._ffmpeg_executable = ffmpeg_executable
        self._request_gate = asyncio.Lock()

    async def recognize(self, sample_path: Path) -> RecognitionResult:
        """Recognize ``sample_path`` and return provider-independent metadata."""

        path = _validate_sample(sample_path)
        if self._request_gate.locked():
            raise RecognitionBusyError("Music recognition is already in progress.")

        async with self._request_gate:
            try:
                return await asyncio.wait_for(
                    self._recognize_with_retry(path),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError:
                raise ShazamTimeoutError("Shazam recognition exceeded its total timeout.") from None

    async def _recognize_with_retry(self, sample_path: Path) -> RecognitionResult:
        client = self._client_or_default()
        maximum_attempts = 1 + self._no_match_retries

        for attempt in range(1, maximum_attempts + 1):
            try:
                response = await self._recognize_attempt(
                    client,
                    sample_path,
                    use_legacy_fingerprint=attempt > 1,
                )
            except TimeoutError:
                raise
            except Exception as error:
                translated = _translate_service_error(error)
                logger.warning(
                    "Shazam recognition failed (%s; %s).",
                    type(error).__name__,
                    type(translated).__name__,
                )
                raise translated from error

            result = _normalize_response(response, attempt_count=attempt)
            if result.is_match or attempt == maximum_attempts:
                return result
            logger.info(
                "Shazam returned no match; retrying once with the alternate fingerprint method."
            )

        raise AssertionError("The recognition attempt loop must always return")

    async def _recognize_attempt(
        self,
        client: ShazamClient,
        sample_path: Path,
        *,
        use_legacy_fingerprint: bool,
    ) -> Mapping[str, Any]:
        if not use_legacy_fingerprint:
            return await client.recognize(str(sample_path))

        # ShazamIO deprecates this API, but its replacement has a documented
        # false-negative regression. Keep this bounded fallback until upstream
        # recognition behavior converges, and contain the deprecation warning here.
        # Passing a decoded WAV also keeps ShazamIO from launching visible FFmpeg
        # and FFprobe console processes on Windows.
        with warnings.catch_warnings(record=True):
            legacy_audio = _load_legacy_audio(sample_path)
            return await client.recognize_song(legacy_audio)

    def _client_or_default(self) -> ShazamClient:
        if self._client is None:
            _configure_media_tools(self._ffmpeg_executable)
            self._client = _load_default_client(
                language=self._language,
                endpoint_country=self._endpoint_country,
            )
        return self._client


def _load_default_client(*, language: str, endpoint_country: str) -> ShazamClient:
    try:
        from shazamio import Shazam
    except (ImportError, OSError) as error:
        raise ShazamUnavailableError(
            "ShazamIO is unavailable. Reinstall the application's runtime dependencies."
        ) from error
    return Shazam(language=language, endpoint_country=endpoint_country)


def _configure_media_tools(ffmpeg_executable: str) -> None:
    """Make a bundled FFmpeg/FFprobe pair visible to ShazamIO's legacy fallback."""

    executable = Path(ffmpeg_executable)
    if executable.parent == Path("."):
        return

    directory = str(executable.parent)
    current_path = os.environ.get("PATH", "")
    path_entries = [entry for entry in current_path.split(os.pathsep) if entry]
    normalized_directory = os.path.normcase(os.path.abspath(directory))
    if not any(
        os.path.normcase(os.path.abspath(entry)) == normalized_directory
        for entry in path_entries
    ):
        os.environ["PATH"] = os.pathsep.join([directory, *path_entries])

    try:
        from pydub import AudioSegment
    except (ImportError, OSError):
        return
    AudioSegment.converter = ffmpeg_executable


def _load_legacy_audio(sample_path: Path) -> Any:
    """Decode the capture as WAV in-process for ShazamIO's legacy recognizer."""

    try:
        from pydub import AudioSegment
    except (ImportError, OSError) as error:
        raise ShazamUnavailableError(
            "ShazamIO's audio decoder is unavailable. Reinstall the application."
        ) from error
    return AudioSegment.from_wav(sample_path)


def _translate_service_error(error: Exception) -> ShazamServiceError:
    names = {type(item).__name__ for item in _exception_chain(error)}

    if "FileNotFoundError" in names:
        return ShazamDependencyError(
            "A bundled recognition tool could not start. Install the latest Shazam for VRC "
            "update and try again. (code: media-tool-missing)"
        )
    if names.intersection(
        {
            "ClientConnectorCertificateError",
            "ClientConnectorSSLError",
            "ClientSSLError",
            "SSLCertVerificationError",
            "SSLError",
        }
    ):
        return ShazamNetworkError(
            "Shazam's secure connection could not be verified. Check the Windows date/time "
            "and antivirus HTTPS scanning, then retry. (code: certificate)"
        )
    if isinstance(error, (ConnectionError, OSError)) or names.intersection(
        {
            "ClientConnectionError",
            "ClientConnectorDNSError",
            "ClientConnectorError",
            "ClientOSError",
            "ServerConnectionError",
            "ServerDisconnectedError",
        }
    ):
        return ShazamNetworkError(
            "Could not reach Shazam. Check internet access and firewall, VPN, or DNS filtering, "
            "then retry. (code: network)"
        )
    if names.intersection({"ContentTypeError", "FailedDecodeJson", "JSONDecodeError"}):
        return ShazamUnexpectedResponseError(
            "Shazam returned an unexpected response. Retry once; if it continues, check VPN, "
            "ad-blocker, firewall, or DNS filtering. (code: unexpected-response)"
        )
    return ShazamServiceError(
        "Shazam could not complete the recognition request. Retry once; if it continues, "
        f"share this error code: {type(error).__name__}."
    )


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _positive_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout_seconds must be a finite, positive number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError("timeout_seconds must be a finite, positive number")
    return normalized


def _retry_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in {0, 1}:
        raise ValueError("no_match_retries must be zero or one")
    return value


def _validate_sample(sample_path: Path) -> Path:
    path = Path(sample_path)
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            raise InvalidAudioSampleError(
                "The audio sample is missing, empty, or not a regular file."
            )
    except OSError:
        raise InvalidAudioSampleError(
            "The audio sample could not be accessed for recognition."
        ) from None
    return path


def _normalize_response(
    response: Mapping[str, Any],
    *,
    attempt_count: int,
) -> RecognitionResult:
    if not isinstance(response, Mapping):
        raise InvalidShazamResponseError("Shazam returned an invalid recognition response.")

    track_data = response.get("track")
    if isinstance(track_data, Mapping):
        track = _normalize_track(track_data)
        return RecognitionResult(
            status=RecognitionStatus.MATCHED,
            track=track,
            provider=PROVIDER_NAME,
            attempt_count=attempt_count,
        )

    matches = response.get("matches")
    if _is_sequence(matches) and len(matches) == 0:
        return RecognitionResult(
            status=RecognitionStatus.NO_MATCH,
            track=None,
            provider=PROVIDER_NAME,
            attempt_count=attempt_count,
        )

    raise InvalidShazamResponseError(
        "Shazam returned a recognition response without usable match information."
    )


def _normalize_track(track: Mapping[str, Any]) -> RecognizedTrack:
    title = _nonempty_string(track.get("title"))
    artist = _nonempty_string(track.get("subtitle"))
    if title is None or artist is None:
        raise InvalidShazamResponseError(
            "Shazam matched audio but omitted the track title or artist."
        )

    images = track.get("images")
    genres = track.get("genres")
    return RecognizedTrack(
        title=title,
        artist=artist,
        album=_album(track.get("sections")),
        genre=_mapping_string(genres, "primary"),
        artwork_url=_mapping_string(images, "coverart"),
        track_url=_nonempty_string(track.get("url")),
        provider_track_id=_identifier(track.get("key")),
    )


def _album(sections: object) -> str | None:
    if not _is_sequence(sections):
        return None
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        metadata = section.get("metadata")
        if not _is_sequence(metadata):
            continue
        for item in metadata:
            if not isinstance(item, Mapping):
                continue
            label = _nonempty_string(item.get("title"))
            if label is not None and label.casefold() == "album":
                return _nonempty_string(item.get("text"))
    return None


def _mapping_string(value: object, key: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return _nonempty_string(value.get(key))


def _identifier(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int)):
        return str(value).strip() or None
    return None


def _nonempty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
