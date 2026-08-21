import unittest

from prospecting_core import (
    availability_status,
    build_pipeline_payload,
    clean_list,
    escape_html,
    format_availability,
    format_compensation,
    format_industries,
    format_territories,
    hash_code,
    lead_score,
    miles_between,
    normalize_owner_email,
    rep_area_match,
    safe_public_url,
    sales_insight,
    slugify,
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
            {"osm/1": {"name": "A", "category": "Restaurant & Café", "stage": "Qualified", "call_attempts": 2}},
        )
        self.assertEqual(payload[0]["owner_key_hash"], "hash123")
        self.assertEqual(payload[0]["prospect_id"], "osm/1")
        self.assertEqual(payload[0]["call_attempts"], 2)
        self.assertIsNone(payload[0]["next_follow_up"])
        self.assertIsNone(payload[0]["owner_user_id"])

    def test_rep_area_match_returns_distance(self):
        rep = {"service_lat": 37.7749, "service_lon": -122.4194, "service_radius_miles": 10}
        matched, distance = rep_area_match(rep, (37.8044, -122.2712), 1)
        self.assertTrue(matched)
        self.assertGreater(distance, 7)
        self.assertLess(distance, 10)

    def test_rep_area_match_falls_back_to_metro(self):
        rep = {"metros": ["Austin, TX"]}
        self.assertEqual(rep_area_match(rep, None, 25, "Austin, TX"), (True, None))
        self.assertEqual(rep_area_match(rep, None, 25, "Denver, CO"), (False, None))

    def test_rep_area_match_keeps_legacy_metro_rep_with_city_search(self):
        rep = {"metros": ["Bay Area, CA"]}
        self.assertEqual(rep_area_match(rep, (37.4, -122.1), 25, "Bay Area, CA"), (True, None))

    def test_profile_slug_and_list_cleanup(self):
        self.assertEqual(slugify("Acme Sales, Inc."), "acme-sales-inc")
        self.assertEqual(slugify("!!!"), "rep")
        self.assertEqual(clean_list(" security, CCTV,, payroll "), ["security", "CCTV", "payroll"])
        self.assertEqual(clean_list(["CA", "", " NV "]), ["CA", "NV"])

    def test_profile_formatters_handle_new_and_legacy_rows(self):
        rep = {
            "commission_min": 8,
            "commission_max": 12.5,
            "compensation_types": ["retainer", "bonus"],
            "industries": ["Security", "Restaurants"],
            "customer_types": ["SMB"],
            "states": ["CA"],
            "zip_codes": ["95117"],
            "metros": ["Bay Area, CA"],
            "territory_radius": 35,
            "open_to_new_lines": True,
            "profile_status": "active",
        }
        self.assertEqual(format_compensation(rep), "8-12.5% commission · retainer, bonus")
        self.assertIn("States: CA", format_territories(rep))
        self.assertIn("Industries: Security, Restaurants", format_industries(rep))
        self.assertEqual(availability_status(rep), "open")
        self.assertEqual(format_availability(rep), "Open to new lines")

        legacy = {"categories": ["POS"], "metros": ["Austin, TX"], "service_radius_miles": 25}
        self.assertEqual(format_compensation(legacy), "Compensation varies by line")
        self.assertIn("Industries: POS", format_industries(legacy))
        self.assertIn("Metros: Austin, TX", format_territories(legacy))

    def test_profile_availability_and_public_urls_are_safe(self):
        self.assertEqual(format_availability({"profile_status": "hidden"}), "Hidden")
        self.assertEqual(format_availability({"open_to_new_lines": False}), "Not open to new lines")
        self.assertEqual(format_availability({"availability_status": "selectively_open"}), "Selectively open")
        self.assertEqual(availability_status({"availability_status": "not_open"}), "not open")
        self.assertEqual(safe_public_url("https://example.com"), "https://example.com")
        self.assertEqual(safe_public_url("javascript:alert(1)"), "")


if __name__ == "__main__":
    unittest.main()
