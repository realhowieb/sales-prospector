import unittest

from profile_claims import build_profile_claim_payload, is_claimable_rep, normalize_claim_status, normalize_rep_db_id


class ProfileClaimsTests(unittest.TestCase):
    def test_normalizes_live_rep_ids(self):
        self.assertEqual(normalize_rep_db_id("db-42"), 42)
        self.assertEqual(normalize_rep_db_id(17), 17)
        self.assertIsNone(normalize_rep_db_id("r1"))

    def test_only_unclaimed_live_profiles_are_claimable(self):
        self.assertTrue(is_claimable_rep({"id": "db-42", "claimed": False}))
        self.assertFalse(is_claimable_rep({"id": "db-42", "claimed": True}))
        self.assertFalse(is_claimable_rep({"id": "sample-1", "claimed": False}))

    def test_build_payload_requires_business_email_shape(self):
        payload = build_profile_claim_payload({"id": "db-42"}, "OWNER@Example.COM", "Owner", "Please verify")
        self.assertEqual(payload.rep_id, 42)
        self.assertEqual(payload.claimant_email, "owner@example.com")
        self.assertEqual(payload.status, "pending")
        with self.assertRaises(ValueError):
            build_profile_claim_payload({"id": "db-42"}, "not-an-email")

    def test_claim_status_defaults_to_pending(self):
        self.assertEqual(normalize_claim_status("approved"), "approved")
        self.assertEqual(normalize_claim_status("rejected"), "rejected")
        self.assertEqual(normalize_claim_status("weird"), "pending")


if __name__ == "__main__":
    unittest.main()
