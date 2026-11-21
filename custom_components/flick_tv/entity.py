"""Common push-state and device registration for Flick entities."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


class FlickEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device, key):
        self.device = device
        self._attr_unique_id = f"{device.device_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device.device_id)},
                                            name=device.name, manufacturer="Flick", model="Android TV launcher")

    @property
    def available(self):
        return self.device.connected

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.device.listen(self.async_write_ha_state))
