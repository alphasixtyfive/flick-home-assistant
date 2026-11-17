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
    import asyncio
    from .home_text import HomeText
    from .home_text_template import track_home_text

    registry = hass.data[DOMAIN]
    device = registry.register(dict(entry.data))
    manager = HomeText(device, entry.options,
        lambda source, observed: track_home_text(hass, source, observed), asyncio.get_running_loop())
    device.home_text = manager
    entry.runtime_data = device
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        manager.close()
        device.home_text = None
        registry.unregister(device.device_id)
        raise
    return True


async def async_options_updated(hass, entry):
    device = getattr(entry, "runtime_data", None)
    if device is not None and device.home_text is not None:
        device.home_text.configure(entry.options)


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    device = getattr(entry, "runtime_data", None)
    if device is not None and device.home_text is not None:
        device.home_text.close()
        device.home_text = None
    hass.data[DOMAIN].unregister(entry.data["device_id"])
    return True
