# Shazam Recognition

## Purpose

`shazam_for_vrc.recognition` submits the fingerprint of a short temporary audio
sample through ShazamIO and converts the provider response into application-owned
types:

```text
temporary audio path -> local Shazam fingerprint -> Shazam lookup
                     -> RecognitionResult -> immediate sample cleanup by caller
```

The recognition module does not own or retain the audio file. The capture context
remains responsible for deleting it as soon as recognition finishes.

## Interface

`ShazamRecognizer.recognize()` is asynchronous and accepts a `pathlib.Path`:

```python
from shazam_for_vrc.recognition import ShazamRecognizer

recognizer = ShazamRecognizer()
result = await recognizer.recognize(sample.path)

if result.track is None:
    print("No song recognized")
else:
    print(f"{result.track.artist} - {result.track.title}")
```

A successful provider response returns `RecognitionResult`, including a normalized
status, provider name, attempt count, and optional `RecognizedTrack`. Track metadata
can contain title, artist, album, genre, artwork URL, Shazam URL, and Shazam track ID.
Optional provider fields remain `None` when unavailable.

No match is a normal `RecognitionStatus.NO_MATCH` result. It is not an exception.

## Safety and reliability behavior

The default recognizer implements the version-one safeguards:

- total timeout of 15 seconds across the complete operation
- at most one retry, and only after a valid no-match response
- the retry uses ShazamIO's alternate legacy fingerprint generator because its
  newer Rust generator has known false negatives for some clean audio
- no retries for connection, provider, invalid-response, or local-file failures
- only one in-flight request per `ShazamRecognizer` instance
- no raw Shazam response, temporary path, or provider diagnostics in application logs
- lazy ShazamIO import so a missing runtime dependency produces an actionable error

`no_match_retries=0` disables the retry. Values greater than one are refused.
`timeout_seconds` may be changed to another finite, positive value. ShazamIO performs
its own HTTP retries, but the application timeout bounds their combined duration.

The retry reuses the validated sample but generates a different fingerprint. This
compatibility fallback is intentionally isolated and suppresses only ShazamIO's
deprecation warning. It can be removed when the newer fingerprint implementation
reliably recognizes the same catalogue. Separately, `ListeningService` can take
fresh clean recordings and, when explicitly enabled, one final Windows-output
recording. Each recording is its own bounded recognition operation; the provider's
alternate fingerprint does not create or retain another audio file.

## Errors

All provider failures derive from `ShazamRecognitionError`. Specific errors cover:

- missing or empty audio samples
- missing or unloadable ShazamIO installation
- recognition already in progress
- total timeout
- ShazamIO or remote-service failure
- incomplete or malformed Shazam responses

Callers should show these as operational errors. They should show `NO_MATCH` as a
normal result that suggests trying again during a clearer part of the song.

## Windows setup

Requirements are Python 3.12 or newer and FFmpeg on `PATH`. From PowerShell in the
repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Get-Command ffmpeg
```

If an existing `.venv` refers to a Python installation that has been moved or
removed, rename that environment and recreate it before installing dependencies:

```powershell
Rename-Item .venv .venv-broken
py -3.12 -m venv .venv
```

The project pins ShazamIO to the supported `0.8.x` range in `pyproject.toml`.

## Automated tests

The tests use a fake Shazam client. They do not contact Shazam and do not need a
real audio recording:

```powershell
python -m pytest -q tests\test_shazam_recognition.py
python -m pytest -q
python -m ruff check src tests
```

They cover successful normalization, no-match retry limits, provider failures,
timeouts, overlapping calls, invalid responses, and missing samples.

## Manual provider test with a known song

Create an eight-second temporary WAV from a song you can identify. This file is
outside the repository and is removed after the test:

```powershell
$source = "C:\path\to\a-known-song.mp3"
$env:SHAZAM_TEST_SAMPLE = Join-Path $env:TEMP "shazam-for-vrc-manual.wav"
ffmpeg -hide_banner -loglevel error -y -ss 30 -i $source -t 8 -vn -ac 1 -ar 44100 -c:a pcm_s16le $env:SHAZAM_TEST_SAMPLE
```

Run recognition without printing the raw provider response:

```powershell
@'
import asyncio
import os
from pathlib import Path

from shazam_for_vrc.recognition import ShazamRecognizer

async def main() -> None:
    result = await ShazamRecognizer().recognize(Path(os.environ["SHAZAM_TEST_SAMPLE"]))
    if result.track is None:
        print(f"No match after {result.attempt_count} attempt(s)")
    else:
        print(f"Matched: {result.track.artist} - {result.track.title}")

asyncio.run(main())
'@ | python -

Remove-Item -LiteralPath $env:SHAZAM_TEST_SAMPLE
Remove-Item Env:SHAZAM_TEST_SAMPLE
```

This test uses the public Shazam service and therefore requires an internet
connection. Use it occasionally rather than as a continuous polling test.

## Manual live VRChat pipeline test

With VRChat running in a world that is currently playing a supported livestream:

```powershell
@'
import asyncio

from shazam_for_vrc.recognition import ShazamRecognizer
from shazam_for_vrc.streams.audio_capture import capture_audio
from shazam_for_vrc.streams.resolver import resolve_stream
from shazam_for_vrc.vrchat.log_reader import read_current_state
from shazam_for_vrc.vrchat.player_tracker import select_active_media

stream = resolve_stream(select_active_media(read_current_state()))

async def main() -> None:
    with capture_audio(stream) as sample:
        result = await ShazamRecognizer().recognize(sample.path)
    if result.track is None:
        print(f"No match after {result.attempt_count} attempt(s)")
    else:
        print(f"Matched: {result.track.artist} - {result.track.title}")

asyncio.run(main())
'@ | python -
```

The command deliberately prints neither media URLs nor Shazam's raw response. The
captured WAV is deleted when the capture context exits, including when recognition
fails.
