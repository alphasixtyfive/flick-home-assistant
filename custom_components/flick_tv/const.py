"""Flick's integration and wire-protocol limits."""

DOMAIN = "flick_tv"
PROTOCOL = 1
COMMAND_TIMEOUT = 10
PAIRING_TTL = 300
MAX_PAYLOAD_BYTES = 524_288
MAX_PENDING_PAIRINGS = 32
MAX_PENDING_COMMANDS = 16
COMMANDS = frozenset({
    "get_status", "list_apps", "list_profiles", "set_profile", "delete_profile",
    "select_profile", "next_wallpaper", "set_wallpaper_options", "set_wallpapers", "notify",
    "list_themes", "set_theme",
})
PLATFORMS = ("binary_sensor", "button", "notify", "select", "sensor")
