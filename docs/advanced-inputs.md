# Advanced Inputs

## Behavior

The overlay can start one normal listening operation from any of these inputs:

- a configurable long press or double press on the SteamVR **Listen for song** action;
- a configurable Windows-wide F1-F12 fallback hotkey;
- one VRChat avatar parameter received through local OSC.

The gesture only starts the operation. The button can be released after the short
haptic acknowledgement; capture continues for the configured recording duration.
Input requests are ignored while listening or while another request is waiting for
the UI thread, so one hold cannot start overlapping FFmpeg or recognition work.

All inputs exist only while the executable is open. They feed the same
`ListeningService` used by the desktop Listen button and do not import capture,
recognition, or UI code into the input adapters.

## SteamVR setup

The application ships an OpenVR action manifest plus right-hand bindings for
`oculus_touch` (including compatible Quest/Touch remappings) and `knuckles`
controllers. Settings can generate a binding for right A, B, thumbstick click, or
trigger click. Other controller types can bind **Listen for song** in SteamVR's
controller binding interface while Shazam for VRC is running.

SteamVR can retain a manual user override. If a newly selected button does not take
effect, reset Shazam for VRC to its default binding in SteamVR's controller-binding
screen, then restart the input listener by saving Settings again.

VRChat normally uses B for its Quick Menu. If pressing or holding B invokes both
applications, edit VRChat's SteamVR bindings and remove the right-B Quick Menu
binding. Left Y can remain the Quick Menu button. Alternatively, replace the
Shazam binding with a different button or chord in SteamVR.

The listener connects as a background OpenVR application, so it does not launch
SteamVR. If SteamVR is closed, a controller is unavailable, or SteamVR restarts,
the desktop overlay remains usable and the listener keeps retrying. Its current
state is shown in the **Advanced inputs** panel.

## Haptic acknowledgement

After a long press reaches its threshold, or a second press arrives inside the
configured double-press window, the input adapter asks the UI to accept the trigger.
A short haptic pulse is sent only if the request was accepted. Gestures rejected
because recognition is already running do not vibrate.

## Keyboard fallback

The selected F1-F12 key is registered through the Windows global-hotkey API with
key-repeat suppression. Registration is released when the application closes or
the setting is disabled. If another application already owns the key, the overlay
reports that conflict without affecting manual or SteamVR input.

## VRChat avatar OSC trigger

This option lets an avatar Expressions Menu Button, Toggle, or Contact Receiver
start listening. It is enabled by default for new installations and listens only
on the local PC. Existing settings are preserved during an update.

The quickest setup is the ready-made VCC/Unity package described in
[`avatar-setup.md`](avatar-setup.md). Dragging its prefab under the avatar root
installs the menu Button and parameter automatically. For a manual setup:

1. In the avatar's Expression Parameters asset, create a Bool named
   `ShazamListen` with a default value of false. **Synced** is optional because the
   app only needs your local value.
2. Drive that Bool with one of these avatar controls:
   - An Expressions Menu **Button** is the most convenient choice because VRChat
     resets it automatically.
   - A **Toggle** works, but it must be switched off before switching it on can
     trigger again.
   - A **Contact Receiver** can target the same parameter. **Constant** receiver
     mode is the most reliable choice because it stays true during contact and
     resets when contact ends.
3. Upload or switch to the avatar, then enable **OSC** in VRChat's Action Menu.
4. In **Settings > Advanced inputs**, confirm **VRChat avatar OSC trigger** is
   enabled, enter `ShazamListen`, keep receive port `9001`, and save.

The parameter field also accepts the full address
`/avatar/parameters/ShazamListen`. Parameter folders such as
`Tools/ShazamListen` are supported. The listener accepts Bool, Int, or Float OSC
values and fires only when the configured value changes from zero/false to
nonzero/true. Repeated true values never queue overlapping listening operations.

VRChat sends OSC to port `9001` by default. If another OSC receiver already owns
that port, the Advanced inputs status reports the conflict and keeps retrying. Use
a different receive port here only when VRChat's OSC output port has also been
changed. Closing or restarting VRChat does not disable the receiver; it remains
ready for the next matching message.
