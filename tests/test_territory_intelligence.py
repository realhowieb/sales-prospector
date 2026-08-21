import unittest

from territory_intelligence import build_metro_activity_rows, calculate_territory_intelligence


class TerritoryIntelligenceTests(unittest.TestCase):
    def test_calculates_marketplace_indicators_from_rows(self):
        reps = [
            {
                "active": True,
                "profile_status": "active",
                "verified": True,
                "availability_status": "open",
                "metros": ["Bay Area, CA"],
                "states": ["CA"],
                "categories": ["Security"],
                "industries": ["Commercial Security"],
                "rating": 4.5,
                "commission_min": 10,
                "commission_max": 20,
            },
            {
                "active": True,
                "profile_status": "active",
                "verified": False,
                "open_to_new_lines": True,
                "metros": ["Bay Area, CA"],
                "categories": ["Security"],
                "industries": ["Commercial Security"],
                "rating": 5,
            },
            {
                "active": True,
                "profile_status": "active",
                "metros": ["Austin, TX"],
                "categories": ["POS"],
                "rating": 5,
            },
        ]
        opportunities = [
            {
                "active": True,
                "metros": ["Bay Area, CA"],
                "states": ["CA"],
                "categories": ["Security"],
                "industries": ["Commercial Security"],
                "commission_min": 15,
                "commission_max": 25,
            },
            {"active": True, "metros": ["Austin, TX"], "categories": ["POS"]},
        ]
        companies = [
            {
                "profile_status": "active",
                "metros_needed": ["Bay Area, CA"],
                "states_needed": ["CA"],
                "categories": ["Security"],
                "industries": ["Commercial Security"],
            }
        ]

        result = calculate_territory_intelligence(
            reps,
            opportunities,
            companies,
            metro="Bay Area, CA",
            state="CA",
            category="Security",
            industry="Commercial Security",
        )

        self.assertFalse(result.not_enough_data)
        self.assertEqual(result.total_active_reps, 2)
        self.assertEqual(result.verified_reps, 1)
        self.assertEqual(result.open_reps, 2)
        self.assertEqual(result.active_opportunities, 1)
        self.assertEqual(result.companies_seeking_reps, 1)
        self.assertAlmostEqual(result.average_rep_rating, 4.75)
        self.assertAlmostEqual(result.average_commission_min, 12.5)
        self.assertAlmostEqual(result.average_commission_max, 22.5)
        self.assertAlmostEqual(result.supply_to_demand_ratio, 1.0)
        self.assertIsNotNone(result.opportunity_score)

    def test_insufficient_data_hides_opportunity_score(self):
        result = calculate_territory_intelligence(
            reps=[{"active": True, "profile_status": "active", "states": ["CA"], "categories": ["Security"]}],
            opportunities=[],
            companies=[],
            state="CA",
            category="Security",
        )

        self.assertTrue(result.not_enough_data)
        self.assertIsNone(result.opportunity_score)
        self.assertIsNone(result.supply_to_demand_ratio)

    def test_state_can_match_state_from_metro(self):
        result = calculate_territory_intelligence(
            reps=[{"active": True, "profile_status": "active", "metros": ["San Jose, CA"], "categories": ["Security"]}],
            opportunities=[{"active": True, "metros": ["San Jose, CA"], "categories": ["Security"]}],
            companies=[{"profile_status": "active", "metros_needed": ["San Jose, CA"], "categories": ["Security"]}],
            state="CA",
            category="Security",
        )

        self.assertEqual(result.total_active_reps, 1)
        self.assertEqual(result.active_opportunities, 1)
        self.assertEqual(result.companies_seeking_reps, 1)

    def test_build_metro_activity_rows_uses_approximate_centers(self):
        rows = build_metro_activity_rows(
            reps=[{"active": True, "profile_status": "active", "metros": ["San Jose, CA"], "categories": ["Security"]}],
            opportunities=[{"active": True, "metros": ["San Jose, CA"], "categories": ["Security"]}],
            companies=[{"profile_status": "active", "metros_needed": ["San Jose, CA"], "categories": ["Security"]}],
            metro_bboxes={"San Jose, CA": (37.1, -122.0, 37.5, -121.6)},
            category="Security",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metro"], "San Jose, CA")
        self.assertAlmostEqual(rows[0]["lat"], 37.3)
        self.assertAlmostEqual(rows[0]["lon"], -121.8)
        self.assertEqual(rows[0]["rep_supply"], 1)
        self.assertEqual(rows[0]["opportunities"], 1)
        self.assertEqual(rows[0]["company_demand"], 1)
        self.assertEqual(rows[0]["activity"], 3)


if __name__ == "__main__":
    unittest.main()
