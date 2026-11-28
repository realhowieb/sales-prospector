import unittest

from auth_system import (
    auth_session_from_response,
    can_create_company,
    can_create_rep,
    is_admin_role,
    normalize_account_role,
    password_problem,
    public_signup_role,
    recovery_pkce_pair,
    recovery_request_path,
)
import base64
import hashlib


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


class PasswordResetTests(unittest.TestCase):
    def test_pkce_pair_is_stable_valid_and_secret_bound(self):
        v1, c1 = recovery_pkce_pair(" Rep@Example.com ", "server-secret")
        v2, _ = recovery_pkce_pair("rep@example.com", "server-secret")
        self.assertEqual(v1, v2)  # same email (normalized) rebuilds the verifier in a new tab
        self.assertTrue(43 <= len(v1) <= 128)
        expected = base64.urlsafe_b64encode(hashlib.sha256(v1.encode()).digest()).decode().rstrip("=")
        self.assertEqual(c1, expected)  # S256 challenge
        self.assertNotEqual(v1, recovery_pkce_pair("rep@example.com", "other-secret")[0])
        self.assertNotEqual(v1, recovery_pkce_pair("other@example.com", "server-secret")[0])

    def test_recovery_path_redirects_to_reset_page(self):
        self.assertEqual(recovery_request_path(""), "recover")
        self.assertEqual(recovery_request_path("https://firstknock.hsfinest.com/"),
                         "recover?redirect_to=https%3A%2F%2Ffirstknock.hsfinest.com%2F%3Freset_password%3D1")

    def test_password_problem(self):
        self.assertIn("8", password_problem("short", "short"))
        self.assertIn("match", password_problem("longenough1", "longenough2"))
        self.assertEqual(password_problem("longenough1", "longenough1"), "")


if __name__ == "__main__":
    unittest.main()
