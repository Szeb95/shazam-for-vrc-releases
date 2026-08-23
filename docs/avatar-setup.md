# Avatar setup

## What the prefab does

`Shazam for VRC Avatar Button.prefab` is a Modular Avatar add-on. When the avatar
is built, it adds:

- a top-level **Listen for song** Expressions Menu Button;
- a Bool expression parameter named `ShazamListen`;
- a false default with **Saved** and network sync disabled.

The Button sets the parameter to true while pressed and returns it to false when
released. With OSC enabled, VRChat sends the change to
`/avatar/parameters/ShazamListen`. Shazam for VRC listens on the local default OSC
output port, `127.0.0.1:9001`, and starts one listening operation on the rising
edge. Nothing in the prefab records audio or makes network requests by itself.

## Install with VCC

1. Add the official Modular Avatar repository to VCC if it is not already present:
   `https://vpm.nadena.dev/vpm.json`.
2. Add the Shazam for VRC repository:
   `https://szeb95.github.io/shazam-for-vrc-releases/index.json`.
3. Open **Manage Project** for the avatar project.
4. Install **Modular Avatar** and **Shazam for VRC Avatar Button**, then apply the
   changes.
5. Open the project in Unity. In **Packages > Shazam for VRC Avatar Button >
   Runtime > Prefabs**, drag `Shazam for VRC Avatar Button.prefab` directly under
   the avatar's root object in the Hierarchy.

The package requires Unity 2022.3, the VRChat Avatars SDK, and Modular Avatar.
VCC manages compatible versions of those dependencies.

## Install the Unity package

As a fallback, download `Shazam-for-VRC-Avatar-1.2.0.unitypackage` from the same
GitHub release as the Windows installer. Install Modular Avatar first, import the
Unity package, then find the prefab under **Assets > Shazam for VRC Avatar >
Runtime > Prefabs** and drag it under the avatar root.

## Upload and use it

1. Build and upload the avatar normally. Modular Avatar applies the prefab during
   the build without replacing the avatar's existing menu or parameter assets.
2. Start Shazam for VRC on the same PC as VRChat.
3. In VRChat's Action Menu, open **Options > OSC** and turn OSC on.
4. Open **Expressions** and press **Listen for song** while supported media is
   playing.

The avatar button starts the same workflow as the desktop Listen button. If the
desktop setting **Use VRChat/computer audio for the third attempt** is enabled,
that workflow uses two clean-stream attempts followed by one mixed Windows-output
attempt. The avatar prefab itself still has no audio access.

New desktop installations already have the avatar OSC trigger enabled with the
matching `ShazamListen` name and receive port `9001`. Updated installations keep
the user's previous setting; enable it under **Settings > Advanced inputs** if it
was previously off.

## Troubleshooting

- **The menu button is missing:** confirm the prefab is a child of the object with
  the VRChat Avatar Descriptor, and confirm Modular Avatar is installed.
- **The button appears but the app does nothing:** turn on OSC in VRChat and check
  the desktop app's Advanced inputs status.
- **Port 9001 is unavailable:** another OSC receiver owns VRChat's default output
  port. Close it, configure it to forward `/avatar/parameters/ShazamListen`, or
  change both VRChat's OSC output port and the app's receive port.
- **The parameter was renamed:** Modular Avatar auto-rename is deliberately off.
  If you manually rename `ShazamListen`, enter that exact same name in the app.
