"""Administrative actions are separate from ordinary TV entity controls."""


def management_allowed(user_id: str | None, user: object | None) -> bool:
    if user_id is None:
        return True  # Home Assistant automation/system context.
    return bool(user is not None and getattr(user, "id", None) == user_id
                and getattr(user, "is_active", False) and getattr(user, "is_admin", False))


async def async_require_management(hass, context) -> None:
    from homeassistant.exceptions import Unauthorized
    user_id = context.user_id
    user = await hass.auth.async_get_user(user_id) if user_id is not None else None
    if not management_allowed(user_id, user):
        raise Unauthorized(permission="manage_flick_tv")
