import hashlib
import hmac
import json
import unittest
from unittest import mock

try:
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    import api.main as main
    from api import billing, cache
    from api.auth import User, current_user, get_settings
    from api.settings import Settings

    SETTINGS = Settings("https://sb.example", "anon", "service", ("http://localhost:3000",),
                        stripe_secret_key="sk_test_x", stripe_webhook_secret="whsec_test",
                        stripe_price_monthly="price_m", stripe_price_yearly="price_y", web_url="https://fk.example")
except ImportError:  # API deps are optional for the Streamlit app's CI
    billing = None

from tests.test_trigger_leads import CSV


def signed(event: dict, secret="whsec_test", ts=1_800_000_000):
    body = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, f"t={ts},v1={sig}"


@unittest.skipIf(billing is None, "fastapi not installed")
class PlanTests(unittest.TestCase):
    def test_is_pro(self):
        self.assertTrue(billing.is_pro({"plan": "pro", "plan_status": "active"}))
        self.assertTrue(billing.is_pro({"plan": "pro"}))
        self.assertTrue(billing.is_pro({"plan": "pro", "plan_status": "past_due"}))
        self.assertFalse(billing.is_pro({"plan": "pro", "plan_status": "canceled"}))
        self.assertFalse(billing.is_pro({}))
        self.assertFalse(billing.is_pro(None))

    def test_signature(self):
        body, header = signed({"type": "x"})
        self.assertEqual(billing.verify_signature(body, header, "whsec_test", now=1_800_000_010)["type"], "x")
        with self.assertRaises(HTTPException):
            billing.verify_signature(body, header, "wrong", now=1_800_000_010)
        with self.assertRaises(HTTPException):
            billing.verify_signature(body, header, "whsec_test", now=1_800_009_999)  # too old

    def test_events_set_plan(self):
        with mock.patch.object(billing, "set_plan") as sp:
            billing.handle_event(SETTINGS, {"type": "checkout.session.completed", "data": {"object": {
                "mode": "subscription", "client_reference_id": "u-1", "customer": "cus_1", "subscription": "sub_1"}}})
            self.assertEqual(sp.call_args.args[1:], ("u-1", {"plan": "pro", "plan_status": "active",
                                                             "stripe_customer_id": "cus_1",
                                                             "stripe_subscription_id": "sub_1"}))
            billing.handle_event(SETTINGS, {"type": "customer.subscription.deleted", "data": {"object": {
                "id": "sub_1", "customer": "cus_1", "status": "canceled", "metadata": {"user_id": "u-1"}}}})
            self.assertEqual(sp.call_args.args[2]["plan"], "free")
            self.assertEqual(billing.handle_event(SETTINGS, {"type": "invoice.paid", "data": {"object": {}}}),
                             "ignored: invoice.paid")


@unittest.skipIf(billing is None, "fastapi not installed")
class FreePlanApiTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides[current_user] = lambda: User("u-2", "free@example.com")
        main.app.dependency_overrides[get_settings] = lambda: SETTINGS
        cache.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_free_sees_top_leads_only(self):
        many = [{"Permit #": str(i), "Business": f"Co {i}", "Signal": "Build-out (TI)", "Score": 90 - i,
                 "Opened": "2026-11-01", "Description": "COM: TI", "Chain": False} for i in range(15)]
        with mock.patch.object(main, "_permit_rows", return_value=many):
            body = self.client.get("/v1/leads/permits", params={"cities": "Palo Alto"}).json()
        self.assertEqual(body["count"], 15)
        self.assertEqual(len(body["leads"]), billing.FREE_LEADS)
        self.assertTrue(body["limited"])
        self.assertEqual(self.client.get("/v1/me").json()["plan"], "free")

    def test_pro_only_features(self):
        r = self.client.post("/v1/leads/licenses/contacts", params={"cities": "Palo Alto"})
        self.assertEqual(r.status_code, 402)
        r = self.client.put("/v1/digest", json={"active": True, "territory": "Palo Alto", "sources": ["permits"]})
        self.assertEqual(r.status_code, 402)

    def test_pipeline_cap(self):
        with mock.patch("api.pipeline.has_entry", return_value=False), \
                mock.patch("api.pipeline.count_entries", return_value=billing.FREE_PIPELINE):
            r = self.client.put("/v1/pipeline/abc-1", json={"name": "X"})
        self.assertEqual(r.status_code, 402)

    def test_checkout_and_webhook(self):
        with mock.patch.object(billing, "_stripe", return_value={"url": "https://checkout.stripe.com/x"}) as st:
            r = self.client.post("/v1/billing/checkout", json={"interval": "year"})
        self.assertEqual(r.json()["url"], "https://checkout.stripe.com/x")
        data = st.call_args.args[2]
        self.assertEqual((data["line_items[0][price]"], data["client_reference_id"]), ("price_y", "u-2"))
        self.assertEqual(data["subscription_data[metadata][user_id]"], "u-2")

        body, header = signed({"type": "invoice.paid", "data": {"object": {}}})
        with mock.patch("api.billing.time.time", return_value=1_800_000_005):
            r = self.client.post("/webhooks/stripe", content=body, headers={"Stripe-Signature": header})
        self.assertEqual(r.json()["result"], "ignored: invoice.paid")
        bad = self.client.post("/webhooks/stripe", content=body, headers={"Stripe-Signature": "t=1,v1=nope"})
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
