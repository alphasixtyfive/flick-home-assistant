from types import SimpleNamespace
import unittest
from custom_components.flick.permissions import management_allowed


class PermissionTests(unittest.TestCase):
    def test_administrative_services_accept_system_automation_context(self):
        self.assertTrue(management_allowed(None, None))

    def test_nonadmin_tv_user_cannot_manage_profiles_or_launcher(self):
        user = SimpleNamespace(id="tv", is_active=True, is_admin=False)
        self.assertFalse(management_allowed("tv", user))

    def test_missing_inactive_or_different_users_cannot_manage(self):
        self.assertFalse(management_allowed("missing", None))
        self.assertFalse(management_allowed("admin", SimpleNamespace(id="admin", is_active=False, is_admin=True)))
        self.assertFalse(management_allowed("tv", SimpleNamespace(id="admin", is_active=True, is_admin=True)))
        self.assertTrue(management_allowed("admin", SimpleNamespace(id="admin", is_active=True, is_admin=True)))
