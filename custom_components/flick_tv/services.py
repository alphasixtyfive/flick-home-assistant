"""Validated management actions; no caller chooses an arbitrary device command."""

from datetime import timedelta
from uuid import uuid4

import probatio as schema
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .permissions import async_require_management
from .protocol import InvalidPayload, camera_entity_id, camera_title, identifier
from .session import DeviceRejected, DeviceUnavailable


def _whole_number(value):
    # Number selectors may submit 300.0 for a whole-number field.
    if type(value) not in (int, float) or not 0 <= value <= 86400 or value != int(value):
        raise schema.Invalid("Use a whole number")
    return int(value)


def device_from_id(hass, device_id):
    try:
        selected = identifier(device_id)
        device = hass.data[DOMAIN].devices.get(selected)
        if device is None:
            registered = dr.async_get(hass).async_get(selected)
            if registered is not None:
                device = next((hass.data[DOMAIN].devices.get(value)
                               for domain, value in registered.identifiers if domain == DOMAIN), None)
    except InvalidPayload as err:
        raise ServiceValidationError("Choose a paired TV") from err
    if device is None:
        raise ServiceValidationError("Choose a paired TV")
    return device


async def async_request(device, command, data=None, *, allow_pin=False):
    try:
        return await device.request(command, data, allow_pin=allow_pin)
    except InvalidPayload as err:
        raise ServiceValidationError(str(err)) from err
    except (DeviceRejected, DeviceUnavailable) as err:
        raise HomeAssistantError(str(err)) from err


def _camera_state(hass, value):
    try:
        camera = camera_entity_id(value)
    except InvalidPayload as err:
        raise ServiceValidationError(str(err)) from err
    state = hass.states.get(camera)
    if state is None:
        raise ServiceValidationError("Choose an existing camera entity")
    return state


def _open_camera(hass, device, data):
    if "camera_view" not in device.status.get("capabilities", []):
        raise ServiceValidationError("This TV does not report camera view support")
    state = _camera_state(hass, data["camera_entity_id"])
    if title := camera_title(state.name):
        data["title"] = title


def _notification(hass, data):
    if "id" not in data:
        data["id"] = str(uuid4())
    camera = data.pop("camera_entity_id", None)
    if camera is None:
        return
    if "image" in data:
        raise ServiceValidationError("Choose an image URL or a camera, not both")
    state = _camera_state(hass, camera)
    image = async_sign_path(hass, f"/api/camera_proxy/{state.entity_id}", timedelta(seconds=60), use_content_user=True)
    # Home Assistant leaves image dimensions outside the signature.
    data["image"] = f"{image}&width=1280&height=720"


@callback
def async_register_services(hass):
    async def handle(call):
        await async_require_management(hass, call.context)
        device = device_from_id(hass, call.data["device_id"])
        data = {key: value for key, value in call.data.items() if key != "device_id"}
        if "profile_id" in data:
            data["id"] = data.pop("profile_id")
        if call.service == "open_camera":
            _open_camera(hass, device, data)
        elif call.service == "notify":
            _notification(hass, data)
        result = await async_request(device, call.service, data)
        if call.service in ("get_status", "list_apps", "list_profiles"):
            return result
        return None

    base = {schema.Required("device_id"): str}
    definitions = {
        "get_status": {}, "list_apps": {}, "list_profiles": {}, "next_wallpaper": {},
        "select_profile": {schema.Required("profile_id"): str},
        "delete_profile": {schema.Required("profile_id"): str},
        "set_profile": {schema.Required("profile_id"): str, schema.Optional("name"): str,
                        schema.Optional("child"): bool, schema.Optional("allowed_apps"): list,
                        schema.Optional("avatar"): schema.Any(str, None)},
        "set_wallpaper_options": {schema.Optional("interval_seconds"): _whole_number, schema.Optional("shuffle"): bool,
                                  schema.Optional("dim_amount"): schema.Any(int, float)},
        "set_wallpapers": {schema.Required("photos"): list, schema.Optional("replace", default=True): bool},
        "open_camera": {schema.Required("camera_entity_id"): str},
        "notify": {schema.Required("message"): str, schema.Optional("id"): str, schema.Optional("title"): str,
                   schema.Optional("image"): str, schema.Optional("camera_entity_id"): str,
                   schema.Optional("actions"): list,
                   schema.Optional("persistent", default=False): bool, schema.Optional("timeout", default=8): _whole_number},
    }
    for name, extra in definitions.items():
        response = SupportsResponse.ONLY if name in ("get_status", "list_apps", "list_profiles") else SupportsResponse.NONE
        hass.services.async_register(DOMAIN, name, handle, schema=schema.Schema(base | extra), supports_response=response)
