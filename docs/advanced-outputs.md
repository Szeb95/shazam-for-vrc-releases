# Advanced Outputs

## XSOverlay notifications

The **XSOverlay notifications** setting is enabled by default. While XSOverlay is
running, Shazam for VRC sends concise VR notifications for these events:

- **Start listening** when a button, controller action, or F9 starts the pipeline.
- **Track recognized** with `Artist — Title`.
- **Mix** with the whole mix title when its current playback position is unknown.
- **No track found** after all configured fresh-recording attempts.
- **Listening error** with the same actionable message shown in the desktop app.

The desktop status and track log continue working when this output is
disabled or XSOverlay is not running.

## Connection behavior

Notifications use XSOverlay's local notification API at `127.0.0.1:42069`, its
default UDP port. The adapter is deliberately one-way: it never starts XSOverlay,
waits for a reply, or delays recognition. If XSOverlay is closed, the local
datagram is simply not displayed.

If XSOverlay uses a non-default notification port, restore `UdpPort` to `42069` in
its `ExternalMessageAPIConfig.json` or disable **XSOverlay notifications** in
Shazam for VRC.

## Privacy

Notification data stays on the local computer. A notification contains only the
short status/error text or recognized artist and title. It never includes the
media URL, VRChat log contents, audio, or the raw Shazam response, and the output
adapter does not retain notification content.

## Troubleshooting

1. Start SteamVR and XSOverlay before Shazam for VRC.
2. Leave **XSOverlay notifications** selected and save settings.
3. Start a listening operation. **Start listening** should appear immediately.
4. If it does not, verify that XSOverlay's notification API is enabled and uses
   UDP port `42069`.

The sender uses XSOverlay's documented legacy UDP notification endpoint because
it remains supported and requires no additional background connection. XSOverlay's
newer WebSocket API is a future migration option.

## VRChat chatbox track results

The **VRChat chatbox track results** setting is disabled by default because
chatbox messages may be visible to other players. The wording describes the
playback context:

```text
Twitch stream song: Title — Artist
VRCDN stream song: Title — Artist
Song in mix: Title — Artist
YouTube song: Title — Artist
Mix: Mix name
```

Live sources include the detected provider, such as Twitch, YouTube, or VRCDN.
Provider naming remains automatically extensible. A recognized track inside an
identified mix uses `Song in mix`; an ordinary prerecorded track includes its
source, such as `YouTube song`.
When the mix is known but its current time cannot be determined, the complete
media title is sent as `Mix`.

Start, no-match, and error states are not sent to the chatbox. Messages are made
single-line and shortened to VRChat's 144-character limit when necessary. The
extra chatbox notification sound is suppressed.

### Setup

1. In VRChat, open the Action Menu and select **OSC > Enabled**.
2. In Shazam for VRC, select **VRChat chatbox track results**.
3. Select **Save settings** or begin a listening operation, which also saves the
   current settings.

The app sends `/chatbox/input` OSC messages to `127.0.0.1:9000`, VRChat's default
local input address. Like the XSOverlay adapter, this is a local one-way datagram:
recognition remains usable if VRChat is closed or OSC is disabled.

If VRChat was launched with a custom OSC input port, use its default port `9000`
for this version of Shazam for VRC.
