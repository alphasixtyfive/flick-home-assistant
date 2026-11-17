"""Administrator pairing and launcher configuration forms."""

from __future__ import annotations

from uuid import uuid4
import probatio as schema
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.setup import async_setup_component

from .const import DOMAIN
from .profile_options import app_options
from .protocol import InvalidPayload, parent_pin, profile, profile_patch, text
from .session import DeviceRejected, DeviceUnavailable


def _text(*, password=False):
    return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD if password else selector.TextSelectorType.TEXT))


class FlickConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._code = None
        self._device_id = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FlickOptionsFlow(config_entry.entry_id)

    async def async_step_user(self, user_input=None):
        if not await async_setup_component(self.hass, DOMAIN, {}):
            return self.async_abort(reason="setup_failed")
        errors = {}
        if user_input is not None:
            try:
                code = user_input.get("pairing_code", "")
                if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
                    raise InvalidPayload("Invalid pairing code")
                pending = self.hass.data[DOMAIN].pairing(code)
                self._code, self._device_id = code, pending.device_id
                await self.async_set_unique_id(pending.device_id)
                return await self.async_step_confirm()
            except InvalidPayload:
                errors["base"] = "pairing_not_found"
        return self.async_show_form(step_id="user", data_schema=schema.Schema({schema.Required("pairing_code"): _text()}), errors=errors)

    async def async_step_confirm(self, user_input=None):
        try:
            pending = self.hass.data[DOMAIN].pairing(self._code or "")
            if pending.device_id != self._device_id:
                raise InvalidPayload("Pairing changed")
        except InvalidPayload:
            return self.async_abort(reason="pairing_expired")
        existing = next((entry for entry in self._async_current_entries() if entry.unique_id == pending.device_id), None)
        errors = {}
        name = existing.title if existing is not None else pending.name
        if user_input is not None:
            try:
                name = text(user_input.get("name", name), 80).strip()
            except InvalidPayload:
                errors["base"] = "invalid_name"
        if user_input is None or errors:
            return self.async_show_form(step_id="confirm", data_schema=schema.Schema({schema.Required("name", default=name): _text()}),
                errors=errors,
                description_placeholders={"name": pending.name, "replacement": "Replace the existing pairing." if existing else ""})
        data = pending.entry_data() | {"name": name}
        if existing is not None:
            self.hass.config_entries.async_update_entry(existing, data=data, title=name)
            await self.hass.config_entries.async_reload(existing.entry_id)
            return self.async_abort(reason="reconfigured")
        return self.async_create_entry(title=name, data=data)


