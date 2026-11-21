"""Authenticated TV subscriptions on Home Assistant's existing WebSocket API."""

import probatio as schema
from homeassistant.components import websocket_api
from homeassistant.core import callback
from homeassistant.helpers.http import current_request

from .const import DOMAIN
from .home_text import MAX_GENERATION
from .protocol import InvalidPayload, text


def _user(connection):
    user = connection.user
    if user is None or not user.is_active:
        raise InvalidPayload("An active TV user is required")
    return user


def _device(hass, connection, message):
    user = _user(connection)
    return hass.data[DOMAIN].authorized(message["device_id"], message["device_secret"], user.id, connection)


@callback
def async_register_commands(hass):
    for command in (ws_connect, ws_result, ws_status, ws_notification_action, ws_home_text):
        websocket_api.async_register_command(hass, command)


@websocket_api.websocket_command({
    schema.Required("type"): "flick_tv/connect",
    schema.Required("device_id"): str,
    schema.Required("device_secret"): str,
    schema.Required("name"): str,
    schema.Required("protocol"): int,
    schema.Optional("pairing_code"): str,
})
@callback
def ws_connect(hass, connection, message):
    try:
        user = _user(connection)
        if user.is_admin:
            raise InvalidPayload("Use a dedicated non-admin TV user")
        data = {key: value for key, value in message.items() if key not in ("id", "type")}
        request = current_request.get()
        transport = request.transport if request is not None else None
        if transport is None:
            raise InvalidPayload("The TV connection is closing")
        registry = hass.data[DOMAIN]
        # Retain only this socket's public close callback, not the HTTP request or HA connection.
        result = registry.connect(data, user.id, connection,
            lambda event: connection.send_event(message["id"], event), retire=transport.close)
        connection.subscriptions[message["id"]] = lambda: registry.disconnect(data["device_id"], connection)
        connection.send_result(message["id"], result)
    except InvalidPayload as err:
        connection.send_error(message["id"], "invalid_pairing", str(err))


_AUTH = {schema.Required("device_id"): str, schema.Required("device_secret"): str}


@websocket_api.websocket_command({
    schema.Required("type"): "flick_tv/home_text", **_AUTH,
    schema.Required("profile_id"): str,
    schema.Required("profile_generation"): schema.All(int, schema.Range(min=0, max=MAX_GENERATION)),
})
@callback
def ws_home_text(hass, connection, message):
    try:
        device = _device(hass, connection, message)
        manager = device.home_text
        if manager is None:
            raise InvalidPayload("Home screen text is not ready")
        subscription = manager.subscribe(
            message["profile_id"], message["profile_generation"],
            lambda value: connection.send_event(message["id"], value))
        connection.subscriptions[message["id"]] = subscription.close
        connection.send_result(message["id"])
    except InvalidPayload:
        connection.send_error(message["id"], "invalid_home_text", "Home screen text is unavailable")


@websocket_api.websocket_command({
    schema.Required("type"): "flick_tv/result", **_AUTH,
    schema.Required("request_id"): str, schema.Required("success"): bool,
    schema.Optional("data", default={}): dict, schema.Optional("error"): str,
})
@callback
def ws_result(hass, connection, message):
    try:
        if "error" in message:
            text(message["error"], 160)
        accepted = _device(hass, connection, message).receive_result(message["request_id"], message["success"], message["data"], message.get("error"))
        connection.send_result(message["id"], {"accepted": accepted})
    except InvalidPayload:
        connection.send_error(message["id"], "invalid_result", "The TV result was not accepted")


@websocket_api.websocket_command({
    schema.Required("type"): "flick_tv/status", **_AUTH,
    schema.Required("version"): str, schema.Required("profile"): dict,
    schema.Required("profiles"): list, schema.Required("capabilities"): list,
    schema.Optional("current_app"): schema.Any(str, None),
})
@callback
def ws_status(hass, connection, message):
    try:
        data = {key: value for key, value in message.items() if key not in ("id", "type", "device_id", "device_secret")}
        _device(hass, connection, message).receive_status(data)
        connection.send_result(message["id"])
    except InvalidPayload:
        connection.send_error(message["id"], "invalid_status", "The TV status was not accepted")


@websocket_api.websocket_command({
    schema.Required("type"): "flick_tv/notification_action", **_AUTH,
    schema.Required("notification_id"): str, schema.Required("action_id"): str,
})
@callback
def ws_notification_action(hass, connection, message):
    try:
        data = _device(hass, connection, message).notification_action(message["notification_id"], message["action_id"])
        hass.bus.async_fire("flick_tv_notification_action", data)
        connection.send_result(message["id"])
    except InvalidPayload:
        connection.send_error(message["id"], "invalid_action", "This notification action is not available")
