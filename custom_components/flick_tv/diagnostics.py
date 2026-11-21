"""Diagnostics deliberately omit identities, pairing data and household state."""

async def async_get_config_entry_diagnostics(hass, entry):
    device = entry.runtime_data
    return {"protocol": 1, "connected": device.connected,
            "version": device.status.get("version"), "capabilities": device.status.get("capabilities", []),
            "profile_count": len(device.status.get("profiles", []))}
