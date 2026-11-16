"""Bounded validation shared by the WebSocket adapter and command callers."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .const import COMMANDS, MAX_PAYLOAD_BYTES, PROTOCOL

_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
_SECRET = re.compile(r"[0-9a-f]{64}\Z")
_CODE = re.compile(r"[0-9]{6}\Z")
_PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\Z")
_PROFILE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class InvalidPayload(ValueError):
    """Input is not part of the supported protocol."""


def bounded_object(value: object) -> dict:
    if not isinstance(value, dict):
        raise InvalidPayload("Expected an object")
    nodes = 0
    characters = 0

    def count_text(item: str) -> None:
        nonlocal characters
        if len(item) > MAX_PAYLOAD_BYTES:
            raise InvalidPayload("Text value is too large")
        characters += len(item)
        if characters > MAX_PAYLOAD_BYTES:
            raise InvalidPayload("Payload is too large")

    def visit(item: object, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 12_000:
            raise InvalidPayload("Too many payload values")
        if depth > 12:
            raise InvalidPayload("Payload is too deeply nested")
        if isinstance(item, dict):
            if len(item) > 64 or any(not isinstance(key, str) for key in item):
                raise InvalidPayload("Too many object fields")
            for key, child in item.items():
                count_text(key)
                visit(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 512:
                raise InvalidPayload("Too many list items")
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, str):
            count_text(item)
        elif item is not None and type(item) not in (int, float, bool):
            raise InvalidPayload("Unsupported value")
    visit(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as err:
        raise InvalidPayload("Invalid JSON value") from err
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise InvalidPayload("Payload is too large")
    return value


def text(value: object, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise InvalidPayload("Invalid text")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise InvalidPayload("Invalid text")
    return value


def identifier(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InvalidPayload("Invalid identifier")
    return value


def profile_id(value: object) -> str:
    if not isinstance(value, str) or not _PROFILE_ID.fullmatch(value):
        raise InvalidPayload("Invalid profile identifier")
    return value


def secret(value: object) -> str:
    if not isinstance(value, str) or not _SECRET.fullmatch(value):
        raise InvalidPayload("Invalid device credential")
    return value


def boolean(value: object) -> bool:
    if type(value) is not bool:
        raise InvalidPayload("Expected a boolean")
    return value


def integer(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise InvalidPayload("Number outside the supported range")
    return value


def fields(value: dict, allowed: set[str], required: set[str] | None = None) -> None:
    if set(value) - allowed or (required or set()) - set(value):
        raise InvalidPayload("Unexpected or missing fields")


def image_url(value: object) -> str:
    url = text(value, 4096)
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password
        parsed.port
    except ValueError as err:
        raise InvalidPayload("Invalid image URL") from err
    if not valid or "#" in url or any(char.isspace() for char in url) or "\\" in url:
        raise InvalidPayload("Use an HTTP or HTTPS image URL without a login")
    return url


def connection_data(value: object) -> dict:
    data = bounded_object(value)
    fields(data, {"device_id", "device_secret", "name", "protocol", "pairing_code"},
           {"device_id", "device_secret", "name", "protocol"})
    if type(data["protocol"]) is not int or data["protocol"] != PROTOCOL:
        raise InvalidPayload("Unsupported protocol")
    result = {"device_id": identifier(data["device_id"]), "device_secret": secret(data["device_secret"]),
              "name": text(data["name"], 80), "protocol": PROTOCOL}
    if "pairing_code" in data:
        if not isinstance(data["pairing_code"], str) or not _CODE.fullmatch(data["pairing_code"]):
            raise InvalidPayload("Invalid pairing code")
        result["pairing_code"] = data["pairing_code"]
    return result


def profile(value: object) -> dict:
    data = bounded_object(value)
    fields(data, {"id", "name", "child"}, {"id", "name", "child"})
    return {"id": profile_id(data["id"]), "name": text(data["name"], 48), "child": boolean(data["child"])}


def status(value: object) -> dict:
    data = bounded_object(value)
    fields(data, {"version", "profile", "profiles", "current_app", "capabilities"}, {"version", "profile", "profiles", "capabilities"})
    profiles = data["profiles"]
    capabilities = data["capabilities"]
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 12:
        raise InvalidPayload("Invalid profile list")
    if not isinstance(capabilities, list) or len(capabilities) > 32:
        raise InvalidPayload("Invalid capabilities")
    result = {"version": text(data["version"], 64), "profile": profile(data["profile"]),
              "profiles": [profile(item) for item in profiles],
              "capabilities": list(dict.fromkeys(identifier(item) for item in capabilities))}
    ids = [item["id"] for item in result["profiles"]]
    if len(ids) != len(set(ids)) or result["profile"]["id"] not in ids:
        raise InvalidPayload("Inconsistent profile list")
    if data.get("current_app") is not None:
        result["current_app"] = text(data["current_app"], 160, empty=True)
    return result


def notification(value: object) -> dict:
    data = bounded_object(value)
    fields(data, {"id", "title", "message", "image", "actions", "persistent", "timeout"}, {"id", "message"})
    actions = data.get("actions", [])
    if not isinstance(actions, list) or len(actions) > 2:
        raise InvalidPayload("Use up to two notification actions")
    parsed_actions = []
    for action in actions:
        action = bounded_object(action)
        fields(action, {"id", "label"}, {"id", "label"})
        parsed_actions.append({"id": identifier(action["id"]), "label": text(action["label"], 32)})
    if len({item["id"] for item in parsed_actions}) != len(parsed_actions):
        raise InvalidPayload("Notification actions must have distinct identifiers")
    result = {"id": identifier(data["id"]), "message": text(data["message"], 2048, empty=True),
              "title": text(data.get("title", ""), 100, empty=True), "actions": parsed_actions,
              "persistent": boolean(data.get("persistent", False)), "timeout": integer(data.get("timeout", 8), 0, 120)}
    if not result["title"].strip() and not result["message"].strip():
        raise InvalidPayload("Enter a notification title or message")
    if data.get("image"):
        result["image"] = image_url(data["image"])
    return result


def profile_patch(value: object, *, allow_pin: bool = False) -> dict:
    data = bounded_object(value)
    allowed = {"id", "name", "child", "allowed_apps", "avatar"}
    if allow_pin:
        allowed.add("pin")
    fields(data, allowed, {"id"})
    result = {"id": profile_id(data["id"])}
    if "name" in data:
        result["name"] = text(data["name"], 48)
    if "child" in data:
        result["child"] = boolean(data["child"])
    if "allowed_apps" in data:
        apps = data["allowed_apps"]
        if not isinstance(apps, list) or len(apps) > 100 or any(not isinstance(app, str) or len(app) > 256 or not _PACKAGE.fullmatch(app) for app in apps):
            raise InvalidPayload("Invalid allowed app list")
        result["allowed_apps"] = list(dict.fromkeys(apps))
    if "avatar" in data:
        result["avatar"] = None if data["avatar"] is None else image_url(data["avatar"])
    if "pin" in data:
        pin = data["pin"]
        if not isinstance(pin, str) or not re.fullmatch(r"[0-9]{4,12}", pin):
            raise InvalidPayload("Use a four to twelve digit PIN")
        result["pin"] = pin
    return result


def command_data(command: str, value: object, *, allow_pin: bool = False) -> dict:
    if command not in COMMANDS:
        raise InvalidPayload("Unsupported command")
    data = bounded_object(value)
    if command in {"get_status", "list_apps", "list_profiles", "next_wallpaper"}:
        fields(data, set())
    elif command in {"select_profile", "delete_profile"}:
        fields(data, {"id"}, {"id"})
        return {"id": profile_id(data["id"])}
    elif command == "set_profile":
        return profile_patch(data, allow_pin=allow_pin)
    elif command == "notify":
        return notification(data)
    elif command == "set_wallpaper_options":
        fields(data, {"interval_seconds", "shuffle", "dim_amount"})
        if not data:
            raise InvalidPayload("Choose a slideshow setting to change")
        if "interval_seconds" in data:
            integer(data["interval_seconds"], 30, 86400)
        if "shuffle" in data:
            boolean(data["shuffle"])
        if "dim_amount" in data:
            amount = data["dim_amount"]
            if type(amount) not in (int, float) or not 0 <= amount <= .7:
                raise InvalidPayload("Invalid dim amount")
    elif command == "set_wallpapers":
        fields(data, {"photos", "replace"}, {"photos"})
        photos = data["photos"]
        if not isinstance(photos, list) or len(photos) > 500:
            raise InvalidPayload("Choose up to 500 photos")
        return {"photos": list(dict.fromkeys(image_url(item) for item in photos)),
                "replace": boolean(data.get("replace", True))}
    return dict(data)
