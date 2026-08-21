import unittest

from rep_match_score import (
    detect_product_line_conflict,
    has_meaningful_match_context,
    match_confidence_label,
    score_opportunity_rep_match,
    score_rep_match,
)


class RepMatchScoreTests(unittest.TestCase):
    def test_strong_match_is_deterministic_and_explainable(self):
        rep = {
            "metros": ["Bay Area, CA"],
            "states": ["CA"],
            "categories": ["Security"],
            "industries": ["Commercial Security"],
            "customer_types": ["SMB"],
            "availability_status": "open",
            "rating": 5,
            "years_experience": 10,
            "verified": True,
            "response_time_hours": 2,
        }
        context = {
            "metros": ["Bay Area, CA"],
            "states": ["CA"],
            "categories": ["Security"],
            "industries": ["Commercial Security"],
            "customer_types": ["SMB"],
        }
        first = score_rep_match(rep, context)
        second = score_rep_match(rep, context)
        self.assertEqual(first, second)
        self.assertEqual(first.score, 100)
        self.assertTrue(first.enough_context)
        self.assertIn("Strong territory overlap", first.explanations)
        self.assertIn("Exact category match", first.explanations)
        self.assertIn("Open to new product lines", first.explanations)
        self.assertIn("Verified profile", first.explanations)

    def test_missing_data_reduces_confidence_without_disqualifying(self):
        rep = {"name": "New Rep", "availability_status": "selectively_open"}
        context = {"states": ["CA"], "categories": ["Security"], "customer_types": ["SMB"]}
        result = score_rep_match(rep, context)
        self.assertGreater(result.score, 0)
        self.assertLess(result.score, 20)
        self.assertTrue(result.enough_context)
        self.assertIn("Selectively open to new product lines", result.explanations)
        self.assertIn("No territory coverage listed", result.confidence_notes)
        self.assertIn("No category or industry experience listed", result.confidence_notes)

    def test_legacy_boolean_availability_is_supported(self):
        rep = {"open_to_new_lines": False, "rating": 4, "response_time_hours": 24}
        result = score_rep_match(rep, {"categories": ["POS"]})
        self.assertNotIn("Open to new product lines", result.explanations)
        self.assertIn("Not currently open to new product lines", result.confidence_notes)

    def test_context_detection(self):
        self.assertFalse(has_meaningful_match_context({"keyword": "security"}))
        self.assertTrue(has_meaningful_match_context({"states": ["CA"]}))
        self.assertTrue(has_meaningful_match_context({"zip_code": "95117"}))

    def test_opportunity_rep_match_adds_requirement_explanations(self):
        opportunity = {
            "categories": ["Security"],
            "industries": ["Commercial Security"],
            "customer_types": ["SMB"],
            "metros": ["Bay Area, CA"],
            "states": ["CA"],
            "compensation_types": ["commission"],
            "experience_required": 5,
            "exclusive_territory": True,
        }
        rep = {
            "categories": ["Security"],
            "industries": ["Commercial Security"],
            "customer_types": ["SMB"],
            "metros": ["Bay Area, CA"],
            "states": ["CA"],
            "compensation_types": ["commission"],
            "availability_status": "open",
            "years_experience": 7,
            "verified": True,
            "rating": 4.5,
            "response_time_hours": 2,
        }
        result = score_opportunity_rep_match(opportunity, rep)
        self.assertGreaterEqual(result.score, 90)
        self.assertIn("Meets 5+ years experience requirement", result.explanations)
        self.assertIn("Compensation preference overlap", result.explanations)
        self.assertEqual(result.confidence_label, "Strong Match")
        self.assertIn("Metro overlap", result.territory_overlap)
        self.assertIn("Category overlap", result.category_overlap)
        self.assertIn("Compatible", result.compensation_compatibility)

    def test_opportunity_rep_match_surfaces_conflicts(self):
        opportunity = {
            "categories": ["Security"],
            "customer_types": ["Enterprise"],
            "states": ["CA"],
            "compensation_types": ["commission"],
            "experience_required": 8,
            "exclusive_territory": True,
        }
        rep = {
            "categories": ["Payroll"],
            "customer_types": ["SMB"],
            "states": ["TX"],
            "compensation_types": ["salary"],
            "availability_status": "not_open",
            "years_experience": 3,
        }
        result = score_opportunity_rep_match(opportunity, rep)
        self.assertEqual(result.confidence_label, "Possible Match")
        self.assertIn("No listed territory overlap", result.territory_overlap)
        self.assertIn("No listed category overlap", result.category_overlap)
        self.assertIn("No compensation preference overlap", result.compensation_compatibility)
        self.assertIn("Below 8+ years experience requirement", result.possible_conflicts)
        self.assertIn("Compensation preferences do not overlap", result.possible_conflicts)

    def test_confidence_labels_are_coarse(self):
        self.assertEqual(match_confidence_label(90), "Strong Match")
        self.assertEqual(match_confidence_label(70), "Good Match")
        self.assertEqual(match_confidence_label(40), "Possible Match")
        self.assertEqual(match_confidence_label(90, enough_context=False), "Possible Match")

    def test_direct_competitor_overlap_is_likely_conflict_but_private_by_default(self):
        rep = {"existing_lines": ["ADT Security"], "categories": ["Security"]}
        opportunity = {"direct_competitors": ["ADT"], "categories": ["Security"]}
        conflict = detect_product_line_conflict(rep, opportunity)
        self.assertEqual(conflict.status, "Likely conflict")
        self.assertIn("direct competitor", conflict.explanation)
        self.assertEqual(conflict.public_details, [])

    def test_company_can_mark_competitor_details_public(self):
        rep = {"existing_lines": ["ADT"], "categories": ["Security"]}
        opportunity = {"direct_competitors": ["ADT"], "competitor_info_public": True}
        conflict = detect_product_line_conflict(rep, opportunity)
        self.assertEqual(conflict.status, "Likely conflict")
        self.assertEqual(conflict.public_details, ["ADT"])

    def test_related_category_overlap_is_only_possible_conflict(self):
        rep = {"existing_lines": ["Alarm monitoring package"], "categories": ["Security"]}
        opportunity = {"categories": ["Security"]}
        conflict = detect_product_line_conflict(rep, opportunity)
        self.assertEqual(conflict.status, "Possible conflict")
        self.assertIn("may be normal experience", conflict.explanation)

    def test_no_line_data_is_unknown(self):
        conflict = detect_product_line_conflict({"categories": ["Security"]}, {"categories": ["Security"]})
        self.assertEqual(conflict.status, "Unknown")

    def test_likely_conflict_flows_into_opportunity_match(self):
        rep = {
            "existing_lines": ["ADT"],
            "categories": ["Security"],
            "states": ["CA"],
            "availability_status": "open",
        }
        opportunity = {
            "direct_competitors": ["ADT"],
            "categories": ["Security"],
            "states": ["CA"],
        }
        result = score_opportunity_rep_match(opportunity, rep)
        self.assertEqual(result.product_line_conflict, "Likely conflict")
        self.assertTrue(any("Likely conflict" in note for note in result.possible_conflicts))


if __name__ == "__main__":
    unittest.main()
