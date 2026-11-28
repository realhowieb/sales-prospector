import unittest
from datetime import datetime, timezone
from unittest import mock

try:
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    import api.main as main
    from api import digest
    from api.auth import User, current_user, get_settings
    from api.settings import Settings

    SETTINGS = Settings("https://sb.example", "anon", "service", ("http://localhost:3000",),
                        smtp_host="smtp.example", smtp_pass="pw", smtp_from="FirstKnock <noreply@example.com>",
                        digest_secret="s3cret", web_url="https://fk.example")
except ImportError:  # API deps are optional for the Streamlit app's CI
    digest = None

PERMIT = {"Permit #": "26BLD-1", "Business": "Rivian", "Signal": "Move-in (U&O)", "Address": "620 Hansen Wy",
          "Value": 1_500_000, "Opened": "2026-10-23", "Contractor": "Acme", "Map": "https://maps.example/1"}
PERMIT2 = dict(PERMIT, **{"Permit #": "26BLD-2", "Business": "Neko Health"})
LICENSE = {"ABC file #": "670730", "Business": "Little Sheep", "Trigger": "Pending license", "Address": "1 Main St",
           "City": "Mountain View", "Type": "Restaurant", "Person to ask for": "Jane Doe",
           "ABC record": "https://abc.example/670730"}


@unittest.skipIf(digest is None, "fastapi not installed")
class DigestTests(unittest.TestCase):
    def test_only_unsent_leads_are_emailed(self):
        found = {"permits": [PERMIT, PERMIT2], "licenses": [LICENSE]}
        sections, totals, keys = digest.fresh(found, {"permit:26BLD-1"})
        self.assertEqual([r["Business"] for r in sections["permits"]], ["Neko Health"])
        self.assertEqual(totals, {"permits": 1, "licenses": 1})
        self.assertEqual(set(keys), {"permit:26BLD-2", "abc:670730"})

    def test_email_lists_leads_and_caps_sections(self):
        many = [dict(PERMIT, **{"Permit #": str(i), "Business": f"Co {i}"}) for i in range(14)]
        sections, totals, _ = digest.fresh({"permits": many}, set())
        subject, text, html = digest.build_email("Palo Alto", sections, "https://fk.example", totals)
        self.assertEqual(subject, "FirstKnock: 14 new leads this week")
        self.assertIn("Co 0", text)
        self.assertNotIn("Co 13", text)
        self.assertIn("4 more on the site", text)
        self.assertIn("https://fk.example/alerts/", html)
        self.assertIn("$1.5M", text)

    def test_failing_source_does_not_sink_email(self):
        def boom(_):
            raise HTTPException(503, "map servers busy")

        found, errors = digest.gather(("Palo Alto",), ["permits", "map"],
                                      {"permits": lambda c: [PERMIT], "map": boom})
        self.assertEqual(found, {"permits": [PERMIT]})
        self.assertEqual(errors, {"map": "map servers busy"})

    def test_run_sends_and_remembers(self):
        sub = {"user_id": "u-1", "email": "rep@example.com", "territory": "Palo Alto", "sources": ["permits"],
               "seen": ["permit:26BLD-1"]}
        with mock.patch.object(digest, "due_subscriptions", return_value=[sub]), \
                mock.patch.object(digest, "send_email") as send, mock.patch.object(digest, "mark_sent") as mark:
            report = digest.run_all(SETTINGS, {"permits": lambda c: [PERMIT, PERMIT2]}, lambda t: ("Palo Alto",),
                                    now=datetime(2026, 11, 30, tzinfo=timezone.utc))
        self.assertEqual(report["sent"], 1)
        self.assertEqual(send.call_args.args[1], "rep@example.com")
        self.assertIn("Neko Health", send.call_args.args[3])
        self.assertNotIn("Rivian", send.call_args.args[3])
        self.assertEqual(mark.call_args.args[2], ["permit:26BLD-1", "permit:26BLD-2"])


@unittest.skipIf(digest is None, "fastapi not installed")
class DigestApiTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides[current_user] = lambda: User("u-1", "rep@example.com")
        main.app.dependency_overrides[get_settings] = lambda: SETTINGS
        main._last_call.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_scheduler_needs_secret(self):
        self.assertEqual(self.client.post("/internal/digest/run").status_code, 403)
        self.assertEqual(self.client.post("/internal/digest/run", headers={"X-Digest-Secret": "nope"}).status_code, 403)
        with mock.patch.object(digest, "run_all", return_value={"sent": 0}) as run:
            r = self.client.post("/internal/digest/run", headers={"X-Digest-Secret": "s3cret"})
        self.assertEqual(r.json(), {"sent": 0})
        run.assert_called_once()

    def test_settings_default_off_then_saved(self):
        with mock.patch.object(digest, "get_subscription", return_value=None):
            body = self.client.get("/v1/digest").json()
        self.assertFalse(body["active"])
        self.assertEqual(body["email"], "rep@example.com")
        with mock.patch.object(digest, "save_subscription", side_effect=lambda s, uid, em, a, t, src:
                               {"active": a, "territory": t, "sources": src}) as save:
            r = self.client.put("/v1/digest", json={"active": True, "territory": "Palo Alto",
                                                     "sources": ["permits", "bogus"]})
        self.assertEqual(r.json()["sources"], ["permits"])
        self.assertEqual(save.call_args.args[1:3], ("u-1", "rep@example.com"))
        bad = self.client.put("/v1/digest", json={"active": True, "territory": "Palo Alto", "sources": []})
        self.assertEqual(bad.status_code, 422)

    def test_test_send_is_throttled(self):
        with mock.patch.object(digest, "send_test", return_value={"sent_to": "rep@example.com"}):
            first = self.client.post("/v1/digest/test", json={"territory": "Palo Alto", "sources": ["permits"]})
            second = self.client.post("/v1/digest/test", json={"territory": "Palo Alto", "sources": ["permits"]})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)


if __name__ == "__main__":
    unittest.main()
