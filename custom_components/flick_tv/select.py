"""Explicit, administratively authorized profile switching."""

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import ServiceValidationError
from .entity import FlickEntity
from .permissions import async_require_management
from .services import async_request


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ProfileSelect(entry.runtime_data)])


class ProfileSelect(FlickEntity, SelectEntity):
    _attr_name = "Profile"
    _attr_icon = "mdi:account-switch"

    def __init__(self, device):
        super().__init__(device, "profile")

    def _choices(self):
        profiles = self.device.status.get("profiles", [])
        names = [profile["name"] for profile in profiles]
        return {(profile["name"] if names.count(profile["name"]) == 1 else f"{profile['name']} ({profile['id']})"): profile["id"]
                for profile in profiles}

    @property
    def options(self):
        return list(self._choices())

    @property
    def current_option(self):
        current = self.device.status.get("profile", {}).get("id")
        return next((label for label, profile_id in self._choices().items() if profile_id == current), None)

    async def async_select_option(self, option):
        await async_require_management(self.hass, self._context)
        if option not in self._choices():
            raise ServiceValidationError("Choose one of this TV's profiles")
        await async_request(self.device, "select_profile", {"id": self._choices()[option]})
