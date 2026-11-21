"""Standard message/title notifications; rich cards use flick_tv.notify."""

from uuid import uuid4
from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from .entity import FlickEntity
from .services import async_request


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FlickNotify(entry.runtime_data)])


class FlickNotify(FlickEntity, NotifyEntity):
    _attr_name = "Notification"
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, device):
        super().__init__(device, "notification")

    async def async_send_message(self, message, title=None):
        await async_request(self.device, "notify", {"id": str(uuid4()), "message": message, "title": title or ""})
