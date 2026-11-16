import math
import unittest
from unittest.mock import patch
from custom_components.flick_tv.const import MAX_PAYLOAD_BYTES

from custom_components.flick_tv.protocol import (
    InvalidPayload, bounded_object, command_data, connection_data, notification, profile_patch, status,
)


def valid_status():
    profile = {"id": "adult-1", "name": "Viewer", "child": False}
    return {"version": "0.1.0", "profile": profile, "profiles": [profile], "capabilities": ["get_status"]}


class ProtocolTests(unittest.TestCase):
    def test_profile_count_matches_the_launcher_limit(self):
        data = valid_status()
        data["profiles"] = [{"id": f"viewer-{i}", "name": "Viewer", "child": False} for i in range(13)]
        data["profile"] = data["profiles"][0]
        with self.assertRaises(InvalidPayload):
            status(data)
        data["profiles"].pop()
        self.assertEqual(12, len(status(data)["profiles"]))

    def test_command_names_and_unknown_fields_are_not_remote_execution(self):
        for command, payload in [("shell", {"command": "echo secret"}), ("select_profile", {"id": "one", "pin": "1234"}),
                                 ("next_wallpaper", {"service": "anything"})]:
            with self.assertRaises(InvalidPayload):
                command_data(command, payload)

    def test_service_profile_updates_cannot_accept_pin_or_hash(self):
        for field in ("pin", "pin_hash", "pin_salt"):
            with self.assertRaises(InvalidPayload):
                profile_patch({"id": "one", field: "1234"})
        self.assertEqual("1234", profile_patch({"id": "one", "pin": "1234"}, allow_pin=True)["pin"])
        with self.assertRaises(InvalidPayload):
            profile_patch({"id": "one", "pin": "not digits"}, allow_pin=True)

    def test_profile_allowlist_matches_launcher_limit(self):
        self.assertEqual(100, len(profile_patch({"id": "one", "allowed_apps": [f"app.test{i}" for i in range(100)]})["allowed_apps"]))
        with self.assertRaises(InvalidPayload):
            profile_patch({"id": "one", "allowed_apps": [f"app.test{i}" for i in range(101)]})

    def test_parent_pin_is_exactly_four_digits_and_preserves_leading_zeroes(self):
        self.assertEqual("0123", profile_patch({"id": "one", "pin": "0123"}, allow_pin=True)["pin"])
        for pin in ("123", "12345", "12x4", "", 1234):
            with self.assertRaises(InvalidPayload):
                profile_patch({"id": "one", "pin": pin}, allow_pin=True)

    def test_notification_actions_are_small_named_choices_not_services(self):
        data = {"id": "notice", "message": "Someone is here", "actions": [{"id": "view", "label": "View"}]}
        self.assertEqual(8, notification(data)["timeout"])
        with self.assertRaises(InvalidPayload):
            notification(data | {"actions": [{"id": "view", "label": "View", "service": "anything"}]})
        with self.assertRaises(InvalidPayload):
            notification(data | {"actions": [{"id": str(i), "label": "Choice"} for i in range(3)]})
        with self.assertRaises(InvalidPayload):
            notification(data | {"timeout": True})

    def test_remote_photos_reject_credentials_and_local_files(self):
        for image in ("file:///private/photo.jpg", "content://documents/private", "https://user:pass@example.test/a.jpg"):
            with self.assertRaises(InvalidPayload):
                command_data("set_wallpapers", {"photos": [image]})
        self.assertEqual(["https://images.example.test/a.jpg"], command_data("set_wallpapers", {"photos": ["https://images.example.test/a.jpg"]})["photos"])

    def test_byte_depth_and_nonfinite_limits_are_enforced(self):
        with self.assertRaises(InvalidPayload):
            bounded_object({"text": "x" * 524_288})
        with self.assertRaises(InvalidPayload):
            bounded_object({"temperature": math.nan})
        nested = {}
        for _ in range(14):
            nested = {"nested": nested}
        with self.assertRaises(InvalidPayload):
            bounded_object(nested)

    def test_status_cannot_export_profile_credentials_or_unknown_fields(self):
        self.assertEqual("Viewer", status(valid_status())["profile"]["name"])
        data = valid_status()
        data["profiles"] = [data["profile"] | {"pin_hash": "private"}]
        with self.assertRaises(InvalidPayload):
            status(data)
        with self.assertRaises(InvalidPayload):
            status(valid_status() | {"device_secret": "private"})

    def test_protocol_and_secret_are_exact(self):
        data = {"device_id": "device-1", "device_secret": "a" * 64, "name": "TV", "protocol": 1}
        self.assertEqual(1, connection_data(data)["protocol"])
        for patch in ({"protocol": True}, {"protocol": 2}, {"device_secret": "too-short"}):
            with self.assertRaises(InvalidPayload):
                connection_data(data | patch)


class AlignedBoundsTests(unittest.TestCase):
    def test_title_only_notifications_and_exact_text_bounds(self):
        self.assertEqual("", notification({"id": "one", "title": "Hello", "message": ""})["message"])
        notification({"id": "one", "message": "a" * 2048, "title": "b" * 100})
        for data in ({"id": "one", "message": "", "title": ""}, {"id": "one", "message": "a" * 2049},
                     {"id": "one", "message": "Hi", "title": "b" * 101}):
            with self.assertRaises(InvalidPayload):
                notification(data)

    def test_profile_and_package_grammar_are_not_general_command_identifiers(self):
        for data in ({"id": "bad:id"}, {"id": "one", "allowed_apps": ["app.2invalid"]}):
            with self.assertRaises(InvalidPayload):
                profile_patch(data)
        self.assertEqual("profile_1", profile_patch({"id": "profile_1", "allowed_apps": ["app._valid"]})["id"])

    def test_remote_image_fragments_and_empty_wallpaper_patches_are_rejected(self):
        with self.assertRaises(InvalidPayload):
            command_data("set_wallpapers", {"photos": ["https://images.example.test/a.jpg#fragment"]})
        with self.assertRaises(InvalidPayload):
            command_data("set_wallpaper_options", {})


class TraversalBudgetTests(unittest.TestCase):
    def test_malformed_unicode_stays_on_the_sanitized_error_path(self):
        with self.assertRaisesRegex(InvalidPayload, "Invalid JSON value"):
            bounded_object({"name": "\ud800"})

    def test_wide_nested_arrays_stop_at_the_global_node_limit(self):
        # Every list and the encoded message fit their individual limits.
        value = {"rows": [[None] * 512 for _ in range(24)]}
        with patch("custom_components.flick_tv.protocol.json.dumps") as encode:
            with self.assertRaises(InvalidPayload):
                bounded_object(value)
            encode.assert_not_called()

    def test_oversized_strings_are_rejected_before_serialization(self):
        with patch("custom_components.flick_tv.protocol.json.dumps") as encode:
            with self.assertRaises(InvalidPayload):
                bounded_object({"message": "x" * (MAX_PAYLOAD_BYTES + 1)})
            encode.assert_not_called()

    def test_shared_strings_cannot_expand_into_a_huge_serialized_message(self):
        # Reusing one input string is cheap; serializing every reference would not be.
        value = {"rows": ["x" * (MAX_PAYLOAD_BYTES // 4)] * 8}
        with patch("custom_components.flick_tv.protocol.json.dumps") as encode:
            with self.assertRaises(InvalidPayload):
                bounded_object(value)
            encode.assert_not_called()
