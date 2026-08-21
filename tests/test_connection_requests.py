import unittest

from connection_requests import (
    build_connection_payload,
    contact_visible,
    duplicate_open_connection,
    normalize_connection_status,
    normalize_prefixed_id,
)


class ConnectionRequestsTests(unittest.TestCase):
    def test_builds_pending_company_connection_payload(self):
        payload = build_connection_payload(
            company_id="co-12",
            rep_id="db-45",
            opportunity_id="opp-7",
            message="Interested in a Bay Area security line",
        )

        self.assertEqual(payload.company_id, 12)
        self.assertEqual(payload.rep_id, 45)
        self.assertEqual(payload.opportunity_id, 7)
        self.assertEqual(payload.status, "pending")
        self.assertEqual(payload.initiated_by, "company")

    def test_normalizes_non_database_demo_ids(self):
        self.assertEqual(normalize_prefixed_id("local-company-1", "co-"), "local-company-1")
        self.assertEqual(normalize_prefixed_id("r1", "db-"), "r1")

    def test_status_defaults_to_pending(self):
        self.assertEqual(normalize_connection_status("accepted"), "accepted")
        self.assertEqual(normalize_connection_status("declined"), "declined")
        self.assertEqual(normalize_connection_status("bad"), "pending")

    def test_duplicate_open_connection_ignores_closed_requests(self):
        connections = [
            {"company_id": 1, "rep_id": 2, "opportunity_id": None, "status": "declined"},
            {"company_id": 1, "rep_id": 2, "opportunity_id": None, "status": "pending"},
        ]

        duplicate = duplicate_open_connection(connections, company_id="co-1", rep_id="db-2")
        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate["status"], "pending")
        self.assertIsNone(duplicate_open_connection(connections, company_id="co-1", rep_id="db-3"))

    def test_contact_visible_only_after_acceptance(self):
        self.assertTrue(contact_visible({"status": "accepted"}))
        self.assertFalse(contact_visible({"status": "pending"}))
        self.assertFalse(contact_visible({"status": "withdrawn"}))


if __name__ == "__main__":
    unittest.main()
