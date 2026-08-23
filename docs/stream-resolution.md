# Player and Stream Detection

## Purpose

Part 02 converts the latest media activity reported by the VRChat log reader into
a normalized stream decision for later audio capture. It does not invoke FFmpeg,
recognize music, or display output.

```text
LogSnapshot
    -> MediaCandidate
    -> URL classification
    -> yt-dlp metadata and a fresh or VRChat-active URL
    -> ResolvedStream
```

## Player selection

`vrchat.player_tracker.select_active_media()` consumes the snapshot returned by
part 01. VRChat's currently supported log messages do not provide a stable world
player-object ID, so version 1 selects the latest media activity after entry into
the current world. An AVPro opening is preferred, followed by a successful
resolution and then a pending original URL.

The candidate retains both the original page URL and VRChat's resolved URL. The
resolved URL is useful as a fallback, but signed CDN URLs can expire and should
not be treated as durable configuration.

## Classification

`streams.stream_detector.classify_url()` performs no network access. It validates
that the URL uses HTTP or HTTPS and identifies its provider and transport:

- YouTube, Twitch, VRCDN, direct media, or an unknown provider
- HLS, DASH, RTSP, a direct media file, a webpage, or an unknown transport

Transport and playback type are intentionally different. HLS (`.m3u8`) can carry
either live or prerecorded media, so the URL classifier never labels HLS as live
without metadata.

## Resolution

`streams.resolver.resolve_stream()` asks yt-dlp for metadata without downloading
the complete media. Playlist handling is disabled, and the best audio-capable
format is selected. The returned `ResolvedStream` includes:

- normalized provider, transport, and playback type
- a fresh playable URL
- HTTP headers required by the media host
- duration when reported
- normalized title, track, artist, album, and chapter metadata when reported
- a single-track, long-form, or unknown content classification with confidence
- whether capture is known to represent the current moment

Confirmed livestreams set `supports_current_audio` to true. Prerecorded media does
not set that flag because its current position is an estimate rather than a known
fact. Upcoming, ended, and unknown states are also preserved for actionable output.

For YouTube, structured `track` plus `artist` metadata is treated as a high-
confidence single song. Multiple chapters, a duration of at least 20 minutes, or
long-form title markers such as “DJ set” and “full album” identify long-form media.
A shorter video without long-form evidence is treated as a probable single song.
The title rule deliberately does not treat the word “remix” as a mix.

`streams.playback_position` estimates prerecorded position from the timestamp of
the latest AVPro opening, a nonzero AVPro opening offset when present, and common
YouTube `t`, `start`, or `time_continue` URL parameters. If elapsed time passes the
reported duration, it assumes looping and wraps the display position. The estimate
is always low confidence because VRChat does not log pause, seek, loop, or Udon
network-sync changes.

For a confirmed Twitch livestream, the resolver prefers VRChat's logged direct HLS
URL over yt-dlp's fresh audio-only rendition. Twitch can expose an audio-only
rendition containing digital silence even while the HLS rendition used by AVPro is
audible. yt-dlp metadata is still required to confirm that the source is live; the
logged URL is used only for the resulting capture.

If metadata inspection fails while VRChat has logged a direct HLS, DASH, or media
URL, the resolver returns that URL with an unknown playback type. This lets a later
integration offer an explicitly experimental attempt without falsely claiming the
source is live. If no direct fallback exists, a typed resolution error is raised.

## Privacy and safety

Only HTTP and HTTPS inputs are accepted. URLs and HTTP headers are hidden from the
normal representation of `ResolvedStream`, and resolver error messages do not
repeat provider errors that may contain signed URLs or tokens. Resolution uses the
yt-dlp Python interface and never executes URLs through a shell.

### VRCDN

VRCDN live playback is handled directly rather than sent through yt-dlp. The
HTTPS transport-stream form under `stream.vrcdn.live/live/` is preserved. The
VRChat/AVPro `rtspt://` form is normalized to standard `rtsp://`, and
`ResolvedStream.rtsp_transport` is set to `tcp` so part 03 can give FFmpeg the
matching transport option. RTSP-family URLs remain rejected for every unrecognized
host.

## Limitations

- Multiple simultaneous world players cannot yet be distinguished reliably.
- Prerecorded long-form position is approximate and may be wrong after a pause,
  seek, manual sync, late join, or non-looping playback.
- Unknown direct HLS sources may require a manual test before being trusted as live.
- A stream may expire after resolution; audio capture should start immediately.

## Testing

The player selector, classifier, and resolver have offline unit tests. Metadata
responses and failures are injected into the resolver, so the automated test suite
never contacts YouTube, Twitch, or another media provider.
