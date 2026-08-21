import unittest

from monetization import (
    PLAN_COMPANY_FREE,
    PLAN_COMPANY_PRO,
    PLAN_REP_FREE,
    PLAN_REP_PRO,
    can_be_featured,
    can_contact_rep,
    can_use_advanced_search,
    can_view_full_profile,
    can_view_territory_intelligence,
    entitlement_context,
    normalize_plan,
)


class MonetizationTest(unittest.TestCase):
    def test_default_plan_follows_role(self):
        self.assertEqual(normalize_plan(None, "rep"), PLAN_REP_FREE)
        self.assertEqual(normalize_plan(None, "company"), PLAN_COMPANY_FREE)

    def test_company_free_is_limited_when_enforced(self):
        ctx = entitlement_context("company", PLAN_COMPANY_FREE, unrestricted=False)
        self.assertFalse(can_contact_rep(ctx))
        self.assertFalse(can_use_advanced_search(ctx))
        self.assertFalse(can_view_full_profile(ctx))

    def test_company_pro_unlocks_company_features(self):
        ctx = entitlement_context("company", PLAN_COMPANY_PRO, unrestricted=False)
        self.assertTrue(can_contact_rep(ctx))
        self.assertTrue(can_use_advanced_search(ctx))
        self.assertTrue(can_view_full_profile(ctx))
        self.assertTrue(can_view_territory_intelligence(ctx))

    def test_rep_pro_can_be_featured_and_use_ti(self):
        ctx = entitlement_context("rep", PLAN_REP_PRO, unrestricted=False)
        self.assertTrue(can_be_featured(ctx))
        self.assertTrue(can_view_territory_intelligence(ctx))

    def test_development_mode_is_unrestricted(self):
        ctx = entitlement_context("company", PLAN_COMPANY_FREE, unrestricted=True)
        self.assertTrue(can_contact_rep(ctx))
        self.assertTrue(can_view_full_profile(ctx))
        self.assertTrue(can_use_advanced_search(ctx))


if __name__ == "__main__":
    unittest.main()
