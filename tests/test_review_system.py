import unittest

from review_system import (
    build_review_payload,
    has_duplicate_review,
    normalize_review_status,
    reviews_summary,
)


class ReviewSystemTests(unittest.TestCase):
    def test_aggregates_approved_reviews_only(self):
        summary = reviews_summary([
            {"rep_id": "42", "rating": 5, "status": "approved", "review": "Great"},
            {"rep_id": "42", "rating": 1, "status": "pending", "review": "Not counted"},
            {"rep_id": "42", "rating": 4, "status": "approved", "title": "Solid"},
            {"rep_id": "99", "rating": 5, "status": "rejected"},
        ])

        self.assertEqual(summary["42"]["count"], 2)
        self.assertEqual(summary["42"]["avg"], 4.5)
        self.assertEqual(len(summary["42"]["recent"]), 2)
        self.assertNotIn("99", summary)

    def test_status_defaults_to_pending(self):
        self.assertEqual(normalize_review_status("approved"), "approved")
        self.assertEqual(normalize_review_status("rejected"), "rejected")
        self.assertEqual(normalize_review_status("unknown"), "pending")
        self.assertEqual(normalize_review_status(None), "pending")

    def test_payload_normalizes_rating_and_rep_id(self):
        payload = build_review_payload(
            rep_id="db-42",
            rating="5",
            reviewer=" Buyer ",
            title=" Helpful ",
            review=" Good intro ",
            verified_relationship=True,
            lead_id="7",
        )

        self.assertEqual(payload.rep_id, "42")
        self.assertEqual(payload.rating, 5)
        self.assertEqual(payload.reviewer, "Buyer")
        self.assertEqual(payload.title, "Helpful")
        self.assertEqual(payload.review, "Good intro")
        self.assertEqual(payload.lead_id, 7)

    def test_rejects_invalid_rating(self):
        with self.assertRaises(ValueError):
            build_review_payload(rep_id="42", rating=6, reviewer="Buyer")

    def test_duplicate_detection_uses_lead_or_reviewer(self):
        reviews = [
            {"rep_id": "42", "lead_id": 10, "reviewer": "Buyer", "status": "pending"},
            {"rep_id": "42", "lead_id": 11, "reviewer": "Rejected Buyer", "status": "rejected"},
        ]

        self.assertTrue(has_duplicate_review(reviews, rep_id="db-42", lead_id=10))
        self.assertTrue(has_duplicate_review(reviews, rep_id="42", reviewer="buyer"))
        self.assertFalse(has_duplicate_review(reviews, rep_id="42", reviewer="Rejected Buyer"))


if __name__ == "__main__":
    unittest.main()
