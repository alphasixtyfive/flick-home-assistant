"""Flick launcher integration."""

from __future__ import annotations

from .const import DOMAIN, PLATFORMS


async def async_setup(hass, config):
    from .session import Registry
    from .services import async_register_services
    from .websocket import async_register_commands
    hass.data.setdefault(DOMAIN, Registry())
    async_register_commands(hass)
    async_register_services(hass)
    return True


async def async_setup_entry(hass, entry):
    registry = hass.data[DOMAIN]
    device = registry.register(dict(entry.data))
    entry.runtime_data = device
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        registry.unregister(device.device_id)
        raise
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].unregister(entry.data["device_id"])
    return True
