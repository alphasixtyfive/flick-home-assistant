# Flick TV for Home Assistant

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
This gives Home Assistant administrators parent-level control, including selection
of an adult profile. A child profile's app list does not restrict content inside a
streaming app; configure child accounts there as well.

For brief text notifications, use `notify.send_message` with the TV's notification
entity. The `flick_tv.notify` action also accepts an image URL, up to two named actions,
a timeout and a persistent flag. Persistent cards offer Dismiss and support Back.
Images require direct HTTP(S) links without credentials. Notification buttons emit
`flick_tv_notification_action` with the device, notification and action IDs; automations
decide what those IDs mean. The TV cannot choose an arbitrary Home Assistant service.

Notifications over other apps require Android's Display over other apps permission.
Android can hide overlays on protected content.

## Access and privacy

Management actions require a Home Assistant administrator or system
automation context. The TV authenticates through the existing Home Assistant WebSocket
API; it opens no inbound network port. Pairing also binds a random device secret to
that user and the current socket. Never publish configuration entries, diagnostics
containing household data, tokens or parent PINs.

Parent PIN changes use a masked options form. They are not exposed as service arguments
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
