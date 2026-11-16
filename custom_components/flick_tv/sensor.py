"""Small, credential-free launcher state."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from .entity import FlickEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CurrentProfile(entry.runtime_data), Version(entry.runtime_data)])


class CurrentProfile(FlickEntity, SensorEntity):
    _attr_name = "Current profile"
    _attr_icon = "mdi:account-circle"

    def __init__(self, device):
        super().__init__(device, "current_profile")

    @property
    def native_value(self):
        return self.device.status.get("profile", {}).get("name")


class Version(FlickEntity, SensorEntity):
    _attr_name = "Version"
    _attr_icon = "mdi:information-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device):
        super().__init__(device, "version")

    @property
    def native_value(self):
        return self.device.status.get("version")
