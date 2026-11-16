"""One-shot actions for a paired TV."""

from homeassistant.components.button import ButtonEntity
from .entity import FlickEntity
from .services import async_request


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([NextWallpaper(entry.runtime_data)])


class NextWallpaper(FlickEntity, ButtonEntity):
    _attr_name = "Next wallpaper"
    _attr_icon = "mdi:image-multiple"

    def __init__(self, device):
        super().__init__(device, "next_wallpaper")

    async def async_press(self):
        await async_request(self.device, "next_wallpaper")
