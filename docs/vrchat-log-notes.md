# VRChat Log Research Notes

Record verified VRChat log formats here with sensitive values removed. Do not
commit personal VRChat logs.

Verified public examples (reviewed 2026-08-20) show these forms:

- `[Behaviour] Entering Room: The Default Cube`
- `[Behaviour] Joining wrld_<uuid>:<instance>~region(<region>)`
- `[Video Playback] Attempting to resolve URL '<original>'`
- `[Video Playback] URL '<original>' resolved to '<direct>'`
- `[AVProVideo] Opening <direct> (offset 0) with API MediaFoundation`

The client log is not a documented API, so fixtures cover small whitespace, quote,
and capitalization variations. See `docs/log-reader.md` for supported behavior.

Remaining questions for later milestones:

- Can stop, pause, replacement, or player identity be inferred reliably?
- What differs between AVPro and Unity video players?
- Which livestream URL forms appear before and after resolution?
