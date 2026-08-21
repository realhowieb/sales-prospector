import unittest

from auth_system import (
    auth_session_from_response,
    can_create_company,
    can_create_rep,
    is_admin_role,
    normalize_account_role,
    public_signup_role,
)


class AuthSystemTest(unittest.TestCase):
    def test_public_signup_role_never_returns_admin(self):
        self.assertEqual(public_signup_role("admin"), "rep")
        self.assertEqual(public_signup_role("company"), "company")

    def test_normalize_account_role_defaults_safely(self):
        self.assertEqual(normalize_account_role("ADMIN"), "admin")
        self.assertEqual(normalize_account_role("unknown"), "rep")
        self.assertEqual(normalize_account_role(None), "rep")

    def test_create_permissions_follow_role(self):
        self.assertTrue(can_create_rep("rep"))
        self.assertFalse(can_create_rep("company"))
        self.assertTrue(can_create_company("company"))
        self.assertFalse(can_create_company("rep"))
        self.assertTrue(can_create_rep("admin"))
        self.assertTrue(can_create_company("admin"))

    def test_admin_requires_verified_admin_table_check(self):
        self.assertFalse(is_admin_role("admin", False))
        self.assertFalse(is_admin_role("rep", True))
        self.assertTrue(is_admin_role("admin", True))

    def test_session_response_supports_nested_supabase_session(self):
        session = auth_session_from_response({
            "user": {"id": "user-1", "email": "Rep@Example.com"},
            "session": {"access_token": "token", "refresh_token": "refresh"},
        })
        self.assertEqual(session.user_id, "user-1")
        self.assertEqual(session.email, "rep@example.com")
        self.assertEqual(session.access_token, "token")


if __name__ == "__main__":
    unittest.main()
