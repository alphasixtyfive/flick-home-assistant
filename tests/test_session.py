import asyncio
import unittest

from custom_components.flick_tv.protocol import InvalidPayload
from custom_components.flick_tv.session import DeviceRejected, DeviceUnavailable, Registry


def connect_data(device_id="tv-1", *, code="123456", secret="a" * 64):
    data = {"device_id": device_id, "device_secret": secret, "name": "Test TV", "protocol": 1}
    if code is not None:
        data["pairing_code"] = code
    return data


class PairingTests(unittest.TestCase):
    def test_pairing_needs_explicit_approval_and_expires_without_extension(self):
        clock = [100.0]
        registry = Registry(clock=lambda: clock[0])
        first = object()
        result = registry.connect(connect_data(), "tv-user", first, lambda event: None)
        self.assertEqual("pending", result["status"])
        self.assertEqual({}, registry.devices)
        clock[0] = 350
        registry.connect(connect_data(), "tv-user", object(), lambda event: None)
        self.assertEqual(400, registry.pairing("123456").expires)
        clock[0] = 401
        with self.assertRaises(InvalidPayload):
            registry.pairing("123456")

    def test_pairing_codes_are_not_ambiguous_and_pending_state_is_bounded(self):
        registry = Registry()
        registry.connect(connect_data(), "tv-user", object(), lambda event: None)
        with self.assertRaises(InvalidPayload):
            registry.connect(connect_data("tv-2"), "tv-user", object(), lambda event: None)
        for index in range(2, 33):
            registry.connect(connect_data(f"tv-{index}", code=f"{index:06}"), "tv-user", object(), lambda event: None)
        with self.assertRaises(InvalidPayload):
            registry.connect(connect_data("overflow", code="999999"), "tv-user", object(), lambda event: None)

    def test_a_different_user_or_secret_cannot_replace_a_pending_pairing(self):
        registry = Registry()
        registry.connect(connect_data(), "tv-user", object(), lambda event: None)
        for user, secret in (("another-user", "a" * 64), ("tv-user", "b" * 64)):
            with self.assertRaises(InvalidPayload):
                registry.connect(connect_data(secret=secret), user, object(), lambda event: None)

    def test_repair_changes_credentials_only_after_administrator_registration(self):
        registry = Registry()
        old_connection = object()
        registry.connect(connect_data(), "tv-user", old_connection, lambda event: None)
        old = registry.register(registry.pairing("123456").entry_data())
        new_connection = object()
        registry.connect(connect_data(code="654321", secret="b" * 64), "new-user", new_connection, lambda event: None)
        self.assertTrue(old.connected)
        with self.assertRaises(InvalidPayload):
            registry.authorized("tv-1", "b" * 64, "new-user", new_connection)
        new = registry.register(registry.pairing("654321").entry_data())
        self.assertFalse(old.connected)
        self.assertIs(new, registry.authorized("tv-1", "b" * 64, "new-user", new_connection))


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registry = Registry()
        self.connection = object()
        self.events = []
        self.registry.connect(connect_data(), "tv-user", self.connection, self.events.append)
        self.device = self.registry.register(self.registry.pairing("123456").entry_data())
        self.events.clear()

    async def test_result_correlation_and_duplicate_reply(self):
        task = asyncio.create_task(self.device.request("next_wallpaper"))
        await asyncio.sleep(0)
        request_id = self.events[-1]["request_id"]
        self.assertFalse(self.device.receive_result("unknown", True, {}))
        self.assertTrue(self.device.receive_result(request_id, True, {"changed": True}))
        self.assertFalse(self.device.receive_result(request_id, True, {}))
        self.assertEqual({"changed": True}, await task)

    async def test_old_connection_user_and_secret_cannot_submit_replies(self):
        for secret, user, connection in (("b" * 64, "tv-user", self.connection), ("a" * 64, "other", self.connection), ("a" * 64, "tv-user", object())):
            with self.assertRaises(InvalidPayload):
                self.registry.authorized("tv-1", secret, user, connection)

    async def test_reconnect_fails_pending_request_and_old_disconnect_cannot_clear_new_link(self):
        task = asyncio.create_task(self.device.request("next_wallpaper"))
        await asyncio.sleep(0)
        new_connection = object()
        self.registry.connect(connect_data(code=None), "tv-user", new_connection, self.events.append)
        with self.assertRaises(DeviceUnavailable):
            await task
        self.registry.disconnect("tv-1", self.connection)
        self.assertTrue(self.device.connected)
        with self.assertRaises(InvalidPayload):
            self.registry.authorized("tv-1", "a" * 64, "tv-user", self.connection)

    async def test_timeouts_do_not_retry_a_nonidempotent_action(self):
        with self.assertRaises(DeviceUnavailable):
            await self.device.request("next_wallpaper", timeout=.01)
        self.assertEqual(1, len(self.events))
        self.assertEqual({}, self.device._requests)

    async def test_only_an_issued_notification_action_can_fire_once(self):
        data = {"id": "doorbell", "message": "Someone is here", "actions": [{"id": "view", "label": "View"}]}
        task = asyncio.create_task(self.device.request("notify", data))
        await asyncio.sleep(0)
        self.device.receive_result(self.events[-1]["request_id"], True, {})
        await task
        for notice, action in (("unknown", "view"), ("doorbell", "unlock")):
            with self.assertRaises(InvalidPayload):
                self.device.notification_action(notice, action)
        result = self.device.notification_action("doorbell", "view")
        self.assertEqual({"device_id": "tv-1", "notification_id": "doorbell", "action_id": "view"}, result)
        with self.assertRaises(InvalidPayload):
            self.device.notification_action("doorbell", "view")

    async def test_notification_actions_do_not_cross_between_devices(self):
        self.registry.connect(connect_data("tv-2", code="654321", secret="b" * 64), "tv-user", object(), lambda event: None)
        other = self.registry.register(self.registry.pairing("654321").entry_data())
        task = asyncio.create_task(self.device.request("notify", {"id": "notice", "message": "Hi", "actions": [{"id": "view", "label": "View"}]}))
        await asyncio.sleep(0)
        self.device.receive_result(self.events[-1]["request_id"], True, {})
        await task
        with self.assertRaises(InvalidPayload):
            other.notification_action("notice", "view")

    async def test_rejected_notice_does_not_leave_usable_actions(self):
        task = asyncio.create_task(self.device.request("notify", {"id": "notice", "message": "Hi", "actions": [{"id": "view", "label": "View"}]}))
        await asyncio.sleep(0)
        self.device.receive_result(self.events[-1]["request_id"], False, {}, "permission_required")
        with self.assertRaisesRegex(DeviceRejected, "Enable this feature"):
            await task
        with self.assertRaises(InvalidPayload):
            self.device.notification_action("notice", "view")


    async def test_failure_of_old_notice_does_not_remove_replacement_actions(self):
        data = {"id": "same-card", "message": "Hi", "actions": [{"id": "view", "label": "View"}]}
        first = asyncio.create_task(self.device.request("notify", data))
        await asyncio.sleep(0)
        old_id = self.events[-1]["request_id"]
        second = asyncio.create_task(self.device.request("notify", data | {"message": "Updated"}))
        await asyncio.sleep(0)
        new_id = self.events[-1]["request_id"]
        self.device.receive_result(old_id, False, {})
        with self.assertRaises(DeviceRejected):
            await first
        self.device.receive_result(new_id, True, {})
        await second
        self.assertEqual("view", self.device.notification_action("same-card", "view")["action_id"])
