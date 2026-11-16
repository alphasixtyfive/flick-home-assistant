import unittest

from custom_components.flick_tv.profile_options import app_options


class ProfileAppOptionsTests(unittest.TestCase):
    def test_allowed_uninstalled_app_remains_a_selectable_choice(self):
        choices = app_options(
            [{"value": "app.installed", "label": "Installed app"}],
            ["app.installed", "app.removed"],
        )
        self.assertEqual(
            [
                {"value": "app.installed", "label": "Installed app"},
                {"value": "app.removed", "label": "app.removed (not installed)"},
            ],
            choices,
        )

    def test_duplicates_keep_the_installed_label_and_order(self):
        choices = app_options(
            [
                {"value": "app.first", "label": "First"},
                {"value": "app.second", "label": "Second"},
                {"value": "app.first", "label": "Duplicate"},
            ],
            ["app.second", "app.removed", "app.removed"],
        )
        self.assertEqual(["app.first", "app.second", "app.removed"], [x["value"] for x in choices])
        self.assertEqual("First", choices[0]["label"])

    def test_new_profile_offers_only_installed_apps(self):
        self.assertEqual([], app_options([], []))
        installed = [{"value": "app.installed", "label": "Installed app"}]
        self.assertEqual(installed, app_options(installed, []))

    def test_returned_options_do_not_modify_the_catalogue(self):
        installed = [{"value": "app.installed", "label": "Installed app"}]
        allowed = ["app.removed"]
        choices = app_options(installed, allowed)
        choices[0]["label"] = "Edited label"
        self.assertEqual([{"value": "app.installed", "label": "Installed app"}], installed)
        self.assertEqual(["app.removed"], allowed)
