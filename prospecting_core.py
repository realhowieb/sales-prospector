from __future__ import annotations

import hashlib
import html
from math import asin, cos, radians, sin, sqrt


PRODUCT_PROFILES: dict[str, dict] = {
    "Marketing/Web": {
        "weights": {"phone": 35, "no_website": 35, "address": 15, "independent": 15},
        "fit_categories": "all",
        "angle": "presence gap and direct-response conversation",
    },
    "Security/ADT": {
        "weights": {"phone": 30, "no_website": 5, "address": 30, "independent": 25},
        "fit_categories": {"Restaurant & Café", "Retail Boutique", "Beauty & Spa", "Auto Services", "Fitness & Gym"},
        "fit_bonus": 10,
        "angle": "in-person security/CCTV conversation",
    },
    "POS": {
        "weights": {"phone": 25, "no_website": 10, "address": 25, "independent": 25},
        "fit_categories": {"Restaurant & Café", "Retail Boutique", "Beauty & Spa", "Auto Services"},
        "fit_bonus": 15,
        "angle": "checkout, payments, and hardware conversation",
    },
    "Payroll/HR": {
        "weights": {"phone": 30, "no_website": 5, "address": 10, "independent": 20},
        "fit_categories": {"Medical & Dental", "Professional Svcs", "Fitness & Gym", "Restaurant & Café"},
        "fit_bonus": 20,
        "angle": "small-team payroll and HR compliance conversation",
    },
    "Insurance": {
        "weights": {"phone": 25, "no_website": 5, "address": 20, "independent": 20},
        "fit_categories": {"Auto Services", "Home Services", "Professional Svcs", "Medical & Dental"},
        "fit_bonus": 15,
        "angle": "commercial coverage and risk review",
    },
    "Merchant Services": {
        "weights": {"phone": 30, "no_website": 10, "address": 20, "independent": 25},
        "fit_categories": {"Restaurant & Café", "Retail Boutique", "Beauty & Spa", "Fitness & Gym"},
        "fit_bonus": 15,
        "angle": "payment processing and fee-savings conversation",
    },
}


def escape_html(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def normalize_owner_email(email: str) -> str:
    return (email or "").strip().lower()


def category_matches_profile(category: str, profile: dict) -> bool:
    fit_categories = profile.get("fit_categories", set())
    return fit_categories == "all" or category in fit_categories


def lead_score(row: dict, profile_name: str = "Marketing/Web") -> tuple[int, list[str]]:
    profile = PRODUCT_PROFILES.get(profile_name, PRODUCT_PROFILES["Marketing/Web"])
    weights = profile["weights"]
    score, why = 0, []
    if row["phone"]:
        points = weights["phone"]
        score += points
        why.append(f"Has phone (reachable) +{points}")
    else:
        why.append("No phone listed")
    if not row["website"]:
        points = weights["no_website"]
        score += points
        why.append(f"No website +{points}")
    else:
        why.append("Already has a website")
    if row["address"]:
        points = weights["address"]
        score += points
        why.append(f"Street address (visitable) +{points}")
    if row["independent"]:
        points = weights["independent"]
        score += points
        why.append(f"Independent, not a chain +{points}")
    else:
        why.append("Looks like a chain/brand")
    if category_matches_profile(row["category"], profile):
        points = profile.get("fit_bonus", 0)
        if points:
            score += points
            why.append(f"Good category fit for {profile_name} +{points}")
    return min(score, 100), why


def heat_of(score: int) -> str:
    return "Hot" if score >= 66 else "Warm" if score >= 40 else "Cool"


def sales_insight(row: dict, profile_name: str = "Marketing/Web") -> str:
    profile = PRODUCT_PROFILES.get(profile_name, PRODUCT_PROFILES["Marketing/Web"])
    ownership = "local independent" if row["independent"] else "known brand or chain"
    access = "direct phone access" if row["phone"] else "no listed phone"
    place = "a physical storefront" if row["address"] else "limited address detail"
    website = "no visible website" if not row["website"] else "an existing website"
    return (
        f"Recommended approach: {ownership} {row['category'].lower()} with {access}, "
        f"{place}, and {website}. Good candidate for a {profile['angle']}."
    )


def bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def miles_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 3958.8 * 2 * asin(sqrt(a))


def build_pipeline_payload(owner_email: str, owner_key_hash: str, entries: dict) -> list[dict]:
    payload = []
    for prospect_id, entry in entries.items():
        payload.append({
            "owner_email": owner_email,
            "owner_key_hash": owner_key_hash,
            "prospect_id": str(prospect_id),
            "name": entry.get("name", ""),
            "category": entry.get("category", ""),
            "stage": entry.get("stage", "New lead"),
            "note": entry.get("note", ""),
            "next_follow_up": entry.get("next_follow_up") or None,
            "outcome": entry.get("outcome", ""),
        })
    return payload
