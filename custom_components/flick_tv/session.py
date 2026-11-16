"""In-memory pairing and bounded request/reply sessions; no Home Assistant imports."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import hmac
import time
from uuid import uuid4

from .const import COMMAND_TIMEOUT, MAX_PENDING_COMMANDS, MAX_PENDING_PAIRINGS, PAIRING_TTL, PROTOCOL
from .protocol import InvalidPayload, bounded_object, command_data, connection_data, identifier, secret, status


class DeviceUnavailable(ConnectionError):
    """The TV cannot complete a request on its current connection."""


class DeviceRejected(RuntimeError):
    """The TV declined a validated request."""


@dataclass(repr=False)
class PendingPairing:
    device_id: str
    name: str
    user_id: str
    device_secret: str = field(repr=False)
    code: str = field(repr=False)
    expires: float
    transport: object = field(repr=False)
    send: Callable[[dict], None] = field(repr=False)

    def entry_data(self) -> dict:
        return {"device_id": self.device_id, "name": self.name, "user_id": self.user_id,
                "device_secret": self.device_secret, "protocol": PROTOCOL}


class Device:
    def __init__(self, entry_data: dict) -> None:
        self.device_id = identifier(entry_data["device_id"])
        self.name = entry_data["name"]
        self.user_id = entry_data["user_id"]
        self._secret = secret(entry_data["device_secret"])
        self.transport: object | None = None
        self._send: Callable[[dict], None] | None = None
        self.status: dict = {}
        self._listeners: set[Callable[[], None]] = set()
        self._requests: dict[str, asyncio.Future[dict]] = {}
        self._notices: dict[str, tuple[float, set[str]]] = {}

    @property
    def connected(self) -> bool:
        return self._send is not None

    def matches(self, user_id: str, device_secret: str) -> bool:
        return self.user_id == user_id and hmac.compare_digest(self._secret, device_secret)

    def authorize(self, user_id: str, device_secret: str, transport: object) -> None:
        if not self.matches(user_id, device_secret) or self.transport is not transport or not self.connected:
            raise InvalidPayload("This connection is not paired with the TV")

    def listen(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def _changed(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def attach(self, transport: object, send: Callable[[dict], None]) -> None:
        self.disconnect()
        self.transport, self._send = transport, send
        self._changed()

    def disconnect(self, transport: object | None = None) -> None:
        if transport is not None and self.transport is not transport:
            return
        self.transport, self._send = None, None
        for pending in self._requests.values():
            if not pending.done():
                pending.set_exception(DeviceUnavailable("TV disconnected"))
        self._requests.clear()
        self._changed()

    def receive_status(self, value: dict) -> None:
        latest = status(value)
        self.status = latest
        self._changed()

    async def request(self, command: str, data: dict | None = None, *, allow_pin: bool = False,
                      timeout: float = COMMAND_TIMEOUT) -> dict:
        payload = command_data(command, data or {}, allow_pin=allow_pin)
        if not self.connected or self._send is None:
            raise DeviceUnavailable("TV is offline")
        if len(self._requests) >= MAX_PENDING_COMMANDS:
            raise DeviceUnavailable("TV already has too many pending requests")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._requests[request_id] = future
        notice = self._remember_notification(payload) if command == "notify" else None
        try:
            self._send({"type": "command", "protocol": PROTOCOL, "request_id": request_id,
                        "command": command, "data": payload})
            async with asyncio.timeout(timeout):
                return await future
        except TimeoutError as err:
            if command == "notify":
                self._forget_notification(payload["id"], notice)
            raise DeviceUnavailable("TV did not respond in time") from err
        except (DeviceUnavailable, DeviceRejected, asyncio.CancelledError):
            if command == "notify":
                self._forget_notification(payload["id"], notice)
            raise
        except Exception as err:
            if command == "notify":
                self._forget_notification(payload["id"], notice)
            raise DeviceUnavailable("TV connection could not accept the request") from err
        finally:
            self._requests.pop(request_id, None)


    def receive_result(self, request_id: str, success: bool, value: dict, error: str | None = None) -> bool:
        future = self._requests.get(identifier(request_id))
        if future is None or future.done():
            return False
        data = bounded_object(value)
        if type(success) is not bool:
            raise InvalidPayload("Invalid result")
        if success:
            future.set_result(data)
        else:
            message = "Enable this feature in Flick's Home Assistant settings on the TV" if error == "permission_required" else "TV rejected the request"
            future.set_exception(DeviceRejected(message))
        return True

    def _forget_notification(self, notification_id, notice):
        if self._notices.get(notification_id) is notice:
            self._notices.pop(notification_id, None)

    def _remember_notification(self, data: dict):
        now = time.monotonic()
        self._notices = {key: notice for key, notice in self._notices.items() if notice[0] > now}
        self._notices.pop(data["id"], None)
        while len(self._notices) >= 64:
            self._notices.pop(next(iter(self._notices)))
        lifetime = 86400 if data["persistent"] or data["timeout"] == 0 else data["timeout"] + 30
        notice = (now + lifetime, {action["id"] for action in data["actions"]})
        self._notices[data["id"]] = notice
        return notice

    def notification_action(self, notification_id: str, action_id: str) -> dict:
        notification_id, action_id = identifier(notification_id), identifier(action_id)
        notice = self._notices.get(notification_id)
        if notice is None or notice[0] <= time.monotonic() or action_id not in notice[1]:
            raise InvalidPayload("Unknown notification action")
        # One accepted action per displayed notice; repeated packets cannot fire it again.
        self._notices.pop(notification_id, None)
        return {"device_id": self.device_id, "notification_id": notification_id, "action_id": action_id}


class Registry:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.devices: dict[str, Device] = {}
        self.pending: dict[str, PendingPairing] = {}
        self.clock = clock

    def connect(self, value: dict, user_id: str, transport: object, send: Callable[[dict], None]) -> dict:
        data = connection_data(value)
        device_id = data["device_id"]
        device = self.devices.get(device_id)
        if device is not None and device.transport is transport and device.connected:
            raise InvalidPayload("This TV already has a subscription on this connection")
        if device is not None and device.matches(user_id, data["device_secret"]):
            device.attach(transport, send)
            return {"status": "connected", "protocol": PROTOCOL}
        self._expire()
        code = data.get("pairing_code")
        if not code:
            raise InvalidPayload("Pair this TV in Home Assistant settings")
        existing = self.pending.get(device_id)
        if existing is not None and existing.transport is transport:
            raise InvalidPayload("This TV already has a pending subscription on this connection")
        if existing and (existing.user_id != user_id or not hmac.compare_digest(existing.device_secret, data["device_secret"])):
            raise InvalidPayload("A different pairing is already pending for this device")
        if any(item.code == code and item.device_id != device_id for item in self.pending.values()):
            raise InvalidPayload("Generate a different pairing code on the TV")
        if existing is None and len(self.pending) >= MAX_PENDING_PAIRINGS:
            raise InvalidPayload("Too many pending TVs; try again later")
        expires = existing.expires if existing and existing.code == code else self.clock() + PAIRING_TTL
        self.pending[device_id] = PendingPairing(device_id, data["name"], user_id, data["device_secret"], code, expires, transport, send)
        return {"status": "pending", "protocol": PROTOCOL, "expires_in": max(0, int(expires - self.clock()))}

    def pairing(self, code: str) -> PendingPairing:
        self._expire()
        matches = [pending for pending in self.pending.values() if hmac.compare_digest(pending.code, code)]
        if len(matches) != 1:
            raise InvalidPayload("Pairing code expired or was not found")
        return matches[0]

    def register(self, entry_data: dict) -> Device:
        device = Device(entry_data)
        previous = self.devices.pop(device.device_id, None)
        if previous:
            previous.disconnect()
        self.devices[device.device_id] = device
        self._expire()
        pending = self.pending.get(device.device_id)
        if pending and device.matches(pending.user_id, pending.device_secret):
            self.pending.pop(device.device_id)
            device.attach(pending.transport, pending.send)
            try:
                pending.send({"type": "paired", "protocol": PROTOCOL})
            except Exception:
                # The administrator approved the credentials; a closed socket can reconnect.
                device.disconnect(pending.transport)
        return device

    def unregister(self, device_id: str) -> None:
        if device := self.devices.pop(device_id, None):
            device.disconnect()

    def disconnect(self, device_id: str, transport: object) -> None:
        pending = self.pending.get(device_id)
        if pending and pending.transport is transport:
            self.pending.pop(device_id, None)
        if device := self.devices.get(device_id):
            device.disconnect(transport)

    def authorized(self, device_id: str, device_secret: str, user_id: str, transport: object) -> Device:
        device = self.devices.get(identifier(device_id))
        if device is None:
            raise InvalidPayload("TV is not paired")
        device.authorize(user_id, secret(device_secret), transport)
        return device

    def _expire(self) -> None:
        self.pending = {key: value for key, value in self.pending.items() if value.expires > self.clock()}
