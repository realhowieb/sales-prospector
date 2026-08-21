"""Subscription plans and entitlement checks that gate features (contact rep, full profile, advanced search, territory intelligence, featured listings)."""

from __future__ import annotations

from dataclasses import dataclass


PLAN_REP_FREE = "rep_free"
PLAN_REP_PRO = "rep_pro"
PLAN_COMPANY_FREE = "company_free"
PLAN_COMPANY_PRO = "company_pro"
PLAN_ADMIN = "admin"

KNOWN_PLANS = {
    PLAN_REP_FREE,
    PLAN_REP_PRO,
    PLAN_COMPANY_FREE,
    PLAN_COMPANY_PRO,
    PLAN_ADMIN,
}

PLAN_LABELS = {
    PLAN_REP_FREE: "Rep Free",
    PLAN_REP_PRO: "Rep Pro",
    PLAN_COMPANY_FREE: "Company Free",
    PLAN_COMPANY_PRO: "Company Pro",
    PLAN_ADMIN: "Admin",
}


@dataclass(frozen=True)
class EntitlementContext:
    role: str = "rep"
    plan: str = PLAN_REP_FREE
    is_admin: bool = False
    unrestricted: bool = True


BASE_ENTITLEMENTS = {
    PLAN_REP_FREE: {
        "profile",
        "opportunity_browsing",
        "limited_matching",
        "view_limited_profile",
    },
    PLAN_REP_PRO: {
        "profile",
        "enhanced_profile",
        "opportunity_browsing",
        "limited_matching",
        "more_recommendations",
        "featured_placement",
        "territory_intelligence",
        "view_limited_profile",
    },
    PLAN_COMPANY_FREE: {
        "search_marketplace",
        "view_limited_profile",
        "save_reps",
    },
    PLAN_COMPANY_PRO: {
        "search_marketplace",
        "advanced_search",
        "view_full_profile",
        "connection_requests",
        "full_matching",
        "territory_intelligence",
        "shortlists",
        "save_reps",
    },
    PLAN_ADMIN: {
        "profile",
        "enhanced_profile",
        "opportunity_browsing",
        "limited_matching",
        "more_recommendations",
        "featured_placement",
        "territory_intelligence",
        "search_marketplace",
        "advanced_search",
        "view_full_profile",
        "connection_requests",
        "full_matching",
        "shortlists",
        "save_reps",
        "admin",
    },
}


def normalize_plan(plan: str | None, role: str = "rep", is_admin: bool = False) -> str:
    if is_admin:
        return PLAN_ADMIN
    value = (plan or "").strip().lower()
    if value in KNOWN_PLANS:
        return value
    return PLAN_COMPANY_FREE if (role or "").lower() == "company" else PLAN_REP_FREE


def plan_label(plan: str | None) -> str:
    return PLAN_LABELS.get(normalize_plan(plan), "Free")


def entitlement_context(
    role: str | None = "rep",
    plan: str | None = None,
    is_admin: bool = False,
    unrestricted: bool = True,
) -> EntitlementContext:
    normalized_role = (role or "rep").strip().lower()
    normalized_plan = normalize_plan(plan, normalized_role, is_admin)
    return EntitlementContext(
        role=normalized_role,
        plan=normalized_plan,
        is_admin=is_admin,
        unrestricted=bool(unrestricted),
    )


def has_entitlement(ctx: EntitlementContext, feature: str) -> bool:
    if ctx.unrestricted or ctx.is_admin:
        return True
    return feature in BASE_ENTITLEMENTS.get(ctx.plan, set())


def can_contact_rep(ctx: EntitlementContext) -> bool:
    return has_entitlement(ctx, "connection_requests")


def can_view_full_profile(ctx: EntitlementContext) -> bool:
    return has_entitlement(ctx, "view_full_profile")


def can_use_advanced_search(ctx: EntitlementContext) -> bool:
    return has_entitlement(ctx, "advanced_search")


def can_view_territory_intelligence(ctx: EntitlementContext) -> bool:
    return has_entitlement(ctx, "territory_intelligence")


def can_be_featured(ctx: EntitlementContext) -> bool:
    return has_entitlement(ctx, "featured_placement")


def can_use_full_matching(ctx: EntitlementContext) -> bool:
    return has_entitlement(ctx, "full_matching") or has_entitlement(ctx, "more_recommendations")
