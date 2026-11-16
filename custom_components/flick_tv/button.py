"""One-shot actions for a paired TV."""

from uuid import uuid4

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from .entity import FlickEntity
from .services import async_request


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([NextWallpaper(entry.runtime_data), TestNotification(entry.runtime_data)])


class NextWallpaper(FlickEntity, ButtonEntity):
    _attr_name = "Next wallpaper"
    _attr_icon = "mdi:image-multiple"

    def __init__(self, device):
        super().__init__(device, "next_wallpaper")

    async def async_press(self):
        await async_request(self.device, "next_wallpaper")


class TestNotification(FlickEntity, ButtonEntity):
    _attr_name = "Test notification"
    _attr_icon = "mdi:bell-check-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device):
        super().__init__(device, "test_notification")

    async def async_press(self):
        await async_request(self.device, "notify", {
            "id": str(uuid4()),
            "title": "Flick",
            "message": "Notifications are working on this TV.",
            "timeout": 8,
            "persistent": False,
        })