class FlickOptionsFlow(OptionsFlow):
    def __init__(self, entry_id):
        self._entry_id = entry_id
        self._profiles = []
        self._apps = []
        self._editing = None

    def _device(self):
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        device = entry.runtime_data if entry is not None else None
        if device is None or not device.connected:
            raise DeviceUnavailable("TV is offline")
        return device

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["wallpaper", "profiles"])

    async def async_step_profiles(self, user_input=None):
        return self.async_show_menu(step_id="profiles", menu_options=[
            "add_profile", "edit_profile", "parent_pin", "delete_profile"])

    def _options(self):
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return dict(entry.options) if entry is not None else {}

    async def _load_profiles(self, *, include_apps=False):
        data = await self._device().request("list_profiles")
        profiles = data.get("profiles", [])
        if not isinstance(profiles, list) or len(profiles) > 12:
            raise InvalidPayload("Invalid profile list")
        self._profiles = []
        for item in profiles:
            if not isinstance(item, dict):
                raise InvalidPayload("Invalid profile list")
            clean = profile({key: item.get(key) for key in ("id", "name", "child")})
            if "allowed_apps" in item:
                clean["allowed_apps"] = profile_patch({"id": clean["id"], "allowed_apps": item["allowed_apps"]})["allowed_apps"]
            self._profiles.append(clean)
        if not include_apps:
            return
        apps = (await self._device().request("list_apps")).get("apps", [])
        if not isinstance(apps, list) or len(apps) > 512:
            raise InvalidPayload("Invalid app list")
        self._apps = [{"value": item["package_name"], "label": item["name"]} for item in apps[:512]
                      if isinstance(item, dict) and isinstance(item.get("package_name"), str) and isinstance(item.get("name"), str)]

    def _profile_selector(self, *, adults=False, removable=False):
        profiles = self._profiles
        if adults:
            profiles = [item for item in profiles if not item["child"]]
        if removable:
            adults_count = sum(not item["child"] for item in profiles)
            profiles = [item for item in profiles if item["child"] or adults_count > 1]
        return selector.SelectSelector(selector.SelectSelectorConfig(options=[{"value": item["id"], "label": item["name"]} for item in profiles]))

    async def async_step_add_profile(self, user_input=None):
        self._editing = {"id": str(uuid4()), "name": "", "child": True, "allowed_apps": []}
        try:
            await self._load_profiles(include_apps=True)
        except (DeviceUnavailable, DeviceRejected, InvalidPayload):
            return self.async_abort(reason="command_failed")
        if len(self._profiles) >= 12:
            return self.async_abort(reason="profile_limit")
        return await self.async_step_profile(user_input)

    async def async_step_edit_profile(self, user_input=None):
        if user_input is not None:
            self._editing = next((profile for profile in self._profiles if profile["id"] == user_input["profile_id"]), None)
            if self._editing is not None:
                return await self.async_step_profile()
        try:
            await self._load_profiles(include_apps=True)
        except (DeviceUnavailable, DeviceRejected, InvalidPayload):
            return self.async_abort(reason="command_failed")
        return self.async_show_form(step_id="edit_profile", data_schema=schema.Schema({schema.Required("profile_id"): self._profile_selector()}))

    async def async_step_profile(self, user_input=None):
        errors = {}
        if self._editing is None:
            return self.async_abort(reason="command_failed")
        if user_input is not None:
            patch = {"id": self._editing["id"], **user_input}
            try:
                if patch.get("pin") in (None, ""):
                    patch.pop("pin", None)
                else:
                    patch["pin"] = parent_pin(patch["pin"])
            except InvalidPayload:
                errors["pin"] = "invalid_pin"
            if not errors:
                try:
                    await self._device().request("set_profile", patch, allow_pin=True)
                    return self.async_create_entry(title="", data=self._options())
                except (DeviceUnavailable, DeviceRejected, InvalidPayload):
                    errors["base"] = "command_failed"
        existing = self._editing
        form = {
            schema.Required("name", default=existing["name"]): _text(),
            schema.Required("child", default=existing["child"]): selector.BooleanSelector(),
            schema.Optional("pin"): _text(password=True),
        }
        if "allowed_apps" in existing:
            form[schema.Optional("allowed_apps", default=existing["allowed_apps"])] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=app_options(self._apps, existing["allowed_apps"]),
                    multiple=True, custom_value=False))
        return self.async_show_form(step_id="profile", data_schema=schema.Schema(form), errors=errors)

    async def async_step_parent_pin(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                pin = parent_pin(user_input.get("pin"))
            except InvalidPayload:
                errors["pin"] = "invalid_pin"
            if not errors:
                try:
                    await self._device().request("set_profile", {"id": user_input["profile_id"], "pin": pin}, allow_pin=True)
                    return self.async_create_entry(title="", data=self._options())
                except (DeviceUnavailable, DeviceRejected, InvalidPayload):
                    errors["base"] = "command_failed"
        try:
            await self._load_profiles()
        except (DeviceUnavailable, DeviceRejected, InvalidPayload):
            return self.async_abort(reason="command_failed")
        return self.async_show_form(step_id="parent_pin", data_schema=schema.Schema({
            schema.Required("profile_id"): self._profile_selector(adults=True), schema.Required("pin"): _text(password=True)}), errors=errors)

    async def async_step_delete_profile(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await self._device().request("delete_profile", {"id": user_input["profile_id"]})
                return self.async_create_entry(title="", data=self._options())
            except (DeviceUnavailable, DeviceRejected, InvalidPayload):
                errors["base"] = "command_failed"
        try:
            await self._load_profiles()
        except (DeviceUnavailable, DeviceRejected, InvalidPayload):
            return self.async_abort(reason="command_failed")
        return self.async_show_form(step_id="delete_profile", data_schema=schema.Schema({
            schema.Required("profile_id"): self._profile_selector(removable=True)}), errors=errors)

    async def async_step_wallpaper(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = dict(user_input)
                if "interval_seconds" in data and type(data["interval_seconds"]) in (int, float) and float(data["interval_seconds"]).is_integer():
                    data["interval_seconds"] = int(data["interval_seconds"])
                await self._device().request("set_wallpaper_options", data)
                return self.async_create_entry(title="", data=self._options())
            except (DeviceUnavailable, DeviceRejected, InvalidPayload):
                errors["base"] = "command_failed"
        return self.async_show_form(step_id="wallpaper", data_schema=schema.Schema({
            schema.Optional("interval_seconds"): selector.NumberSelector(selector.NumberSelectorConfig(min=30, max=86400, mode=selector.NumberSelectorMode.BOX)),
            schema.Optional("shuffle"): selector.BooleanSelector(),
            schema.Optional("dim_amount"): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=.7, step=.01, mode=selector.NumberSelectorMode.SLIDER)),
        }), errors=errors)
