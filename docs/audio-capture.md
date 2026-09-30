# Audio Capture

## Purpose

`shazam_for_vrc.streams.audio_capture` converts an existing `ResolvedStream` into
a short WAV sample without resolving the URL again:

```text
ResolvedStream -> FFmpeg -> temporary mono PCM WAV
```

Capture defaults to confirmed livestreams. The application may explicitly permit
prerecorded input only when it also supplies an automatically selected seek point.
Unknown playback states remain refused by default.

Local-audio capture is kept separate from clean-stream capture. The recommended
`shazam_for_vrc.streams.vrchat_audio_capture` source uses Windows process loopback
to include VRChat and its child processes while excluding other programs. The
explicit `shazam_for_vrc.streams.system_audio_capture` source records the complete
default Windows output through WASAPI loopback. Neither source is used until the
user enables the setting.

## Interface

Use `capture_audio()` as a context manager:

```python
from shazam_for_vrc.streams.audio_capture import capture_audio

with capture_audio(stream) as sample:
    recognize(sample.path)
    print(sample.requested_duration_seconds)
    print(sample.sample_rate, sample.channel_count, sample.file_size_bytes)
```

The default requested duration is 8 seconds. `duration_seconds` accepts any finite,
positive number. `timeout_seconds` is the total FFmpeg timeout; when omitted, it is
the requested duration plus 15 seconds for stream startup.

`start_seconds` accepts a finite, non-negative media position. FFmpeg receives it
as an input seek before `-i`, so the complete prerecorded file is not decoded from
the beginning. A prerecorded caller must also set
`allow_unsupported_playback=True`; seeking does not change the truthful
livestream-only meaning of `ResolvedStream.supports_current_audio`.

The result is an `AudioSample` containing its temporary `Path`, requested duration,
sample rate, channel count, and file size. Its path exists only inside the `with`
block. The entire temporary directory is removed on normal exit, caller errors,
FFmpeg errors, validation errors, and timeouts. A caller that explicitly wants to
retain a diagnostic sample must copy it somewhere safe while the context is active.

## Final local-audio attempt

With **Use local audio for the final attempt** enabled,
`ListeningService` follows this bounded order:

1. record and recognize one clean player-stream sample;
2. record and recognize a fresh clean player-stream sample after a no-match;
3. record and recognize one sample from the selected local source after a second
   no-match.

The normal retry-count setting applies when this fallback is off. When it is on,
the order above is fixed at two clean attempts plus one local-audio attempt. If a
long YouTube mix has no usable player-time estimate, clean seeking cannot represent
the audible moment, so the service goes directly to the explicit local-audio
fallback.

VRChat-only mode targets `VRChat.exe` and its child processes. It can include the
world player, voices, and other sounds rendered inside VRChat, but it excludes
audio from unrelated programs. Entire Windows output records the complete output
mix and may also include notifications and other applications. The application
never silently changes from the selected VRChat-only source to whole-output audio.

Empty and silent recordings are rejected with an actionable error. Temporary
directories are removed on success and failure. A sample is copied to
`last-sample.wav` only when the independent debug-retention setting is enabled.
Local-audio capture is off by default and should be enabled only when the user
accepts the privacy tradeoff of the selected source.

## FFmpeg input and output

FFmpeg must already be installed and available on `PATH`; the application does not
download or install it. HLS, DASH, HTTPS, and direct-media inputs use the exact
`ResolvedStream.stream_url`, including signed query strings. Normalized VRCDN RTSP
inputs use `rtsp://`, and `-rtsp_transport tcp` is placed before `-i` when requested
by the resolver. Required HTTP headers are passed as FFmpeg input headers.

The output format is suitable for music recognition:

- RIFF/WAVE with PCM signed 16-bit little-endian audio
- mono
- 44.1 kHz
- no video

The completed file must be nonempty, have a readable WAV header, contain audio
frames, and match the expected format before it is yielded to the caller.

## Playback safety and experimental override

Normal capture requires `stream.supports_current_audio` to be true. Confirmed
prerecorded, upcoming, ended, and unknown media are rejected with
`UnsupportedPlaybackError`. This prevents a prerecorded YouTube sample from being
mistaken for the moment currently playing in VRChat.

`ListeningService` uses the explicit override with a seek point in two cases:

- probable single-song videos: representative positions at 20%, 45%, and 70%
  across initial attempts, with later bounded alternatives
- long-form media: the current low-confidence position estimate from the VRChat
  log open time

The override is disabled by default for every other caller and does not itself
make a sample current.

## Errors

All capture failures derive from `AudioCaptureError`. Specific exceptions cover:

- missing or unstartable FFmpeg
- invalid duration, timeout, or seek values
- capture timeout (the process is terminated, then killed if necessary)
- FFmpeg failure or an input without an audio stream
- missing, empty, invalid, or unexpectedly formatted WAV output
- unsupported playback state
- unsafe HTTP headers

Header names and values containing CR or LF are rejected before FFmpeg starts.
Errors and logs intentionally omit stream URLs, signed query tokens, cookies,
authorization values, and raw FFmpeg diagnostics because those can contain the
input URL or headers.

## Automated testing

The tests use an in-process fake for FFmpeg and never contact a media provider or
require an FFmpeg installation:

```powershell
.venv\Scripts\Activate.ps1
python -m pytest -q
python -m ruff check src tests
```

To run only the capture tests:

```powershell
python -m pytest -q tests\test_audio_capture.py tests\test_system_audio_capture.py tests\test_vrchat_audio_capture.py
```

## Manual testing

First check FFmpeg without installing anything automatically:

```powershell
Get-Command ffmpeg
ffmpeg -version
```

With VRChat running in a world that is currently playing a supported livestream,
run this from the repository root. It prints only nonsensitive sample metadata and
deletes the WAV as soon as the `with` block finishes:

```powershell
@'
from shazam_for_vrc.streams.audio_capture import capture_audio
from shazam_for_vrc.streams.resolver import resolve_stream
from shazam_for_vrc.vrchat.log_reader import read_current_state
from shazam_for_vrc.vrchat.player_tracker import select_active_media

stream = resolve_stream(select_active_media(read_current_state()))
with capture_audio(stream) as sample:
    print({
        "duration_requested": sample.requested_duration_seconds,
        "sample_rate": sample.sample_rate,
        "channels": sample.channel_count,
        "file_size": sample.file_size_bytes,
    })
'@ | python -
```

Do not add URL or header printing to manual diagnostics. A retained sample may
contain private listening information; keep one only through a deliberate export.

Automated tests use fake loopback backends and never record the build computer's
real output. A real fallback test should be initiated only through the saved UI
setting while non-private audio is playing. VRChat must be running and producing
audio before the VRChat-only session can be selected.
