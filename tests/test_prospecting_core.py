import unittest

from prospecting_core import (
    build_pipeline_payload,
    escape_html,
    hash_code,
    lead_score,
    miles_between,
    normalize_owner_email,
    sales_insight,
)


class ProspectingCoreTests(unittest.TestCase):
    def setUp(self):
        self.restaurant = {
            "phone": "555-0100",
            "website": "",
            "address": "123 Main St",
            "independent": True,
            "category": "Restaurant & Café",
        }

    def test_marketing_profile_heavily_rewards_no_website(self):
        score, why = lead_score(self.restaurant, "Marketing/Web")
        self.assertEqual(score, 100)
        self.assertIn("No website +35", why)

    def test_security_profile_does_not_overweight_website_gap(self):
        score, why = lead_score(self.restaurant, "Security/ADT")
        self.assertEqual(score, 100)
        self.assertIn("No website +5", why)
        self.assertIn("Good category fit for Security/ADT +10", why)

    def test_sales_insight_is_product_specific(self):
        insight = sales_insight(self.restaurant, "Security/ADT")
        self.assertIn("Recommended approach:", insight)
        self.assertIn("security/CCTV", insight)

    def test_html_escape(self):
        self.assertEqual(escape_html('<img src=x onerror="bad()">'), "&lt;img src=x onerror=&quot;bad()&quot;&gt;")

    def test_hash_and_email_normalization(self):
        self.assertEqual(hash_code(" abc "), hash_code("abc"))
        self.assertEqual(normalize_owner_email(" Rep@Example.COM "), "rep@example.com")

    def test_miles_between_nearby_coordinates(self):
        miles = miles_between(37.7749, -122.4194, 37.8044, -122.2712)
        self.assertGreater(miles, 7)
        self.assertLess(miles, 10)

    def test_pipeline_payload_includes_owner_key_hash(self):
        payload = build_pipeline_payload(
            "rep@example.com",
            "hash123",
            {"osm/1": {"name": "A", "category": "Restaurant & Café", "stage": "Qualified"}},
        )
        self.assertEqual(payload[0]["owner_key_hash"], "hash123")
        self.assertEqual(payload[0]["prospect_id"], "osm/1")
        self.assertIsNone(payload[0]["next_follow_up"])


if __name__ == "__main__":
    unittest.main()

