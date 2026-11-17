import heapq
from types import SimpleNamespace
import unittest

from custom_components.flick_tv.home_text import (
    HomeText, HomeTextOptions, home_text_options, rendered_text,
)
from custom_components.flick_tv.protocol import InvalidPayload


class Timer:
    def __init__(self, callback):
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class Clock:
    def __init__(self):
        self.now = 0
        self.queue = []
        self.sequence = 0

    def time(self):
        return self.now

    def call_later(self, delay, callback):
        self.sequence += 1
        timer = Timer(callback)
        heapq.heappush(self.queue, (self.now + delay, self.sequence, timer))
        return timer

    def advance(self, seconds=0):
        until = self.now + seconds
        while self.queue and self.queue[0][0] <= until:
            at, _, timer = heapq.heappop(self.queue)
            self.now = at
            if not timer.cancelled:
                timer.callback()
        self.now = until


class Device:
    def __init__(self):
        self.connected = True
        self.listeners = set()
        self.profiles = [{"id": "adult", "child": False}, {"id": "child", "child": True}]
        self.select("adult")

    def listen(self, callback):
        self.listeners.add(callback)
        return lambda: self.listeners.discard(callback)

    def select(self, profile):
        current = next((item for item in self.profiles if item["id"] == profile), {})
        self.status = {"profile": current, "profiles": self.profiles}
        self.changed()

    def changed(self):
        for listener in tuple(self.listeners):
            listener()


class TrackerFactory:
    def __init__(self):
        self.trackers = []

    def __call__(self, source, observed):
        tracker = SimpleNamespace(source=source, observed=observed, removed=False)
        self.trackers.append(tracker)
        observed("Initial value")

        def remove():
            tracker.removed = True

        return remove


