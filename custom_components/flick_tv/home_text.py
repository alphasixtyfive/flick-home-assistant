"""An optional plain-text message on a paired TV."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from .protocol import InvalidPayload, boolean, integer, profile_id

MAX_TEMPLATE_LENGTH = 8192
MAX_GENERATION = 2**53 - 1
_BIDI_OVERRIDES = {"\u061c", "\u200e", "\u200f", *map(chr, range(0x202a, 0x202f)),
                   *map(chr, range(0x2066, 0x2070))}


@dataclass(frozen=True, repr=False)
class HomeTextOptions:
    template: str = ""
    show_for_children: bool = False

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or value.keys() - {"template", "show_for_children"}:
            raise InvalidPayload("Invalid home screen text options")
        source = value.get("template", "")
        if not isinstance(source, str) or len(source) > MAX_TEMPLATE_LENGTH:
            raise InvalidPayload("Invalid home screen text template")
        return cls(source, boolean(value.get("show_for_children", False)))

    def as_dict(self):
        return {"template": self.template, "show_for_children": self.show_for_children}


def home_text_options(options):
    """Damaged or unsupported stored options disable output."""
    try:
        return HomeTextOptions.parse(options.get("home_text", {}))
    except (InvalidPayload, AttributeError):
        return HomeTextOptions()


def rendered_text(value):
    """Display one bounded line literally, with no markup or scalar coercion."""
    if not isinstance(value, str):
        raise InvalidPayload("Home screen text must render as text")
    characters = []
    for char in value[:4096]:
        if char.isspace():
            characters.append(" ")
        elif unicodedata.category(char) not in {"Cc", "Cs"} and char not in _BIDI_OVERRIDES:
            characters.append(char)
    return " ".join("".join(characters).split())[:120].strip()


class HomeText:
    """One renderer and one coalesced, profile-scoped subscription."""

    def __init__(self, device, options, tracker_factory, loop):
        self._device = device
        self._options = home_text_options(options)
        self._tracker_factory = tracker_factory
        self._loop = loop
        self._subscription = None
        self._tracker = None
        self._render_generation = 0
        self._rendered = ""
        self._timer = None
        self._last_sent = None
        self._last_payload = None
        self._revision = 0
        self._closed = False
        self._remove_listener = device.listen(self._device_changed)

    def subscribe(self, selected_profile, generation, send):
        selected_profile = profile_id(selected_profile)
        generation = integer(generation, 0, MAX_GENERATION)
        if self._closed or not self._device.connected:
            raise InvalidPayload("TV is offline")
        if self._subscription is not None:
            self._subscription.close()
        subscription = HomeTextSubscription(self, selected_profile, generation, send)
        self._subscription = subscription
        self._last_sent = None
        self._last_payload = None
        self._revision = 0
        self._refresh()
        return subscription

    def configure(self, options):
        self._options = home_text_options(options)
        self._stop_tracker()
        self._refresh()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._remove_listener()
        if self._subscription is not None:
            self._subscription.close()
        self._stop_tracker()

    def _unsubscribe(self, subscription):
        if self._subscription is not subscription:
            return
        self._subscription = None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._stop_tracker()

    def _device_changed(self):
        if not self._device.connected:
            if self._subscription is not None:
                self._subscription.close()
            return
        self._refresh()

    def _allowed(self):
        subscription = self._subscription
        if subscription is None or not self._device.connected:
            return False
        current = self._device.status.get("profile", {})
        known = next((item for item in self._device.status.get("profiles", [])
                      if item.get("id") == subscription.profile_id), None)
        if known is None or current.get("id") != subscription.profile_id:
            return False
        child = current.get("child") is not False or known.get("child") is not False
        return not child or self._options.show_for_children

    def _refresh(self):
        if self._closed or self._subscription is None:
            return
        if not self._allowed() or not self._options.template.strip():
            self._stop_tracker()
        elif self._tracker is None:
            self._start_tracker()
        self._publish_later()

    def _start_tracker(self):
        self._render_generation += 1
        generation = self._render_generation

        def observed(value, error=False):
            if generation != self._render_generation or self._subscription is None:
                return
            try:
                self._rendered = "" if error else rendered_text(value)
            except InvalidPayload:
                self._rendered = ""
            self._publish_later()

        self._tracker = self._tracker_factory(self._options.template, observed)

    def _stop_tracker(self):
        self._render_generation += 1
        self._rendered = ""
        tracker, self._tracker = self._tracker, None
        if tracker is not None:
            tracker()

    def _publish_later(self):
        if self._subscription is None or self._timer is not None:
            return
        delay = 0 if self._last_sent is None else max(0, 1 - (self._loop.time() - self._last_sent))
        self._timer = self._loop.call_later(delay, self._publish)

    def _publish(self):
        self._timer = None
        subscription = self._subscription
        if subscription is None or self._closed or not self._device.connected:
            return
        allowed = self._allowed()
        payload = {"profile_id": subscription.profile_id,
                   "profile_generation": subscription.generation,
                   "text": self._rendered if allowed else "",
                   "show_for_children": self._options.show_for_children}
        if payload == self._last_payload:
            return
        self._revision += 1
        try:
            subscription.send({**payload, "revision": self._revision})
        except Exception:
            subscription.close()
            return
        self._last_payload = payload
        self._last_sent = self._loop.time()


class HomeTextSubscription:
    def __init__(self, owner, selected_profile, generation, send):
        self._owner = owner
        self.profile_id = selected_profile
        self.generation = generation
        self.send = send

    def close(self):
        self._owner._unsubscribe(self)
