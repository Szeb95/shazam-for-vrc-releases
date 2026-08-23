# Shazam for VRC Avatar Button

This package contains a ready-made Modular Avatar prefab for triggering Shazam for
VRC from the VRChat Expressions Menu.

1. Install Modular Avatar in the avatar project.
2. Drag `Runtime/Prefabs/Shazam for VRC Avatar Button.prefab` directly under the
   avatar root in the Unity hierarchy.
3. Build and upload the avatar.
4. In VRChat, enable OSC and press **Expressions > Listen for song**.

The prefab installs a non-saved, local-only Bool parameter named `ShazamListen`.
VRChat sends changes to `/avatar/parameters/ShazamListen` on its OSC output port.
The desktop app listens only on `127.0.0.1:9001` and starts recognition on the
false-to-true edge produced by the Button control.

The parameter name is intentionally not auto-renamed. If you change it in Unity,
use the same name in the desktop app's Settings page.