class TextTests(unittest.TestCase):
    def test_message_template_is_bounded(self):
        self.assertEqual(8192, len(HomeTextOptions.parse({"template": "x" * 8192}).template))
        with self.assertRaises(InvalidPayload):
            HomeTextOptions.parse({"template": "x" * 8193})

    def test_all_bidi_controls_are_removed_without_losing_emoji_joiners(self):
        controls = "\u061c\u200e\u200f" + "".join(map(chr, range(0x202a, 0x202f)))
        controls += "".join(map(chr, range(0x2066, 0x2070)))
        self.assertEqual("a👩\u200d👧b", rendered_text("a" + controls + "👩\u200d👧b"))
        self.assertEqual("one two three", rendered_text(" one\u2028 two\u2029\tthree "))

    def test_default_and_damaged_options_fail_closed(self):
        for options in ({}, None, {"home_text": {"show_for_children": "true"}},
                        {"home_text": {"position": "freeform"}}):
            self.assertEqual(HomeTextOptions(), home_text_options(options))

    def test_template_and_options_are_bounded_and_not_exposed_by_repr(self):
        with self.assertRaises(InvalidPayload):
            HomeTextOptions.parse({"template": "x" * 8193})
        with self.assertRaises(InvalidPayload):
            HomeTextOptions.parse({"icon": "anything"})
        self.assertNotIn("private text", repr(HomeTextOptions("private text")))

    def test_normal_text_whitespace_emojis_and_joiners_survive(self):
        self.assertEqual("Cinema 🎬 Family 👩\u200d👧", rendered_text(" Cinema 🎬\r\n Family 👩\u200d👧 "))
        self.assertEqual("<b>Literal text</b>", rendered_text("<b>Literal text</b>"))

    def test_controls_overrides_and_surrogates_are_removed(self):
        self.assertEqual("abc", rendered_text("a\x00\u202eb\ud800c"))

    def test_output_is_limited_by_codepoint_and_line_count(self):
        self.assertEqual(120, len(rendered_text("😀" * 300)))
        self.assertEqual("one two three four", rendered_text("one\ntwo\nthree\nfour"))

    def test_only_text_is_accepted_without_scalar_or_structured_coercion(self):
        for value in (None, 42, 3.5, False, {"text": "no"}, ["no"]):
            with self.assertRaises(InvalidPayload):
                rendered_text(value)


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.device = Device()
        self.factory = TrackerFactory()
        self.options = {"home_text": {"template": "source"}}
        self.home = HomeText(self.device, self.options, self.factory, self.clock)
        self.events = []

    def tearDown(self):
        self.home.close()

    def subscribe(self, profile="adult", generation=1):
        return self.home.subscribe(profile, generation, self.events.append)

    def test_initial_snapshot_is_deferred_until_subscription_acknowledgement(self):
        self.subscribe()
        self.assertEqual([], self.events)
        self.clock.advance()
        self.assertEqual("Initial value", self.events[0]["text"])
        self.assertEqual(("adult", 1, 1), tuple(self.events[0][key] for key in
                         ("profile_id", "profile_generation", "revision")))
        self.assertEqual({"profile_id", "profile_generation", "revision", "text", "show_for_children"},
                         self.events[0].keys())

    def test_invalid_render_clears_message_without_replacing_tracker(self):
        self.subscribe()
        self.clock.advance()
        observed = self.factory.trackers[0].observed
        for invalid in (None, 42, False, {"message": "private"}, ["private"]):
            observed(invalid)
            self.clock.advance(1)
            self.assertEqual("", self.events[-1]["text"])
            observed("Recovered")
            self.clock.advance(1)
            self.assertEqual("Recovered", self.events[-1]["text"])
        self.assertEqual(1, len(self.factory.trackers))
        self.assertTrue(all(set(event) == {"profile_id", "profile_generation", "revision", "text", "show_for_children"}
                            for event in self.events))

    def test_clearing_message_stops_tracker_and_rejects_late_callback(self):
        self.subscribe()
        self.clock.advance()
        old = self.factory.trackers[0]
        self.home.configure({"home_text": {"template": "  "}})
        old.observed("Stale private value")
        self.clock.advance(1)
        self.assertTrue(old.removed)
        self.assertEqual(1, len(self.factory.trackers))
        self.assertEqual("", self.events[-1]["text"])

    def test_status_updates_reuse_the_single_tracker(self):
        self.subscribe()
        self.clock.advance()
        for _ in range(10):
            self.device.changed()
            self.clock.advance(1)
        self.assertEqual(1, len(self.factory.trackers))
        self.assertEqual(1, len(self.events))

    def test_updates_coalesce_latest_value_at_one_second_and_deduplicate(self):
        self.subscribe()
        self.clock.advance()
        observed = self.factory.trackers[-1].observed
        observed("first")
        observed("latest")
        self.clock.advance(.99)
        self.assertEqual(1, len(self.events))
        self.clock.advance(.01)
        self.assertEqual("latest", self.events[-1]["text"])
        self.assertEqual(2, self.events[-1]["revision"])
        observed("latest")
        self.clock.advance(1)
        self.assertEqual(2, len(self.events))

    def test_error_and_empty_output_replace_previous_text(self):
        self.subscribe()
        self.clock.advance()
        observed = self.factory.trackers[-1].observed
        observed("private error", True)
        self.clock.advance(1)
        self.assertEqual("", self.events[-1]["text"])
        observed("Recovered")
        self.clock.advance(1)
        self.assertEqual("Recovered", self.events[-1]["text"])
        observed("  \n ")
        self.clock.advance(1)
        self.assertEqual("", self.events[-1]["text"])

    def test_default_options_hide_without_creating_template_tracker(self):
        self.home.configure({})
        self.subscribe()
        self.clock.advance()
        self.assertEqual([], self.factory.trackers)
        self.assertEqual("", self.events[-1]["text"])

    def test_child_visibility_is_off_by_default(self):
        self.device.select("child")
        self.subscribe("child")
        self.clock.advance()
        self.assertEqual([], self.factory.trackers)
        self.assertEqual("", self.events[-1]["text"])

    def test_child_output_requires_explicit_option_and_current_known_profile(self):
        self.device.select("child")
        self.home.configure({"home_text": {"template": "source", "show_for_children": True}})
        self.subscribe("child")
        self.clock.advance()
        self.assertEqual("Initial value", self.events[-1]["text"])

    def test_profile_mismatch_clears_and_removes_tracker(self):
        self.subscribe()
        self.clock.advance()
        old = self.factory.trackers[-1]
        self.device.select("child")
        old.observed("Late adult value")
        self.clock.advance(1)
        self.assertTrue(old.removed)
        self.assertEqual("", self.events[-1]["text"])

    def test_unknown_profile_waits_for_authoritative_status(self):
        self.subscribe("not_known")
        self.clock.advance()
        self.assertEqual([], self.factory.trackers)
        self.assertEqual("", self.events[-1]["text"])

    def test_subscribing_before_status_refresh_recovers_when_status_matches(self):
        self.device.status = {}
        self.subscribe()
        self.clock.advance()
        self.assertEqual("", self.events[-1]["text"])
        self.device.select("adult")
        self.clock.advance(1)
        self.assertEqual("Initial value", self.events[-1]["text"])

    def test_replaced_subscription_and_late_render_cannot_change_new_output(self):
        old_subscription = self.subscribe()
        old = self.factory.trackers[-1]
        self.subscribe(generation=2)
        old_subscription.close()
        old.observed("Stale adult value")
        self.clock.advance()
        self.assertEqual(2, self.events[-1]["profile_generation"])
        self.assertEqual("Initial value", self.events[-1]["text"])
        self.assertTrue(old.removed)

    def test_options_change_removes_old_tracker_and_publishes_new_text(self):
        self.subscribe()
        self.clock.advance()
        old = self.factory.trackers[-1]
        self.home.configure({"home_text": {"template": "new source"}})
        old.observed("Stale source")
        self.clock.advance(1)
        self.assertTrue(old.removed)
        self.assertEqual("new source", self.factory.trackers[-1].source)
        self.assertEqual("Initial value", self.events[-1]["text"])

    def test_disconnect_cancels_pending_output_and_tracker(self):
        self.subscribe()
        old = self.factory.trackers[-1]
        self.device.connected = False
        self.device.changed()
        old.observed("Late value")
        self.clock.advance(10)
        self.assertEqual([], self.events)
        self.assertTrue(old.removed)

    def test_close_is_idempotent_and_unregisters_device_listener(self):
        self.subscribe()
        self.home.close()
        self.home.close()
        self.clock.advance()
        self.assertEqual(set(), self.device.listeners)
        self.assertTrue(self.factory.trackers[-1].removed)
        self.assertEqual([], self.events)

    def test_failed_send_releases_subscription_and_tracker(self):
        def disconnected(value):
            raise ConnectionError("closed")

        self.home.subscribe("adult", 1, disconnected)
        self.clock.advance()
        self.assertTrue(self.factory.trackers[-1].removed)

    def test_boolean_and_unbounded_generations_are_rejected(self):
        for generation in (True, -1, 2**53):
            with self.assertRaises(InvalidPayload):
                self.subscribe(generation=generation)
