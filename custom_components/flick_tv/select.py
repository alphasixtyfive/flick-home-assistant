"""Explicit, administratively authorized profile switching."""

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import ServiceValidationError
from .entity import FlickEntity
from .permissions import async_require_management
from .profile_options import profile_choices
from .services import async_request


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ProfileSelect(entry.runtime_data)])


class ProfileSelect(FlickEntity, SelectEntity):
    _attr_name = "Profile"
    _attr_icon = "mdi:account-switch"

    def __init__(self, device):
        super().__init__(device, "profile")

    def _choices(self):
        return profile_choices(self.device.status.get("profiles", []))

    @property
    def options(self):
        return list(self._choices())

    @property
    def current_option(self):
        current = self.device.status.get("profile", {}).get("id")
        return next((label for label, profile_id in self._choices().items() if profile_id == current), None)

    async def async_select_option(self, option):
        await async_require_management(self.hass, self._context)
        profile_id = self._choices().get(option)
        if profile_id is None:
            raise ServiceValidationError("Choose one of this TV's profiles")
        await async_request(self.device, "select_profile", {"id": profile_id})
