<p align="center">
  <img src="docs/assets/flick.svg" width="112" height="112" alt="Flick icon">
</p>

<h1 align="center">Flick TV for Home Assistant</h1>

Connect the [Flick Android TV launcher](https://github.com/alphasixtyfive/flick) to
Home Assistant. Each paired TV appears as a device with profile selection, wallpaper
controls and notifications.

Requires Home Assistant 2026.11 or later and Flick 0.2.2 or later. This is an early
development version.

## Install

Copy `custom_components/flick_tv` into Home Assistant's `custom_components` directory,
then restart Home Assistant. Add **Flick TV** from Settings → Devices & services.

In Flick, connect a dedicated non-admin Home Assistant account under Settings → Home
Assistant. Open TV integration → Pair TV and enter its six-digit code in Home Assistant.
Confirm the TV name. The code expires after five minutes. Pairing is approved through
an administrator's Home Assistant session; the TV keeps its non-admin account.

Flick's ordinary light controls work without this component.

## Controls

Each device exposes a connection sensor, launcher version, current profile, profile
selector, Next wallpaper button and notification entity.
Device options provide native forms for profiles, parent PINs, child app allowlists
and wallpaper preferences. Online photo collections can be replaced or extended with
`flick_tv.set_wallpapers`.

Enable **Launcher management** on the TV before changing profiles or wallpapers.
Management requests also require an unlocked adult profile on the TV. They are unavailable
at the startup picker or while a child profile is active. A child's app list does not restrict content inside a
streaming app; configure child accounts there as well.

For brief text notifications, use `notify.send_message` with the TV's notification
entity. The `flick_tv.notify` action also accepts an image URL or camera, up to two named actions,
a timeout and a persistent flag. Action buttons require an unlocked adult profile;
text and image notifications can still appear while the launcher is locked. Persistent
cards offer Dismiss and support Back.
Images require direct HTTP(S) links without credentials. Notification buttons emit
`flick_tv_notification_action` with the device, notification and action IDs; automations
decide what those IDs mean. The TV cannot choose an arbitrary Home Assistant service.

Notifications over other apps require Android's Display over other apps permission.
Android can hide overlays on protected content.

## Test notifications

Open the paired TV's device page and press **Test notification** under Diagnostic. It sends an
eight-second text card without interrupting remote input. Enable notifications and Android's
Display over other apps permission on the TV first; Launcher management can stay off.

For ordinary text, use the TV's notification entity in Developer tools → Actions:

```yaml
action: notify.send_message
target:
  entity_id: notify.my_tv_notification
data:
  title: Flick
  message: Notifications are working on this TV.
```

To include an existing Home Assistant camera image:

```yaml
action: flick_tv.notify
data:
  device_id: YOUR_FLICK_DEVICE_ID
  title: Camera preview
  message: Here is the latest camera image.
  camera_entity_id: camera.my_camera
  timeout: 15
```

Replace the example entity/device IDs with your TV and camera. Camera images use a short-lived
Home Assistant link; the TV does not receive camera credentials. Use `image` for a direct HTTP(S)
image URL instead of `camera_entity_id`, and do not specify both. The notification entity's native
last-sent timestamp records calls through `notify.send_message`, not the separate rich action.

## Access and privacy

Management actions require a Home Assistant administrator or system
automation context. The TV authenticates through the existing Home Assistant WebSocket
API; it opens no inbound network port. Pairing also binds a random device secret to
that user and the current socket. Never publish configuration entries, diagnostics
containing personal data, tokens or parent PINs.

Parent PINs contain exactly four digits. Changes use a masked options form. They are not exposed as service arguments
or entity attributes. Removing the integration disconnects the TV; ordinary light
controls in Flick remain separate.

## Development

The protocol and session tests use Python's standard library:

```sh
python -m unittest discover -s tests -v
```

These tests run independently of Home Assistant. Integration loading, configuration
flows, entity behavior and Android permissions also need testing on the supported
Home Assistant and TV versions. See the launcher's
[protocol documentation](https://github.com/alphasixtyfive/flick/blob/main/docs/home-assistant-protocol.md).

The square brand icon ships in `custom_components/flick_tv/brand/` at 256px and 512px.
Both PNGs use the vector source in `docs/assets/flick.svg`. Home Assistant loads them through its
[native branding support](https://contributors.home-assistant.io/core/integrations/brands/); no dashboard resource is needed.
