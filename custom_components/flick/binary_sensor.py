"""Connection health does not become unavailable when the TV disconnects."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from .entity import FlickEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ConnectionSensor(entry.runtime_data)])


class ConnectionSensor(FlickEntity, BinarySensorEntity):
    _attr_name = "Connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device):
        super().__init__(device, "connection")

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.device.connected
