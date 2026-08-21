"""
Territory Prospector — find new business customers by area & category.

Live data source: OpenStreetMap via the public Overpass API (no API key, no billing).
Lead scores are computed from real listing signals and the rep's selected product profile.
Pipeline stages and notes can sync to Supabase when configured; CSV import/export remains available.
"""
from __future__ import annotations

import io
import re
import secrets
import smtplib
import time
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlencode

import pandas as pd
import requests
import streamlit as st

from auth_system import (
    auth_session_from_response,
    can_create_company,
    can_create_rep,
    is_admin_role,
    normalize_account_role,
    public_signup_role,
)
from monetization import (
    can_be_featured,
    can_contact_rep,
    can_use_advanced_search,
    can_use_full_matching,
    can_view_full_profile,
    can_view_territory_intelligence,
    entitlement_context,
    plan_label,
)
from connection_requests import (
    build_connection_payload,
    contact_visible,
    duplicate_open_connection,
    normalize_connection_status,
)
from prospecting_core import (
    PRODUCT_PROFILES,
    bbox_center,
    build_pipeline_payload,
    escape_html as h,
    hash_code as _hash_code,
    heat_of,
    lead_score,
    miles_between,
    normalize_owner_email,
    sales_insight,
)
from profile_claims import build_profile_claim_payload, is_claimable_rep, normalize_claim_status
from rep_match_score import RepMatchResult, score_opportunity_rep_match, score_rep_match
from review_system import (
    build_review_payload,
    has_duplicate_review,
    normalize_rep_review_id,
    normalize_review_status,
    reviews_summary as aggregate_reviews_summary,
)
from shortlists import (
    COLLECTIONS,
    build_shortlist_item,
    is_saved as shortlist_is_saved,
    remove_session_shortlist,
    upsert_session_shortlist,
)
from territory_intelligence import (
    TerritoryIntelligenceResult,
    build_metro_activity_rows,
    calculate_territory_intelligence,
    matching_opportunities,
    matching_reps,
)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return slug.strip("-") or "rep"


def clean_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = value
    return [str(item).strip() for item in items if str(item).strip()]


def format_compensation(rep: dict) -> str:
    comp_types = clean_list(rep.get("compensation_types"))
    low = rep.get("commission_min")
    high = rep.get("commission_max")
    bits = []
    if low not in (None, "") and high not in (None, ""):
        bits.append(f"{float(low):g}-{float(high):g}% commission")
    elif low not in (None, ""):
        bits.append(f"from {float(low):g}% commission")
    elif high not in (None, ""):
        bits.append(f"up to {float(high):g}% commission")
    if comp_types:
        bits.append(", ".join(comp_types))
    return " · ".join(bits) if bits else "Compensation varies by line"


def availability_status(rep: dict) -> str:
    status = (rep.get("availability_status") or "").strip().lower().replace("_", " ")
    if status in {"open", "selectively open", "not open"}:
        return status
    return "open" if rep.get("open_to_new_lines", True) else "not open"


def format_territories(rep: dict) -> str:
    states = clean_list(rep.get("states"))
    zips = clean_list(rep.get("zip_codes"))
    metros = clean_list(rep.get("metros"))
    radius = rep.get("territory_radius") or rep.get("service_radius_miles")
    parts = []
    if states:
        parts.append("States: " + ", ".join(states[:6]))
    if zips:
        parts.append("ZIPs: " + ", ".join(zips[:6]))
    if metros:
        parts.append("Metros: " + ", ".join(metros[:4]))
    if radius:
        parts.append(f"{int(radius)} mi radius")
    return " · ".join(parts) if parts else "Territory available on request"


def format_industries(rep: dict) -> str:
    industries = clean_list(rep.get("industries")) or clean_list(rep.get("categories"))
    customer_types = clean_list(rep.get("customer_types"))
    parts = []
    if industries:
        parts.append("Industries: " + ", ".join(industries[:5]))
    if customer_types:
        parts.append("Customers: " + ", ".join(customer_types[:4]))
    return " · ".join(parts) if parts else "Industry fit varies by opportunity"


def format_availability(rep: dict) -> str:
    status = rep.get("profile_status") or "active"
    if status != "active":
        return status.title()
    availability = availability_status(rep)
    if availability == "open":
        return "Open to new lines"
    if availability == "selectively open":
        return "Selectively open"
    return "Not open to new lines"


def safe_public_url(value: str) -> str:
    url = (value or "").strip()
    if url.startswith(("https://", "http://")):
        return url
    return ""


def rep_area_match(rep: dict, metro: str, center: tuple[float, float] | None = None) -> bool:
    if not metro:
        return True
    metro_l = metro.lower()
    fields = [
        " ".join(clean_list(rep.get("metros"))),
        str(rep.get("service_area") or ""),
        " ".join(clean_list(rep.get("states"))),
        " ".join(clean_list(rep.get("zip_codes"))),
    ]
    if any(metro_l in field.lower() for field in fields):
        return True
    if center and rep.get("service_lat") is not None and rep.get("service_lon") is not None:
        radius = float(rep.get("service_radius_miles") or rep.get("territory_radius") or 25)
        return miles_between(float(rep["service_lat"]), float(rep["service_lon"]), center[0], center[1]) <= radius
    return False


# pydeck ships with streamlit, but guard the import so a stale-module deploy
# degrades to the list view instead of crashing the whole app.
try:
    import pydeck as pdk
    HAVE_PYDECK = True
except Exception:  # pragma: no cover
    HAVE_PYDECK = False

# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #
APP_TITLE = "Territory Prospector"

# Preset metro bounding boxes: (south, west, north, east)
METROS: dict[str, tuple[float, float, float, float]] = {
    "Austin, TX":    (30.10, -97.94, 30.52, -97.56),
    "Denver, CO":    (39.61, -105.11, 39.91, -104.60),
    "Nashville, TN": (36.03, -86.92, 36.28, -86.63),
    "Portland, OR":  (45.43, -122.84, 45.65, -122.47),
    "Tampa, FL":     (27.87, -82.53, 28.17, -82.34),
    "Columbus, OH":  (39.86, -83.20, 40.14, -82.77),
    "Atlanta, GA":   (33.65, -84.55, 33.89, -84.29),
    "Seattle, WA":   (47.50, -122.44, 47.73, -122.24),
    "Bay Area, CA":  (37.20, -122.55, 37.95, -121.75),  # SF, Oakland, San Jose & Peninsula
    "Phoenix, AZ":   (33.30, -112.20, 33.70, -111.90),
    "Chicago, IL":   (41.80, -87.75, 41.99, -87.55),
}

# Category -> (color RGB, list of Overpass tag filters)
CATEGORIES: dict[str, dict] = {
    "Restaurant & Café": {
        "color": [198, 67, 42],
        "filters": ['["amenity"~"^(restaurant|cafe|fast_food|bar|pub|ice_cream)$"]'],
    },
    "Fitness & Gym": {
        "color": [182, 122, 30],
        "filters": ['["leisure"~"^(fitness_centre|sports_centre)$"]', '["sport"="fitness"]'],
    },
    "Auto Services": {
        "color": [62, 124, 100],
        "filters": ['["shop"~"^(car_repair|tyres|car_parts)$"]', '["amenity"="car_wash"]'],
    },
    "Home Services": {
        "color": [46, 108, 140],
        "filters": ['["craft"~"^(plumber|electrician|carpenter|hvac|painter|roofer)$"]'],
    },
    "Medical & Dental": {
        "color": [122, 86, 176],
        "filters": ['["amenity"~"^(dentist|doctors|clinic|pharmacy)$"]'],
    },
    "Retail Boutique": {
        "color": [192, 81, 138],
        "filters": ['["shop"~"^(clothes|boutique|gift|books|shoes|jewelry|florist|furniture)$"]'],
    },
    "Professional Svcs": {
        "color": [14, 90, 84],
        "filters": ['["office"~"^(accountant|lawyer|insurance|estate_agent|financial|it|company|consulting)$"]'],
    },
    "Beauty & Spa": {
        "color": [176, 89, 46],
        "filters": ['["shop"~"^(hairdresser|beauty)$"]', '["leisure"="spa"]'],
    },
}

STAGES = ["— none —", "New lead", "Contacted", "Qualified", "Won", "Passed"]
STAGE_COLORS = {
    "New lead": "#8A9993", "Contacted": "#2E6C8C", "Qualified": "#B67A1E",
    "Won": "#3E7C64", "Passed": "#C6432A",
}
US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
]
INDUSTRY_OPTIONS = sorted(set(CATEGORIES) | {
    "Security", "CCTV", "Access Control", "POS", "Payroll", "HR", "Insurance",
    "Merchant Services", "Medical Devices", "Foodservice", "SaaS", "Wholesale",
})
CUSTOMER_TYPE_OPTIONS = [
    "SMB", "Mid-market", "Enterprise", "Restaurants", "Retail", "Clinics",
    "Franchises", "Independent operators", "Multi-location", "Homeowners",
]
COMPENSATION_TYPE_OPTIONS = ["commission", "retainer", "bonus", "draw", "salary", "revenue share"]
REP_SORT_OPTIONS = ["Best Match", "Highest Rated", "Most Experienced", "Fastest Response", "Newest"]
AVAILABILITY_STATUS_OPTIONS = ["Open", "Selectively Open", "Not Open"]
AVAILABILITY_STATUS_VALUES = {
    "Open": "open",
    "Selectively Open": "selectively_open",
    "Not Open": "not_open",
}
AVAILABILITY_STATUS_LABELS = {v: k for k, v in AVAILABILITY_STATUS_VALUES.items()}
TERRITORY_TYPE_OPTIONS = ["flexible", "exclusive", "shared", "remote"]
OPPORTUNITY_SORT_OPTIONS = ["Newest", "Featured", "Highest Commission", "Most Relevant"]

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
USER_AGENT = "TerritoryProspector/1.0 (sales prospecting demo; contact via app)"


# --------------------------------------------------------------------------- #
# Data fetching
# --------------------------------------------------------------------------- #
def build_query(bbox: tuple[float, float, float, float], categories: list[str], cap: int) -> str:
    s, w, n, e = bbox
    box = f"({s},{w},{n},{e})"
    stmts = []
    for cat in categories:
        for filt in CATEGORIES[cat]["filters"]:
            stmts.append(f"  nwr{filt}{box};")
    body = "\n".join(stmts)
    return f"[out:json][timeout:35];\n(\n{body}\n);\nout center tags {cap};"


@st.cache_data(ttl=3600, show_spinner=False)
def geocode_area(query: str) -> tuple[float, float, float, float] | None:
    """Free-text area -> bounding box via Nominatim (OSM). Cached, rate-limit friendly."""
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        bb = data[0]["boundingbox"]  # [south, north, west, east] as strings
        south, north, west, east = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
        return (south, west, north, east)
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_overpass(query: str) -> list[dict]:
    """Run an Overpass query, trying mirrors in turn. Returns raw elements."""
    last_err = None
    for url in OVERPASS_ENDPOINTS:
        try:
            r = requests.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=60)
            if r.status_code == 200:
                return r.json().get("elements", [])
            last_err = f"HTTP {r.status_code}"
        except Exception as exc:  # try next mirror
            last_err = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"All Overpass endpoints failed ({last_err}).")


def which_category(tags: dict) -> str | None:
    """Map an element's tags back to one of our display categories."""
    amenity = tags.get("amenity", "")
    shop = tags.get("shop", "")
    leisure = tags.get("leisure", "")
    if amenity in {"restaurant", "cafe", "fast_food", "bar", "pub", "ice_cream"}:
        return "Restaurant & Café"
    if leisure in {"fitness_centre", "sports_centre"} or tags.get("sport") == "fitness":
        return "Fitness & Gym"
    if shop in {"car_repair", "tyres", "car_parts"} or amenity == "car_wash":
        return "Auto Services"
    if tags.get("craft") in {"plumber", "electrician", "carpenter", "hvac", "painter", "roofer"}:
        return "Home Services"
    if amenity in {"dentist", "doctors", "clinic", "pharmacy"}:
        return "Medical & Dental"
    if shop in {"clothes", "boutique", "gift", "books", "shoes", "jewelry", "florist", "furniture"}:
        return "Retail Boutique"
    if tags.get("office") in {"accountant", "lawyer", "insurance", "estate_agent", "financial", "it", "company", "consulting"}:
        return "Professional Svcs"
    if shop in {"hairdresser", "beauty"} or leisure == "spa":
        return "Beauty & Spa"
    return None


def score_prospects(df: pd.DataFrame, profile_name: str) -> pd.DataFrame:
    if df.empty:
        return df
    scored = df.copy()
    for idx, row in scored.iterrows():
        score, why = lead_score(row, profile_name)
        scored.at[idx, "score"] = score
        scored.at[idx, "heat"] = heat_of(score)
        scored.at[idx, "why"] = " · ".join(why)
        scored.at[idx, "insight"] = sales_insight(row, profile_name)
    return scored.sort_values("score", ascending=False).reset_index(drop=True)

def parse_elements(elements: list[dict], profile_name: str = "Marketing/Web") -> pd.DataFrame:
    rows = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        cat = which_category(tags)
        if cat is None:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        street = " ".join(x for x in [tags.get("addr:housenumber"), tags.get("addr:street")] if x)
        phone = tags.get("phone") or tags.get("contact:phone") or ""
        website = tags.get("website") or tags.get("contact:website") or ""
        independent = not (tags.get("brand") or tags.get("operator"))
        row = {
            "id": f'{el["type"]}/{el["id"]}',
            "name": name,
            "category": cat,
            "lat": float(lat),
            "lon": float(lon),
            "address": street,
            "phone": phone,
            "website": website,
            "hours": tags.get("opening_hours", ""),
            "cuisine": tags.get("cuisine", ""),
            "independent": independent,
        }
        score, why = lead_score(row, profile_name)
        row["score"] = score
        row["heat"] = heat_of(score)
        row["why"] = " · ".join(why)
        row["insight"] = sales_insight(row, profile_name)
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset="id").sort_values("score", ascending=False).reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
# Session pipeline
# --------------------------------------------------------------------------- #
def pipe() -> dict:
    return st.session_state.setdefault("pipe", {})


def set_stage(bid: str, stage: str, name: str, cat: str):
    p = pipe()
    if stage == "— none —":
        p.pop(bid, None)
    else:
        entry = p.get(bid, {})
        entry.update({"stage": stage, "name": name, "category": cat})
        p[bid] = entry


def set_note(bid: str, note: str, name: str, cat: str):
    p = pipe()
    if not note and bid not in p:
        return
    entry = p.setdefault(bid, {"name": name, "category": cat})
    entry["note"] = note


def set_follow_up(bid: str, next_follow_up: str, name: str, cat: str):
    p = pipe()
    entry = p.setdefault(bid, {"name": name, "category": cat, "stage": "New lead"})
    entry["next_follow_up"] = next_follow_up


def mark_contacted(bid: str, name: str, cat: str):
    entry = pipe().setdefault(bid, {"name": name, "category": cat, "stage": "Contacted"})
    entry["stage"] = "Contacted"
    entry["last_contacted"] = date.today().isoformat()
    entry["call_attempts"] = int(entry.get("call_attempts", 0) or 0) + 1


# --------------------------------------------------------------------------- #
# Rep marketplace (customer side)
# --------------------------------------------------------------------------- #
# "Best deal" is a match score = deal strength + rating + response speed.
RESPONSE_HOURS = {"< 1 hour": 0.5, "~2 hours": 2, "Same day": 8, "Within 24 hrs": 24, "1–2 days": 40}
RESPONSE_OPTS = list(RESPONSE_HOURS.keys())

# Seed roster so the marketplace is populated on day one. Reps can add themselves
# at runtime; making listings visible to *every* visitor needs a shared datastore
# (see README) — session listings are visible in the current browser only.
REPS_SEED: list[dict] = [
    {"id": "r1", "name": "Marcus Reed", "company": "Reed Digital Co.", "categories": ["Professional Svcs", "Retail Boutique"], "metros": ["Austin, TX", "Nashville, TN", "Denver, CO", "Bay Area, CA"], "deal": "20% off your first 3 months", "deal_strength": 0.80, "rating": 4.9, "reviews": 210, "response": "< 1 hour", "verified": True, "blurb": "Websites & local SEO for independent shops and service firms.", "email": "marcus@reeddigital.co", "phone": "(512) 555-0110"},
    {"id": "r2", "name": "Priya Nair", "company": "BrightLeaf Marketing", "categories": ["Restaurant & Café", "Beauty & Spa"], "metros": ["Austin, TX", "Tampa, FL"], "deal": "Free brand audit + 15% off first campaign", "deal_strength": 0.70, "rating": 4.8, "reviews": 156, "response": "~2 hours", "verified": True, "blurb": "Social + email marketing that fills tables and chairs.", "email": "priya@brightleaf.com", "phone": "(813) 555-0132"},
    {"id": "r3", "name": "Diego Alvarez", "company": "Frontier POS Systems", "categories": ["Restaurant & Café", "Retail Boutique"], "metros": ["Phoenix, AZ", "Denver, CO", "Austin, TX", "Bay Area, CA"], "deal": "First month free on any POS plan", "deal_strength": 0.75, "rating": 4.6, "reviews": 98, "response": "Same day", "verified": True, "blurb": "Point-of-sale & payments with next-day hardware.", "email": "diego@frontierpos.com", "phone": "(602) 555-0148"},
    {"id": "r4", "name": "Sarah Kim", "company": "Uplift Payroll", "categories": ["Professional Svcs", "Medical & Dental"], "metros": ["Seattle, WA", "Portland, OR", "Bay Area, CA"], "deal": "Waived $300 setup fee", "deal_strength": 0.60, "rating": 4.9, "reviews": 301, "response": "< 1 hour", "verified": True, "blurb": "Payroll & HR for small teams; onboarding in 48 hours.", "email": "sarah@upliftpayroll.com", "phone": "(206) 555-0171"},
    {"id": "r5", "name": "Tom Becker", "company": "Anvil Fitness Supply", "categories": ["Fitness & Gym"], "metros": ["Columbus, OH", "Chicago, IL", "Nashville, TN"], "deal": "25% volume discount on equipment", "deal_strength": 0.85, "rating": 4.4, "reviews": 67, "response": "Within 24 hrs", "verified": True, "blurb": "Commercial gym equipment, financing available.", "email": "tom@anvilfitness.com", "phone": "(614) 555-0195"},
    {"id": "r6", "name": "Lena Fischer", "company": "Coastal Insurance Group", "categories": ["Auto Services", "Home Services", "Professional Svcs"], "metros": ["Tampa, FL", "Atlanta, GA"], "deal": "Price-match guarantee + $50 back", "deal_strength": 0.65, "rating": 4.7, "reviews": 142, "response": "~2 hours", "verified": True, "blurb": "Commercial liability & fleet coverage.", "email": "lena@coastalins.com", "phone": "(813) 555-0210"},
    {"id": "r7", "name": "Andre Wallace", "company": "GreenRoute Logistics", "categories": ["Retail Boutique", "Home Services"], "metros": ["Atlanta, GA", "Nashville, TN"], "deal": "Free first delivery run", "deal_strength": 0.55, "rating": 4.5, "reviews": 54, "response": "Same day", "verified": False, "blurb": "Local same-day delivery for small retailers.", "email": "andre@greenroute.com", "phone": "(404) 555-0223"},
    {"id": "r8", "name": "Mia Torres", "company": "Glow Supply Partners", "categories": ["Beauty & Spa"], "metros": ["Austin, TX", "Phoenix, AZ", "Tampa, FL", "Bay Area, CA"], "deal": "Buy 2 get 1 free on starter kits", "deal_strength": 0.70, "rating": 4.8, "reviews": 189, "response": "< 1 hour", "verified": True, "blurb": "Salon & spa product supply with net-30 terms.", "email": "mia@glowsupply.com", "phone": "(512) 555-0246"},
    {"id": "r9", "name": "Kevin Osei", "company": "Meridian Medical Supply", "categories": ["Medical & Dental"], "metros": ["Chicago, IL", "Columbus, OH"], "deal": "10% off + net-60 terms", "deal_strength": 0.60, "rating": 4.9, "reviews": 223, "response": "Within 24 hrs", "verified": True, "blurb": "Consumables & equipment for clinics and dental offices.", "email": "kevin@meridianmed.com", "phone": "(312) 555-0268"},
    {"id": "r10", "name": "Rachel Stone", "company": "Redline Auto Parts", "categories": ["Auto Services"], "metros": ["Denver, CO", "Phoenix, AZ", "Seattle, WA", "Bay Area, CA"], "deal": "Trade discount up to 30%", "deal_strength": 0.90, "rating": 4.3, "reviews": 78, "response": "1–2 days", "verified": False, "blurb": "Wholesale parts for independent repair shops.", "email": "rachel@redlineparts.com", "phone": "(303) 555-0281"},
    {"id": "r11", "name": "Sam Whitfield", "company": "Homestead HVAC Wholesale", "categories": ["Home Services"], "metros": ["Portland, OR", "Seattle, WA", "Denver, CO"], "deal": "Free next-day shipping, no minimum", "deal_strength": 0.50, "rating": 4.6, "reviews": 110, "response": "Same day", "verified": True, "blurb": "HVAC parts & units for contractors.", "email": "sam@homesteadhvac.com", "phone": "(503) 555-0294"},
    {"id": "r12", "name": "Nadia Haddad", "company": "Keystone Books & Supply", "categories": ["Retail Boutique", "Professional Svcs"], "metros": ["Nashville, TN", "Atlanta, GA", "Columbus, OH"], "deal": "15% off bulk orders", "deal_strength": 0.60, "rating": 4.7, "reviews": 95, "response": "~2 hours", "verified": True, "blurb": "Wholesale books, gifts & office supply.", "email": "nadia@keystonebooks.com", "phone": "(615) 555-0307"},
    {"id": "r13", "name": "Chris Bell", "company": "PulsePoint Software", "categories": ["Fitness & Gym", "Beauty & Spa"], "metros": ["Austin, TX", "Denver, CO", "Portland, OR", "Bay Area, CA"], "deal": "Free 30-day trial + onboarding", "deal_strength": 0.80, "rating": 4.8, "reviews": 167, "response": "< 1 hour", "verified": True, "blurb": "Booking & membership software for studios and salons.", "email": "chris@pulsepoint.io", "phone": "(512) 555-0320"},
    {"id": "r14", "name": "Olivia Grant", "company": "Harbor Dental Supply", "categories": ["Medical & Dental", "Beauty & Spa"], "metros": ["Tampa, FL", "Atlanta, GA", "Chicago, IL"], "deal": "$500 off first equipment order", "deal_strength": 0.75, "rating": 4.9, "reviews": 198, "response": "Within 24 hrs", "verified": True, "blurb": "Dental & aesthetic equipment with install.", "email": "olivia@harbordental.com", "phone": "(813) 555-0333"},
    {"id": "r15", "name": "Derek Malone", "company": "Cornerstone Restaurant Supply", "categories": ["Restaurant & Café"], "metros": ["Chicago, IL", "Columbus, OH", "Nashville, TN"], "deal": "First month free + free install", "deal_strength": 0.80, "rating": 4.5, "reviews": 88, "response": "Same day", "verified": True, "blurb": "Kitchen equipment & smallwares, leasing available.", "email": "derek@cornerstonesupply.com", "phone": "(312) 555-0346"},
    {"id": "r16", "name": "Fatima Yusuf", "company": "Vantage Financial Advisors", "categories": ["Professional Svcs"], "metros": ["Seattle, WA", "Portland, OR", "Phoenix, AZ", "Bay Area, CA"], "deal": "Free consult + reduced first-year fee", "deal_strength": 0.65, "rating": 5.0, "reviews": 134, "response": "< 1 hour", "verified": True, "blurb": "Bookkeeping, tax & advisory for small business.", "email": "fatima@vantageadvisors.com", "phone": "(206) 555-0359"},
]

for _rep in REPS_SEED:
    _rep["is_sample"] = True


# ---- Shared marketplace store (Supabase — optional) ----------------------- #
# When Supabase secrets are present the marketplace becomes a real, shared,
# open marketplace: reps self-register into a Postgres table via the Supabase
# REST API and every visitor sees them. Without secrets the app runs in demo
# mode (seed roster + this-browser-session listings) so it still works locally.
def _supabase_cfg():
    try:
        cfg = st.secrets["supabase"]
        url, key = cfg["url"], cfg["key"]
        if url and key:
            return url.rstrip("/"), key
    except Exception:
        pass
    return None, None


SUPABASE_URL, SUPABASE_KEY = _supabase_cfg()
SUPABASE_ON = bool(SUPABASE_URL and SUPABASE_KEY)
try:
    SUPABASE_SERVICE_KEY = st.secrets["supabase"]["service_key"] or None
except Exception:
    SUPABASE_SERVICE_KEY = None
LIVE_WRITES_ON = bool(SUPABASE_ON and SUPABASE_SERVICE_KEY)
REP_FIELDS = ["name", "company", "categories", "metros", "deal", "deal_strength",
              "rating", "reviews", "response", "verified", "blurb", "email", "phone",
              "edit_code_hash", "active", "is_sample", "service_area", "service_lat",
              "service_lon", "service_radius_miles", "profile_slug", "headline",
              "years_experience", "industries", "customer_types", "states", "zip_codes",
              "territory_radius", "open_to_new_lines", "commission_min", "commission_max",
              "compensation_types", "existing_lines", "competing_lines", "website",
              "linkedin_url", "profile_status", "claimed", "claim_email",
              "last_active_at", "response_rate", "response_time_hours", "featured",
              "source", "availability_status", "preferred_categories",
              "preferred_company_types", "preferred_compensation",
              "minimum_commission", "notes_for_companies", "owner_user_id"]
LEAD_FIELDS = ["rep_id", "rep_company", "rep_email", "customer_name",
               "customer_email", "customer_phone", "message", "category", "metro",
               "review_token_hash"]
COMPANY_FIELDS = ["name", "slug", "logo_url", "website", "description", "industries",
                  "categories", "company_size", "headquarters", "states_needed",
                  "metros_needed", "customer_types", "opportunities", "verified",
                  "profile_status", "contact_name", "contact_email", "edit_code_hash",
                  "source", "owner_user_id", "featured"]
OPPORTUNITY_FIELDS = ["company_id", "title", "description", "categories", "industries",
                      "customer_types", "metros", "states", "zip_codes", "territory_type",
                      "compensation_types", "commission_min", "commission_max",
                      "recurring_commission", "exclusive_territory", "experience_required",
                      "active", "featured", "application_count", "expires_at",
                      "direct_competitors", "competitor_categories", "competitor_info_public",
                      "owner_user_id", "slug"]
PROFILE_CLAIM_FIELDS = [
    "rep_id", "claimant_email", "claimant_name", "message", "status",
    "verification_token_hash", "verification_sent_at", "email_verified_at",
    "reviewed_at", "reviewed_by", "admin_notes",
]
REVIEW_FIELDS = [
    "rep_id", "lead_id", "company_id", "opportunity_id", "rating", "reviewer",
    "customer_name", "title", "review", "comment", "verified_relationship",
    "verified", "status", "approved_at", "reviewed_at", "reviewed_by",
    "moderation_notes",
]
CONNECTION_FIELDS = [
    "company_id", "rep_id", "opportunity_id", "status", "message", "initiated_by",
    "owner_user_id",
]
CONTENT_REPORT_FIELDS = [
    "target_type", "target_id", "reason", "details", "reporter_email",
    "status", "reviewed_at", "reviewed_by", "admin_notes",
]
ANALYTICS_EVENTS = {
    "rep_profile_view", "opportunity_view", "search", "save_rep",
    "save_opportunity", "connection_request", "connection_accept",
    "claim_profile", "signup", "company_profile_view",
}
PUBLIC_REP_SELECT = ",".join([
    "id", "created_at", "name", "company", "categories", "metros", "deal",
    "deal_strength", "rating", "reviews", "response", "verified", "blurb",
    "active", "is_sample", "service_area", "service_lat", "service_lon",
    "service_radius_miles", "profile_slug", "headline", "years_experience",
    "industries", "customer_types", "states", "zip_codes", "territory_radius",
    "open_to_new_lines", "commission_min", "commission_max", "compensation_types",
    "existing_lines", "website", "linkedin_url", "profile_status", "claimed",
    "last_active_at", "response_rate", "response_time_hours", "featured", "source",
    "availability_status", "preferred_categories", "preferred_company_types",
    "preferred_compensation", "minimum_commission", "notes_for_companies",
])
PUBLIC_COMPANY_SELECT = ",".join([
    "id", "created_at", "updated_at", "name", "slug", "logo_url", "website",
    "description", "industries", "categories", "company_size", "headquarters",
    "states_needed", "metros_needed", "customer_types", "opportunities",
    "verified", "profile_status", "source", "featured",
])
PUBLIC_OPPORTUNITY_SELECT = ",".join([
    "id", "company_id", "created_at", "updated_at", "slug", "title",
    "description", "categories", "industries", "customer_types", "metros",
    "states", "zip_codes", "territory_type", "compensation_types",
    "commission_min", "commission_max", "recurring_commission",
    "exclusive_territory", "experience_required", "active", "featured",
    "application_count", "expires_at", "competitor_info_public",
    "companies(name,slug,verified)",
])
def _email_cfg():
    """Resend-over-SMTP config from secrets. Only `password` (Resend key) + `from` required."""
    try:
        s = st.secrets["smtp"]
        pwd, frm = s["password"], s["from"]
        if pwd and frm:
            return {"host": s.get("host", "smtp.resend.com"), "port": int(s.get("port", 587)),
                    "user": s.get("user", "resend"), "password": pwd, "from": frm}
    except Exception:
        pass
    return None


EMAIL_CFG = _email_cfg()
EMAIL_ON = EMAIL_CFG is not None


def _app_base_url() -> str:
    try:
        return (st.secrets["app"]["base_url"] or "").rstrip("/")
    except Exception:
        return ""


def _admin_code_hash() -> str:
    try:
        return st.secrets["admin"]["code_hash"] or ""
    except Exception:
        return ""


def monetization_enforced() -> bool:
    try:
        return bool(st.secrets["monetization"].get("enforce_entitlements", False))
    except Exception:
        return False


def _sb_headers(extra: dict | None = None) -> dict:
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def current_auth_session() -> dict:
    return st.session_state.get("auth_session") or {}


def current_auth_user_id() -> str:
    return str(current_auth_session().get("user_id") or "")


def current_auth_email() -> str:
    return str(current_auth_session().get("email") or "")


def current_account_profile() -> dict:
    return st.session_state.get("account_profile") or {}


def current_account_role() -> str:
    return normalize_account_role(current_account_profile().get("role"))


def current_admin_verified() -> bool:
    return bool(st.session_state.get("admin_verified", False))


def current_entitlements():
    profile = current_account_profile()
    return entitlement_context(
        role=current_account_role(),
        plan=profile.get("subscription_plan"),
        is_admin=is_admin_role(current_account_role(), current_admin_verified()),
        unrestricted=not monetization_enforced(),
    )


def entitlement_notice(feature: str):
    if monetization_enforced():
        st.info(f"{feature} is available on a paid plan.")


def record_entitlements(record: dict, role: str):
    return entitlement_context(
        role=role,
        plan=record.get("subscription_plan"),
        is_admin=False,
        unrestricted=not monetization_enforced(),
    )


def _sb_user_headers(extra: dict | None = None) -> dict:
    token = current_auth_session().get("access_token")
    if not token:
        raise RuntimeError("Please sign in before saving private account data.")
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {token}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def auth_rest_request(path: str, payload: dict, bearer: str = "") -> dict:
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    r = requests.post(f"{SUPABASE_URL}/auth/v1/{path}", headers=headers, json=payload, timeout=20)
    _sb_check(r)
    return r.json() if r.text else {}


def fetch_account_profile(user_id: str) -> dict:
    if not user_id:
        return {}
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/account_profiles",
        headers=_sb_user_headers(),
        params={"select": "*", "user_id": f"eq.{user_id}", "limit": 1},
        timeout=20,
    )
    _sb_check(r)
    rows = r.json()
    return rows[0] if rows else {}


def upsert_account_profile(user_id: str, email: str, role: str, display_name: str = "") -> dict:
    payload = {
        "user_id": user_id,
        "email": normalize_owner_email(email),
        "role": public_signup_role(role),
        "display_name": display_name.strip(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/account_profiles",
        headers=_sb_user_headers({"Prefer": "resolution=merge-duplicates,return=representation"}),
        params={"on_conflict": "user_id"},
        json=payload,
        timeout=20,
    )
    _sb_check(r)
    rows = r.json()
    return rows[0] if rows else payload


def refresh_admin_verified(user_id: str) -> bool:
    if not (LIVE_WRITES_ON and user_id):
        st.session_state["admin_verified"] = False
        return False
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/admin_account_roles",
            headers=_sb_service_headers(),
            params={"select": "user_id", "user_id": f"eq.{user_id}", "limit": 1},
            timeout=20,
        )
        _sb_check(r)
        ok = bool(r.json())
    except Exception:
        ok = False
    st.session_state["admin_verified"] = ok
    return ok


def qp_list(name: str) -> list[str]:
    raw = st.query_params.get(name, "")
    return clean_list(raw)


def qp_float(name: str, default: float) -> float:
    try:
        return float(st.query_params.get(name, default))
    except (TypeError, ValueError):
        return default


def qp_int(name: str, default: int) -> int:
    try:
        return int(float(st.query_params.get(name, default)))
    except (TypeError, ValueError):
        return default


def qp_bool(name: str, default: bool = False) -> bool:
    value = str(st.query_params.get(name, "")).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def query_param_defaults() -> dict:
    return {
        "keyword": st.query_params.get("q", ""),
        "categories": [v for v in qp_list("category") if v in CATEGORIES],
        "industries": [v for v in qp_list("industry") if v in INDUSTRY_OPTIONS],
        "metros": [v for v in qp_list("metro") if v in METROS],
        "states": [v for v in qp_list("state") if v in US_STATES],
        "zip_code": st.query_params.get("zip", ""),
        "customer_types": [v for v in qp_list("customer_type") if v in CUSTOMER_TYPE_OPTIONS],
        "min_years": qp_int("min_years", 0),
        "verified_only": qp_bool("verified", False),
        "open_only": qp_bool("open", False),
        "availability": [v for v in qp_list("availability") if v in AVAILABILITY_STATUS_VALUES],
        "compensation_types": [v for v in qp_list("comp") if v in COMPENSATION_TYPE_OPTIONS],
        "min_rating": qp_float("min_rating", 0.0),
        "territory_radius": qp_int("radius", 0),
        "sort": st.query_params.get("sort", "Best Match"),
    }


def sync_rep_search_query(filters: dict):
    current_rep = st.query_params.get("rep", "")
    review_token = st.query_params.get("review_token", "")
    params = {}
    if current_rep:
        params["rep"] = current_rep
    if review_token:
        params["review_token"] = review_token
    if filters["keyword"]:
        params["q"] = filters["keyword"]
    for key, query_key in [
        ("categories", "category"),
        ("industries", "industry"),
        ("metros", "metro"),
        ("states", "state"),
        ("customer_types", "customer_type"),
        ("availability", "availability"),
        ("compensation_types", "comp"),
    ]:
        if filters[key]:
            params[query_key] = ",".join(filters[key])
    if filters["zip_code"]:
        params["zip"] = filters["zip_code"]
    if filters["min_years"]:
        params["min_years"] = str(filters["min_years"])
    if filters["verified_only"]:
        params["verified"] = "1"
    if filters["open_only"]:
        params["open"] = "1"
    if filters["min_rating"]:
        params["min_rating"] = f"{filters['min_rating']:g}"
    if filters["territory_radius"]:
        params["radius"] = str(filters["territory_radius"])
    if filters["sort"] != "Best Match":
        params["sort"] = filters["sort"]
    st.query_params.clear()
    for key, value in params.items():
        st.query_params[key] = value


def pg_array_literal(values: list[str]) -> str:
    escaped = [str(v).replace("\\", "\\\\").replace('"', '\\"') for v in clean_list(values)]
    return "{" + ",".join(f'"{v}"' for v in escaped) + "}"


def get_auth_user(access_token: str) -> dict | None:
    if not SUPABASE_ON or not access_token.strip():
        return None
    r = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {access_token.strip()}"},
        timeout=20,
    )
    if r.status_code == 401:
        return None
    _sb_check(r)
    return r.json()


def _sb_check(r):
    """Raise a human-readable error (with Supabase's own message + a hint) on failure."""
    if r.status_code >= 400:
        hint = ""
        if r.status_code == 404:
            hint = (" — that table wasn't found in your Supabase project. Re-run the latest "
                    "supabase_setup.sql (it creates the required marketplace tables), then "
                    "retry. Also confirm the `url` secret is your Project URL.")
        elif r.status_code in (401, 403):
            hint = (" — check the configured Supabase key and the RLS policies from "
                    "supabase_setup.sql. Public writes now require the service_role key.")
        raise RuntimeError(f"Supabase {r.status_code}: {r.text[:300]}{hint}")


def normalize_rep_row(row: dict) -> dict:
    row["categories"] = clean_list(row.get("categories"))
    row["metros"] = clean_list(row.get("metros"))
    row["industries"] = clean_list(row.get("industries"))
    row["customer_types"] = clean_list(row.get("customer_types"))
    row["states"] = clean_list(row.get("states"))
    row["zip_codes"] = clean_list(row.get("zip_codes"))
    row["compensation_types"] = clean_list(row.get("compensation_types"))
    row["existing_lines"] = clean_list(row.get("existing_lines"))
    row["competing_lines"] = clean_list(row.get("competing_lines"))
    row["preferred_categories"] = clean_list(row.get("preferred_categories"))
    row["preferred_company_types"] = clean_list(row.get("preferred_company_types"))
    row["preferred_compensation"] = clean_list(row.get("preferred_compensation"))
    row["rating"] = row.get("rating") or 0.0
    row["reviews"] = row.get("reviews") or 0
    row["is_sample"] = bool(row.get("is_sample", False))
    row["active"] = row.get("active", True) is not False
    row["profile_status"] = row.get("profile_status") or "active"
    row["availability_status"] = availability_status(row).replace(" ", "_")
    row["open_to_new_lines"] = row["availability_status"] in {"open", "selectively_open"}
    row["featured"] = bool(row.get("featured", False))
    row["headline"] = row.get("headline") or row.get("blurb") or ""
    row["service_radius_miles"] = row.get("service_radius_miles") or 25
    row["territory_radius"] = row.get("territory_radius") or row["service_radius_miles"]
    row["response_time_hours"] = row.get("response_time_hours") or RESPONSE_HOURS.get(row.get("response"), 24)
    return row


def supabase_rep_search_params(filters: dict | None = None) -> dict:
    params = {
        "select": PUBLIC_REP_SELECT,
        "active": "eq.true",
        "profile_status": "eq.active",
        "limit": 200,
    }
    filters = filters or {}
    if filters.get("verified_only"):
        params["verified"] = "eq.true"
    if filters.get("open_only"):
        params["open_to_new_lines"] = "eq.true"
    if filters.get("availability"):
        values = [AVAILABILITY_STATUS_VALUES[v] for v in filters["availability"]]
        params["availability_status"] = f"in.({','.join(values)})"
    if filters.get("min_years"):
        params["years_experience"] = f"gte.{int(filters['min_years'])}"
    if filters.get("territory_radius"):
        params["territory_radius"] = f"gte.{int(filters['territory_radius'])}"
    if filters.get("categories"):
        params["categories"] = f"ov.{pg_array_literal(filters['categories'])}"
    if filters.get("industries"):
        params["industries"] = f"ov.{pg_array_literal(filters['industries'])}"
    if filters.get("metros"):
        params["metros"] = f"ov.{pg_array_literal(filters['metros'])}"
    # State/ZIP also have legacy fallbacks in `metros` and `service_area`, so
    # those are verified locally to avoid hiding older rows with null arrays.
    if filters.get("customer_types"):
        params["customer_types"] = f"ov.{pg_array_literal(filters['customer_types'])}"
    if filters.get("compensation_types"):
        params["compensation_types"] = f"ov.{pg_array_literal(filters['compensation_types'])}"
    keyword = (filters.get("keyword") or "").strip()
    if keyword:
        safe_keyword = re.sub(r"[^A-Za-z0-9 @._&/-]+", " ", keyword).strip()
        if safe_keyword:
            pattern = f"*{safe_keyword.split()[0]}*"
            params["or"] = (
                f"(name.ilike.{pattern},company.ilike.{pattern},headline.ilike.{pattern},"
                f"blurb.ilike.{pattern},deal.ilike.{pattern},website.ilike.{pattern})"
            )
    sort = filters.get("sort") or "Best Match"
    if sort == "Newest":
        params["order"] = "created_at.desc"
    elif sort == "Most Experienced":
        params["order"] = "years_experience.desc.nullslast"
    elif sort == "Fastest Response":
        params["order"] = "response_time_hours.asc.nullslast"
    elif sort == "Highest Rated":
        params["order"] = "rating.desc.nullslast,reviews.desc.nullslast"
    else:
        params["order"] = "featured.desc,verified.desc,rating.desc.nullslast"
    return params


@st.cache_data(ttl=45, show_spinner=False)
def fetch_reps_db(filters_key: tuple = ()) -> list[dict]:
    """Read marketplace listings from Supabase, using indexable filters when supplied."""
    filters = dict(filters_key) if filters_key else {}
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/reps", headers=_sb_headers(),
        params=supabase_rep_search_params(filters), timeout=20,
    )
    _sb_check(r)
    rows = r.json()
    for row in rows:
        row["id"] = f"db-{row.get('id')}"
        normalize_rep_row(row)
    return rows


def insert_reps_db(reps: list[dict]):
    """Insert one or more listings through the server-side service role, then bust the cache."""
    payload = [{k: rep.get(k) for k in REP_FIELDS} for rep in reps]
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/reps",
        headers=_sb_service_headers({"Prefer": "return=minimal"}), json=payload, timeout=20,
    )
    _sb_check(r)
    fetch_reps_db.clear()


def insert_reps_user_db(reps: list[dict]):
    """Insert listings with the signed-in user's token so Supabase RLS enforces ownership."""
    payload = [{k: rep.get(k) for k in REP_FIELDS} for rep in reps]
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/reps",
        headers=_sb_user_headers({"Prefer": "return=minimal"}), json=payload, timeout=20,
    )
    _sb_check(r)
    fetch_reps_db.clear()


def filter_key(filters: dict | None) -> tuple:
    if not filters:
        return ()
    parts = []
    for key, value in sorted(filters.items()):
        if isinstance(value, list):
            parts.append((key, tuple(value)))
        else:
            parts.append((key, value))
    return tuple(parts)


def all_reps(filters: dict | None = None) -> list[dict]:
    """Live shared marketplace from Supabase when configured; else demo (seed + session)."""
    if SUPABASE_ON:
        try:
            return fetch_reps_db(filter_key(filters))
        except Exception as exc:
            st.warning(f"Marketplace database unreachable ({exc}). Showing sample reps for now.")
            return [normalize_rep_row(rep.copy()) for rep in REPS_SEED]
    return [normalize_rep_row(rep.copy()) for rep in (REPS_SEED + st.session_state.setdefault("my_reps", []))]


def normalize_company_row(row: dict) -> dict:
    row["industries"] = clean_list(row.get("industries"))
    row["categories"] = clean_list(row.get("categories"))
    row["states_needed"] = clean_list(row.get("states_needed"))
    row["metros_needed"] = clean_list(row.get("metros_needed"))
    row["customer_types"] = clean_list(row.get("customer_types"))
    row["verified"] = bool(row.get("verified", False))
    row["featured"] = bool(row.get("featured", False))
    row["profile_status"] = row.get("profile_status") or "active"
    row["slug"] = row.get("slug") or slugify(row.get("name") or "company")
    row["website"] = safe_public_url(row.get("website") or "")
    row["logo_url"] = safe_public_url(row.get("logo_url") or "")
    return row


@st.cache_data(ttl=45, show_spinner=False)
def fetch_companies_db() -> list[dict]:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/companies",
        headers=_sb_headers(),
        params={"select": PUBLIC_COMPANY_SELECT, "profile_status": "eq.active", "order": "created_at.desc", "limit": 200},
        timeout=20,
    )
    _sb_check(r)
    rows = r.json()
    for row in rows:
        row["id"] = f"co-{row.get('id')}"
        normalize_company_row(row)
    return rows


def all_companies() -> list[dict]:
    if SUPABASE_ON:
        try:
            return fetch_companies_db()
        except Exception as exc:
            st.warning(f"Company directory database unreachable ({exc}). Showing this-session companies for now.")
    return [normalize_company_row(c.copy()) for c in st.session_state.setdefault("my_companies", [])]


def insert_company_db(company: dict):
    payload = {k: company.get(k) for k in COMPANY_FIELDS}
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/companies",
        headers=_sb_service_headers({"Prefer": "return=minimal"}),
        json=payload,
        timeout=20,
    )
    _sb_check(r)
    fetch_companies_db.clear()


def insert_company_user_db(company: dict):
    payload = {k: company.get(k) for k in COMPANY_FIELDS}
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/companies",
        headers=_sb_user_headers({"Prefer": "return=minimal"}),
        json=payload,
        timeout=20,
    )
    _sb_check(r)
    fetch_companies_db.clear()


def find_companies_by_email(email: str) -> list[dict]:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/companies",
        headers=_sb_service_headers(),
        params={"select": "*", "contact_email": f"eq.{normalize_owner_email(email)}", "order": "created_at.desc"},
        timeout=20,
    )
    _sb_check(r)
    rows = r.json()
    for row in rows:
        row["id"] = f"co-{row.get('id')}"
        normalize_company_row(row)
    return rows


def update_company_db(company_id: str, patch: dict):
    db_id = str(company_id).replace("co-", "")
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/companies?id=eq.{db_id}",
        headers=_sb_service_headers({"Prefer": "return=minimal"}),
        json=patch,
        timeout=20,
    )
    _sb_check(r)
    fetch_companies_db.clear()


def update_opportunity_db(opportunity_id, patch: dict):
    db_id = str(opportunity_id).replace("opp-", "").strip()
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/opportunities?id=eq.{db_id}",
        headers=_sb_service_headers({"Prefer": "return=minimal"}),
        json={k: v for k, v in patch.items() if k in OPPORTUNITY_FIELDS},
        timeout=20,
    )
    _sb_check(r)
    fetch_opportunities_db.clear()


def normalize_opportunity_row(row: dict) -> dict:
    for key in [
        "categories", "industries", "customer_types", "metros", "states", "zip_codes",
        "compensation_types", "direct_competitors", "competitor_categories",
    ]:
        row[key] = clean_list(row.get(key))
    row["territory_type"] = row.get("territory_type") or "flexible"
    row["recurring_commission"] = bool(row.get("recurring_commission", False))
    row["exclusive_territory"] = bool(row.get("exclusive_territory", False))
    row["active"] = row.get("active", True) is not False
    row["featured"] = bool(row.get("featured", False))
    row["competitor_info_public"] = bool(row.get("competitor_info_public", False))
    row["application_count"] = int(row.get("application_count") or 0)
    row["experience_required"] = int(row.get("experience_required") or 0)
    row["slug"] = row.get("slug") or slugify(row.get("title") or "opportunity")
    return row


@st.cache_data(ttl=45, show_spinner=False)
def fetch_opportunities_db(company_id: str = "") -> list[dict]:
    params = {
        "select": PUBLIC_OPPORTUNITY_SELECT,
        "active": "eq.true",
        "order": "featured.desc,created_at.desc",
        "limit": 250,
    }
    if company_id:
        params["company_id"] = f"eq.{company_id}"
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/opportunities",
        headers=_sb_headers(),
        params=params,
        timeout=20,
    )
    _sb_check(r)
    rows = r.json()
    for row in rows:
        row["id"] = f"opp-{row.get('id')}"
        normalize_opportunity_row(row)
    return rows


def all_opportunities(company_id: str = "") -> list[dict]:
    if SUPABASE_ON:
        try:
            return fetch_opportunities_db(str(company_id).replace("co-", ""))
        except Exception as exc:
            st.warning(f"Opportunity marketplace database unreachable ({exc}). Showing this-session opportunities for now.")
    rows = st.session_state.setdefault("my_opportunities", [])
    if company_id:
        cid = str(company_id).replace("co-", "")
        rows = [r for r in rows if str(r.get("company_id", "")).replace("co-", "") == cid]
    return [normalize_opportunity_row(r.copy()) for r in rows]


def insert_opportunity_db(opportunity: dict):
    payload = {k: opportunity.get(k) for k in OPPORTUNITY_FIELDS}
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/opportunities",
        headers=_sb_service_headers({"Prefer": "return=minimal"}),
        json=payload,
        timeout=20,
    )
    _sb_check(r)
    fetch_opportunities_db.clear()


def insert_opportunity_user_db(opportunity: dict):
    payload = {k: opportunity.get(k) for k in OPPORTUNITY_FIELDS}
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/opportunities",
        headers=_sb_user_headers({"Prefer": "return=minimal"}),
        json=payload,
        timeout=20,
    )
    _sb_check(r)
    fetch_opportunities_db.clear()


def analytics_metadata(metadata: dict | None) -> dict:
    safe = {}
    for key, value in (metadata or {}).items():
        if value in (None, "", []):
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value
        elif isinstance(value, list):
            safe[key] = [str(v) for v in value[:6]]
        else:
            safe[key] = str(value)
    return safe


def track_event(
    event_name: str,
    target_type: str = "",
    target_id: str = "",
    category: str = "",
    metro: str = "",
    metadata: dict | None = None,
):
    if event_name not in ANALYTICS_EVENTS or not LIVE_WRITES_ON:
        return
    payload = {
        "event_name": event_name,
        "actor_role": current_account_role() if current_auth_session() else "anonymous",
        "actor_user_id": current_auth_user_id() or None,
        "target_type": target_type or None,
        "target_id": str(target_id) if target_id not in (None, "") else None,
        "category": category or None,
        "metro": metro or None,
        "metadata": analytics_metadata(metadata),
    }
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/marketplace_events",
            headers=_sb_service_headers({"Prefer": "return=minimal"}),
            json=payload,
            timeout=8,
        )
    except Exception:
        pass


def track_once(key: str, event_name: str, **kwargs):
    seen = st.session_state.setdefault("tracked_events", [])
    if key in seen:
        return
    seen.append(key)
    track_event(event_name, **kwargs)


def normalize_connection_row(row: dict) -> dict:
    row["status"] = normalize_connection_status(row.get("status"))
    row["initiated_by"] = (row.get("initiated_by") or "company").strip().lower()
    row["message"] = row.get("message") or ""
    return row


@st.cache_data(ttl=30, show_spinner=False)
def fetch_connections_db(filter_key_value: tuple = ()) -> list[dict]:
    params = {"select": "*", "order": "created_at.desc", "limit": 250}
    filters = dict(filter_key_value) if filter_key_value else {}
    if filters.get("company_id"):
        params["company_id"] = f"eq.{filters['company_id']}"
    if filters.get("rep_id"):
        params["rep_id"] = f"eq.{filters['rep_id']}"
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/connections",
        headers=_sb_service_headers(),
        params=params,
        timeout=20,
    )
    _sb_check(r)
    return [normalize_connection_row(row) for row in r.json()]


def connection_filter_key(company_id: str = "", rep_id: str = "") -> tuple:
    parts = []
    if company_id:
        parts.append(("company_id", str(company_id).replace("co-", "")))
    if rep_id:
        parts.append(("rep_id", str(rep_id).replace("db-", "")))
    return tuple(parts)


def all_connections(company_id: str = "", rep_id: str = "") -> list[dict]:
    if SUPABASE_ON and SUPABASE_SERVICE_KEY:
        try:
            return fetch_connections_db(connection_filter_key(company_id, rep_id))
        except Exception as exc:
            st.warning(f"Connection requests unavailable ({exc}). Showing this-session requests for now.")
    rows = st.session_state.setdefault("session_connections", [])
    if company_id:
        cid = str(company_id).replace("co-", "")
        rows = [r for r in rows if str(r.get("company_id", "")) == cid]
    if rep_id:
        rid = str(rep_id).replace("db-", "")
        rows = [r for r in rows if str(r.get("rep_id", "")) == rid]
    return [normalize_connection_row(r.copy()) for r in rows]


def insert_connection_request(company: dict, rep: dict, opportunity_id=None, message: str = ""):
    payload = build_connection_payload(
        company_id=company.get("id"),
        rep_id=rep.get("id"),
        opportunity_id=opportunity_id,
        message=message,
        initiated_by="company",
    )
    existing = duplicate_open_connection(
        all_connections(company_id=str(payload.company_id)),
        company_id=payload.company_id,
        rep_id=payload.rep_id,
        opportunity_id=payload.opportunity_id,
    )
    if existing:
        return existing, False
    if rate_limited("connection_requests", 4):
        raise RuntimeError("Too many connection requests this session. Please slow down and try again later.")
    record = {k: getattr(payload, k) for k in CONNECTION_FIELDS if hasattr(payload, k)}
    record["owner_user_id"] = current_auth_user_id() or None
    if SUPABASE_ON:
        if not current_auth_session() and not SUPABASE_SERVICE_KEY:
            raise RuntimeError("Connection requests need a signed-in company account.")
        try:
            headers = _sb_user_headers({"Prefer": "return=representation"}) if current_auth_session() else _sb_service_headers({"Prefer": "return=representation"})
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/connections",
                headers=headers,
                json=record,
                timeout=20,
            )
            _sb_check(r)
            fetch_connections_db.clear()
            st.session_state["connection_requests"] = st.session_state.get("connection_requests", 0) + 1
            rows = r.json()
            connection = normalize_connection_row(rows[0] if rows else record)
            track_event(
                "connection_request",
                target_type="rep",
                target_id=str(payload.rep_id),
                metadata={
                    "company_id": str(payload.company_id),
                    "opportunity_id": str(payload.opportunity_id or ""),
                },
            )
            return connection, True
        except RuntimeError as exc:
            if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
                existing = duplicate_open_connection(
                    all_connections(company_id=str(payload.company_id)),
                    company_id=payload.company_id,
                    rep_id=payload.rep_id,
                    opportunity_id=payload.opportunity_id,
                )
                if existing:
                    return existing, False
            raise
    record["id"] = f"local-conn-{len(st.session_state.setdefault('session_connections', [])) + 1}"
    record["created_at"] = datetime.utcnow().isoformat()
    record["updated_at"] = record["created_at"]
    st.session_state.setdefault("session_connections", []).append(record)
    st.session_state["connection_requests"] = st.session_state.get("connection_requests", 0) + 1
    track_event(
        "connection_request",
        target_type="rep",
        target_id=str(payload.rep_id),
        metadata={"company_id": str(payload.company_id), "opportunity_id": str(payload.opportunity_id or "")},
    )
    return normalize_connection_row(record), True


def update_connection_status(connection: dict, status: str):
    new_status = normalize_connection_status(status)
    if new_status == "pending":
        raise RuntimeError("Choose accepted, declined, or withdrawn.")
    if SUPABASE_ON and SUPABASE_SERVICE_KEY and str(connection.get("id", "")).isdigit():
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/connections?id=eq.{connection.get('id')}",
            headers=_sb_service_headers({"Prefer": "return=minimal"}),
            json={"status": new_status},
            timeout=20,
        )
        _sb_check(r)
        fetch_connections_db.clear()
        if new_status == "accepted":
            track_event(
                "connection_accept",
                target_type="rep",
                target_id=str(connection.get("rep_id") or ""),
                metadata={"company_id": str(connection.get("company_id") or "")},
            )
        return
    for row in st.session_state.setdefault("session_connections", []):
        if str(row.get("id")) == str(connection.get("id")):
            row["status"] = new_status
            row["updated_at"] = datetime.utcnow().isoformat()
            break
    if new_status == "accepted":
        track_event(
            "connection_accept",
            target_type="rep",
            target_id=str(connection.get("rep_id") or ""),
            metadata={"company_id": str(connection.get("company_id") or "")},
        )


def shortlist_session_key() -> str:
    if "shortlist_session_key" not in st.session_state:
        st.session_state["shortlist_session_key"] = secrets.token_hex(16)
    return st.session_state["shortlist_session_key"]


def session_shortlist_items() -> list[dict]:
    shortlist_session_key()
    return st.session_state.setdefault("shortlist_items", [])


def save_shortlist_item(target_type: str, target_id, collection: str = "Saved") -> bool:
    item = build_shortlist_item(
        target_type=target_type,
        target_id=target_id,
        collection=collection,
        owner_type="anonymous",
        session_key=shortlist_session_key(),
    )
    items, created = upsert_session_shortlist(session_shortlist_items(), item)
    st.session_state["shortlist_items"] = items
    if created and target_type in {"rep", "opportunity"}:
        track_event(
            "save_rep" if target_type == "rep" else "save_opportunity",
            target_type=target_type,
            target_id=str(target_id),
            metadata={"collection": collection},
        )
    return created


def remove_shortlist_item(target_type: str, target_id):
    st.session_state["shortlist_items"] = remove_session_shortlist(session_shortlist_items(), target_type, target_id)


def item_saved(target_type: str, target_id) -> bool:
    return shortlist_is_saved(session_shortlist_items(), target_type, target_id)


def render_save_controls(target_type: str, target_id, key: str, default_collection: str = "Saved"):
    saved = item_saved(target_type, target_id)
    c1, c2 = st.columns([1.15, 1])
    with c1:
        collection = st.selectbox(
            "Collection",
            COLLECTIONS,
            index=COLLECTIONS.index(default_collection) if default_collection in COLLECTIONS else 0,
            key=f"collection_{key}",
            label_visibility="collapsed",
        )
    with c2:
        if saved:
            if st.button("Remove", key=f"remove_{key}", use_container_width=True):
                remove_shortlist_item(target_type, target_id)
                st.success("Removed from shortlist.")
                st.rerun()
        else:
            if st.button("Save", key=f"save_{key}", use_container_width=True):
                save_shortlist_item(target_type, target_id, collection)
                st.success(f"Saved to {collection}.")
                st.rerun()


def shortlist_targets(target_type: str) -> set[str]:
    return {str(item.get("target_id")) for item in session_shortlist_items() if item.get("target_type") == target_type}


def territory_intelligence_rows(metro: str = "", state: str = "", category: str = "", industry: str = "") -> tuple[list[dict], list[dict], list[dict]]:
    if not SUPABASE_ON:
        return [], [], []
    rep_filters = {
        "categories": [category] if category else [],
        "industries": [industry] if industry else [],
        "metros": [metro] if metro else [],
        "states": [state] if state else [],
    }
    reps = fetch_reps_db(filter_key(rep_filters))
    companies = fetch_companies_db()
    opportunities = fetch_opportunities_db()
    return reps, opportunities, companies


# ---- Leads: capture customer requests, notify the rep --------------------- #
def _sb_service_headers(extra: dict | None = None) -> dict:
    if not SUPABASE_SERVICE_KEY:
        raise RuntimeError("Live writes need `[supabase].service_key` configured.")
    h = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def insert_lead_db(lead: dict):
    """Save a lead to Supabase through the server-side service role."""
    r = requests.post(f"{SUPABASE_URL}/rest/v1/leads",
                      headers=_sb_service_headers({"Prefer": "return=representation"}),
                      json={k: lead.get(k) for k in LEAD_FIELDS}, timeout=20)
    _sb_check(r)
    rows = r.json()
    return rows[0] if rows else {}


def fetch_rep_private_contact(rep_id: str) -> dict:
    """Read private contact fields only for server-side lead delivery."""
    db_id = str(rep_id or "").replace("db-", "").strip()
    if not db_id.isdigit():
        return {}
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/reps",
        headers=_sb_service_headers(),
        params={"select": "id,name,company,email,phone", "id": f"eq.{db_id}", "limit": 1},
        timeout=20,
    )
    _sb_check(r)
    rows = r.json()
    return rows[0] if rows else {}


def fetch_leads_db(rep_email: str) -> list[dict]:
    """Read a rep's leads. Requires the service_role key (leads aren't publicly readable)."""
    r = requests.get(f"{SUPABASE_URL}/rest/v1/leads", headers=_sb_service_headers(),
                     params={"select": "*", "rep_email": f"eq.{rep_email}",
                             "order": "created_at.desc"}, timeout=20)
    _sb_check(r)
    return r.json()


def fetch_pipeline_db(owner_email: str, owner_key_hash: str, owner_user_id: str = "") -> list[dict]:
    """Read a rep's private pipeline. Requires the service_role key."""
    params = {"select": "*", "order": "updated_at.desc"}
    if owner_user_id:
        params["owner_user_id"] = f"eq.{owner_user_id}"
    else:
        params["owner_email"] = f"eq.{owner_email}"
        params["owner_key_hash"] = f"eq.{owner_key_hash}"
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/pipeline_entries",
        headers=_sb_service_headers(),
        params=params,
        timeout=20,
    )
    _sb_check(r)
    return r.json()


def save_pipeline_db(owner_email: str, owner_key_hash: str, entries: dict, owner_user_id: str = ""):
    """Upsert pipeline rows for one rep. Requires a unique(owner_email, prospect_id) index."""
    payload = build_pipeline_payload(owner_email, owner_key_hash, entries)
    for row in payload:
        row["owner_user_id"] = owner_user_id or None
    if not payload:
        return
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/pipeline_entries",
        headers=_sb_service_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
        params={"on_conflict": "owner_email,owner_key_hash,prospect_id"},
        json=payload,
        timeout=20,
    )
    _sb_check(r)


def send_lead_email(rep: dict, lead: dict) -> bool:
    """Notify the rep of a new lead via Resend SMTP. Returns True if actually sent."""
    to = (rep.get("email") or "").strip()
    if not EMAIL_ON or "@" not in to:
        return False
    msg = EmailMessage()
    msg["Subject"] = f"New lead from {lead['customer_name']} — Find a Rep"
    msg["From"] = EMAIL_CFG["from"]
    msg["To"] = to
    if lead.get("customer_email"):
        msg["Reply-To"] = lead["customer_email"]
    msg.set_content("\n".join([
        f"You have a new lead from the Find a Rep marketplace for {rep['company']}.",
        "",
        f"Name:        {lead['customer_name']}",
        f"Email:       {lead.get('customer_email') or '—'}",
        f"Phone:       {lead.get('customer_phone') or '—'}",
        f"Looking for: {lead.get('category') or 'any service'} in {lead.get('metro') or 'any area'}",
        "",
        "Message:",
        lead.get("message") or "(none)",
        "",
        "Reply directly to this email to reach them.",
    ]))
    with smtplib.SMTP(EMAIL_CFG["host"], EMAIL_CFG["port"], timeout=20) as s:
        s.starttls()
        s.login(EMAIL_CFG["user"], EMAIL_CFG["password"])
        s.send_message(msg)
    return True


def send_review_link_email(to: str, rep: dict, review_link: str) -> bool:
    if not EMAIL_ON or "@" not in (to or "") or not review_link:
        return False
    msg = EmailMessage()
    msg["Subject"] = f"Review your intro to {rep['company']}"
    msg["From"] = EMAIL_CFG["from"]
    msg["To"] = to
    msg.set_content("\n".join([
        f"Thanks for requesting an intro to {rep['company']}.",
        "",
        "After you've connected, you can leave one verified review here:",
        review_link,
        "",
        "This one-time link helps keep marketplace ratings tied to real requests.",
    ]))
    with smtplib.SMTP(EMAIL_CFG["host"], EMAIL_CFG["port"], timeout=20) as s:
        s.starttls()
        s.login(EMAIL_CFG["user"], EMAIL_CFG["password"])
        s.send_message(msg)
    return True


def make_review_link(token: str) -> str:
    base = _app_base_url()
    if not base:
        return ""
    return f"{base}?{urlencode({'review_token': token})}"


def submit_lead(rep: dict, name: str, email: str, phone: str, message: str):
    review_token = secrets.token_urlsafe(24)
    rep_for_delivery = rep
    if LIVE_WRITES_ON and not rep.get("email"):
        try:
            private_contact = fetch_rep_private_contact(str(rep.get("id", "")))
            rep_for_delivery = {**rep, **private_contact}
        except Exception:
            rep_for_delivery = rep
    lead = {
        "rep_id": str(rep.get("id", "")), "rep_company": rep_for_delivery.get("company", ""),
        "rep_email": rep_for_delivery.get("email", ""), "customer_name": name,
        "customer_email": email, "customer_phone": phone, "message": message,
        "category": "" if cust_category == "Any category" else cust_category,
        "metro": "" if cust_metro == "Anywhere" else cust_metro,
        "review_token_hash": _hash_code(review_token),
    }
    saved = emailed = review_emailed = False
    err = None
    if LIVE_WRITES_ON:
        try:
            insert_lead_db(lead)
            saved = True
        except Exception as exc:
            err = str(exc)
    try:
        emailed = send_lead_email(rep_for_delivery, lead)
    except Exception as exc:
        err = err or str(exc)
    try:
        review_emailed = send_review_link_email(email, rep, make_review_link(review_token))
    except Exception as exc:
        err = err or str(exc)
    st.session_state.setdefault("intro_requests", []).append(
        {"rep": rep["name"], "company": rep["company"],
         "when": datetime.now().strftime("%Y-%m-%d %H:%M")})
    if emailed:
        st.success(f"Sent! {rep['company']} received your request by email and will reach out.")
    elif saved:
        st.success(f"Request delivered to {rep['company']}'s leads — they'll follow up.")
    else:
        st.info(f"Request logged for {rep['company']} (demo mode — add Supabase/email secrets to deliver it).")
    if err and not (saved or emailed):
        st.caption(f"Delivery note: {err}")
    if saved and not review_emailed:
        st.caption("Verified review token created. Configure `[app].base_url` and SMTP to email review links automatically.")


# ---- Ratings & reviews ---------------------------------------------------- #
@st.cache_data(ttl=45, show_spinner=False)
def fetch_reviews_db() -> list[dict]:
    r = requests.get(f"{SUPABASE_URL}/rest/v1/reviews", headers=_sb_headers(),
                     params={"select": "*", "order": "created_at.desc"}, timeout=20)
    _sb_check(r)
    return r.json()


def all_reviews() -> list[dict]:
    if SUPABASE_ON:
        try:
            return fetch_reviews_db()
        except Exception:
            return []
    return st.session_state.setdefault("session_reviews", [])


def reviews_summary(reviews: list[dict]) -> dict:
    """rep_id -> {avg, count, recent[]} from approved reviews only."""
    return aggregate_reviews_summary(reviews)


def effective_rating(rep: dict, summary: dict):
    """Approved-review average when present; seed rows keep their demo fallback."""
    a = summary.get(normalize_rep_review_id(rep.get("id", "")))
    if a and a["count"] > 0:
        return a["avg"], a["count"], True
    if rep.get("is_sample") or str(rep.get("id", "")).startswith("r"):
        return float(rep.get("rating", 0) or 0), int(rep.get("reviews", 0) or 0), False
    return 0.0, 0, False


def fetch_review_lead(token: str) -> dict | None:
    token_hash = _hash_code(token)
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/leads",
        headers=_sb_service_headers(),
        params={"select": "id,rep_id,customer_name,review_token_used_at",
                "review_token_hash": f"eq.{token_hash}", "limit": 1},
        timeout=20,
    )
    _sb_check(r)
    rows = r.json()
    return rows[0] if rows else None


def mark_review_token_used(lead_id):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}",
        headers=_sb_service_headers({"Prefer": "return=minimal"}),
        json={"review_token_used_at": datetime.utcnow().isoformat()},
        timeout=20,
    )
    _sb_check(r)


def insert_review(rep: dict, rating: int, name: str, title: str, comment: str, token: str = ""):
    if LIVE_WRITES_ON:
        lead = fetch_review_lead(token)
        if not lead:
            raise RuntimeError("That review link is invalid.")
        if lead.get("review_token_used_at"):
            raise RuntimeError("That review link has already been used.")
        if normalize_rep_review_id(lead.get("rep_id")) != normalize_rep_review_id(rep.get("id", "")):
            raise RuntimeError("That review link is for a different rep.")
        if has_duplicate_review(fetch_reviews_db(), rep_id=rep.get("id"), lead_id=lead.get("id"), reviewer=name):
            raise RuntimeError("A review has already been submitted for this relationship.")
        payload = build_review_payload(
            rep_id=rep.get("id"),
            rating=rating,
            reviewer=name,
            title=title,
            review=comment,
            verified_relationship=True,
            lead_id=lead.get("id"),
        )
        rec = {
            "rep_id": payload.rep_id,
            "lead_id": payload.lead_id,
            "rating": payload.rating,
            "reviewer": payload.reviewer,
            "customer_name": payload.reviewer,
            "title": payload.title,
            "review": payload.review,
            "comment": payload.review,
            "verified_relationship": True,
            "verified": True,
            "status": "pending",
        }
        r = requests.post(f"{SUPABASE_URL}/rest/v1/reviews",
                          headers=_sb_service_headers({"Prefer": "return=minimal"}),
                          json=rec, timeout=20)
        _sb_check(r)
        mark_review_token_used(lead.get("id"))
        fetch_reviews_db.clear()
    else:
        payload = build_review_payload(
            rep_id=rep.get("id"),
            rating=rating,
            reviewer=name,
            title=title,
            review=comment,
            verified_relationship=True,
        )
        session_reviews = st.session_state.setdefault("session_reviews", [])
        if has_duplicate_review(session_reviews, rep_id=rep.get("id"), reviewer=name):
            raise RuntimeError("A review from that reviewer already exists for this rep.")
        rec = {
            "rep_id": payload.rep_id,
            "rating": payload.rating,
            "reviewer": payload.reviewer,
            "customer_name": payload.reviewer,
            "title": payload.title,
            "review": payload.review,
            "comment": payload.review,
            "verified_relationship": True,
            "verified": True,
            "status": "approved",
        }
        st.session_state.setdefault("session_reviews", []).append(rec)


def rep_for_review_token(token: str) -> tuple[dict | None, dict | None, str | None]:
    if not LIVE_WRITES_ON:
        return None, None, "Verified review links need Supabase and `[supabase].service_key` configured."
    lead = fetch_review_lead(token)
    if not lead:
        return None, None, "That review link is invalid."
    if lead.get("review_token_used_at"):
        return None, lead, "That review link has already been used."
    for rep in all_reps():
        if str(rep.get("id")) == str(lead.get("rep_id")):
            return rep, lead, None
    return None, lead, "The rep for this review link is no longer listed."


# ---- Rep identity, trust & safety, listing management --------------------- #
BANNED_WORDS = {"viagra", "casino", "porn", "xxx", "escort", "loan shark", "bitcoin doubler"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_listing(name, company, email, deal, blurb, existing) -> str | None:
    """Trust & safety checks at sign-up. Returns an error message, or None if OK."""
    if not EMAIL_RE.match(email or ""):
        return "Enter a valid contact email — you'll need it to manage your listing."
    blob = f"{name} {company} {deal} {blurb}".lower()
    if any(b in blob for b in BANNED_WORDS):
        return "Your listing contains blocked words. Please revise it."
    if re.search(r"https?://|www\.", f"{name} {company}"):
        return "Please don't put links in your name or company."
    if len(deal) > 120 or len(blurb) > 200:
        return "Keep the deal under 120 and the description under 200 characters."
    ce = (company.strip().lower(), (email or "").strip().lower())
    for r in existing:
        if (r.get("company", "").strip().lower(), (r.get("email", "") or "").strip().lower()) == ce:
            return "A listing with this company and email already exists — use “Manage your listing” to edit it."
    return None


def rate_limited(bucket: str = "signups_this_session", limit: int = 3) -> bool:
    return st.session_state.get(bucket, 0) >= limit


def send_edit_code_email(to: str, company: str, code: str) -> bool:
    if not EMAIL_ON or "@" not in (to or ""):
        return False
    msg = EmailMessage()
    msg["Subject"] = f"Your Find a Rep edit code for {company}"
    msg["From"] = EMAIL_CFG["from"]
    msg["To"] = to
    msg.set_content(
        f"Thanks for listing {company} on the Find a Rep marketplace.\n\n"
        f"Your edit code is: {code}\n\n"
        "Keep it safe — you'll use it (with this email) to edit, pause, or remove your listing.")
    with smtplib.SMTP(EMAIL_CFG["host"], EMAIL_CFG["port"], timeout=20) as s:
        s.starttls()
        s.login(EMAIL_CFG["user"], EMAIL_CFG["password"])
        s.send_message(msg)
    return True


def find_listings_by_email(email: str) -> list[dict]:
    r = requests.get(f"{SUPABASE_URL}/rest/v1/reps", headers=_sb_service_headers(),
                     params={"select": "*", "email": f"eq.{email}"}, timeout=20)
    _sb_check(r)
    return r.json()


def update_rep_db(db_id, patch: dict):
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/reps?id=eq.{db_id}",
                       headers=_sb_service_headers({"Prefer": "return=minimal"}),
                       json=patch, timeout=20)
    _sb_check(r)
    fetch_reps_db.clear()


def delete_rep_db(db_id):
    r = requests.delete(f"{SUPABASE_URL}/rest/v1/reps?id=eq.{db_id}",
                        headers=_sb_service_headers(), timeout=20)
    _sb_check(r)
    fetch_reps_db.clear()


def insert_profile_claim_db(rep: dict, claimant_email: str, claimant_name: str = "", message: str = ""):
    if not LIVE_WRITES_ON:
        raise RuntimeError("Claim requests need live Supabase plus `[supabase].service_key` configured.")
    payload = build_profile_claim_payload(rep, claimant_email, claimant_name, message)
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/profile_claims",
        headers=_sb_service_headers({"Prefer": "return=minimal"}),
        json={k: getattr(payload, k) for k in ["rep_id", "claimant_email", "claimant_name", "message", "status"]},
        timeout=20,
    )
    _sb_check(r)
    track_event("claim_profile", target_type="rep", target_id=str(rep.get("id") or ""))


def update_profile_claim_db(claim_id, patch: dict):
    db_id = str(claim_id).strip()
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/profile_claims?id=eq.{db_id}",
        headers=_sb_service_headers({"Prefer": "return=minimal"}),
        json={k: v for k, v in patch.items() if k in PROFILE_CLAIM_FIELDS},
        timeout=20,
    )
    _sb_check(r)


def approve_profile_claim(claim: dict, admin_email: str = "", notes: str = ""):
    now = datetime.utcnow().isoformat()
    update_profile_claim_db(claim.get("id"), {
        "status": "approved",
        "reviewed_at": now,
        "reviewed_by": admin_email.strip(),
        "admin_notes": notes.strip(),
    })
    update_rep_db(claim.get("rep_id"), {
        "claimed": True,
        "claim_email": (claim.get("claimant_email") or "").strip().lower(),
        "source": "claimed",
        "last_active_at": now,
    })


def reject_profile_claim(claim: dict, admin_email: str = "", notes: str = ""):
    update_profile_claim_db(claim.get("id"), {
        "status": "rejected",
        "reviewed_at": datetime.utcnow().isoformat(),
        "reviewed_by": admin_email.strip(),
        "admin_notes": notes.strip(),
    })


def update_review_db(review_id, patch: dict):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/reviews?id=eq.{str(review_id).strip()}",
        headers=_sb_service_headers({"Prefer": "return=minimal"}),
        json={k: v for k, v in patch.items() if k in REVIEW_FIELDS},
        timeout=20,
    )
    _sb_check(r)
    fetch_reviews_db.clear()


def moderate_review(review: dict, status: str, reviewer: str = "", notes: str = ""):
    normalized = normalize_review_status(status)
    if normalized == "pending":
        raise RuntimeError("Choose approved or rejected.")
    now = datetime.utcnow().isoformat()
    patch = {
        "status": normalized,
        "reviewed_at": now,
        "reviewed_by": reviewer.strip(),
        "moderation_notes": notes.strip(),
    }
    if normalized == "approved":
        patch["approved_at"] = now
    update_review_db(review.get("id"), patch)


def fetch_table_db(table: str, limit: int = 50) -> list[dict]:
    allowed = {
        "reps", "leads", "reviews", "companies", "profile_claims",
        "connections", "opportunities", "content_reports", "account_profiles",
        "marketplace_events",
    }
    if table not in allowed:
        raise ValueError("Unsupported table")
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_service_headers(),
        params={"select": "*", "order": "created_at.desc", "limit": limit},
        timeout=20,
    )
    _sb_check(r)
    return r.json()


def update_content_report_db(report_id, patch: dict):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/content_reports?id=eq.{str(report_id).strip()}",
        headers=_sb_service_headers({"Prefer": "return=minimal"}),
        json={k: v for k, v in patch.items() if k in CONTENT_REPORT_FIELDS},
        timeout=20,
    )
    _sb_check(r)


def admin_access_unlocked() -> bool:
    return bool(
        LIVE_WRITES_ON
        and (
            is_admin_role(current_account_role(), current_admin_verified())
            or st.session_state.get("admin_code_ok")
        )
    )


def render_admin_gate() -> bool:
    if not LIVE_WRITES_ON:
        st.warning("Admin dashboard needs live Supabase plus `[supabase].service_key`.")
        return False
    if is_admin_role(current_account_role(), current_admin_verified()):
        return True
    if st.session_state.get("admin_code_ok"):
        return True
    st.info("Sign in as an approved admin, or enter the configured admin code.")
    if not _admin_code_hash():
        st.caption("No `[admin].code_hash` fallback is configured.")
        return False
    code = st.text_input("Admin code", type="password", key="admin_dashboard_code")
    if st.button("Unlock admin dashboard", type="primary", key="admin_dashboard_unlock"):
        if code and _hash_code(code) == _admin_code_hash():
            st.session_state["admin_code_ok"] = True
            st.rerun()
        st.error("Invalid admin code.")
    return False


def admin_filter_rows(rows: list[dict], query: str, status: str = "", field: str = "profile_status") -> list[dict]:
    query = (query or "").strip().lower()
    filtered = rows
    if status and status != "All":
        filtered = [r for r in filtered if str(r.get(field) or "").lower() == status.lower()]
    if query:
        terms = [t for t in re.split(r"\s+", query) if t]
        filtered = [
            r for r in filtered
            if all(term in " ".join(str(v) for v in r.values() if v is not None).lower() for term in terms)
        ]
    return filtered


def admin_dataframe(rows: list[dict], columns: list[str], empty: str):
    if not rows:
        st.caption(empty)
        return
    df = pd.DataFrame(rows)
    st.dataframe(df[[c for c in columns if c in df.columns]], use_container_width=True, hide_index=True)


def events_dataframe(events: list[dict]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(columns=["created_at", "event_name", "category", "metro"])
    df = pd.DataFrame(events)
    if "created_at" in df.columns:
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
        df["day"] = df["created_at"].dt.date.astype(str)
    else:
        df["day"] = ""
    for col in ["event_name", "category", "metro", "target_type", "target_id"]:
        if col not in df.columns:
            df[col] = ""
    return df


def render_admin_analytics(events: list[dict], reps: list[dict], companies: list[dict], opportunities: list[dict], connections: list[dict]):
    df = events_dataframe(events)
    active_reps = [r for r in reps if r.get("active", True) is not False and (r.get("profile_status") or "active") == "active"]
    active_companies = [c for c in companies if (c.get("profile_status") or "active") == "active"]
    active_opps = [o for o in opportunities if o.get("active") is not False]
    accepted = [c for c in connections if normalize_connection_status(c.get("status")) == "accepted"]
    requests_count = int((df["event_name"] == "connection_request").sum()) if not df.empty else 0
    accepts_count = int((df["event_name"] == "connection_accept").sum()) if not df.empty else 0
    if requests_count == 0:
        requests_count = len(connections)
    if accepts_count == 0:
        accepts_count = len(accepted)
    acceptance_rate = None if requests_count == 0 else accepts_count / requests_count

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Active reps", len(active_reps))
    a2.metric("Active companies", len(active_companies))
    a3.metric("Active opportunities", len(active_opps))
    a4.metric("Acceptance rate", "n/a" if acceptance_rate is None else f"{acceptance_rate:.0%}")

    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Profile views", int((df["event_name"] == "rep_profile_view").sum()) if not df.empty else 0)
    b2.metric("Company views", int((df["event_name"] == "company_profile_view").sum()) if not df.empty else 0)
    b3.metric("Opportunity views", int((df["event_name"] == "opportunity_view").sum()) if not df.empty else 0)
    b4.metric("Connection requests", requests_count)

    st.subheader("Searches per day")
    if df.empty or not (df["event_name"] == "search").any():
        st.caption("No search events recorded yet.")
    else:
        searches = (
            df[df["event_name"] == "search"]
            .groupby("day", dropna=False)
            .size()
            .reset_index(name="searches")
            .sort_values("day", ascending=False)
        )
        st.dataframe(searches, use_container_width=True, hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Most searched categories")
        if df.empty:
            st.caption("No category search data yet.")
        else:
            cats = df[(df["event_name"] == "search") & (df["category"].fillna("") != "")]
            if cats.empty:
                st.caption("No category search data yet.")
            else:
                st.dataframe(cats.groupby("category").size().reset_index(name="searches").sort_values("searches", ascending=False), use_container_width=True, hide_index=True)
    with c2:
        st.subheader("Most searched metros")
        if df.empty:
            st.caption("No metro search data yet.")
        else:
            metros = df[(df["event_name"] == "search") & (df["metro"].fillna("") != "")]
            if metros.empty:
                st.caption("No metro search data yet.")
            else:
                st.dataframe(metros.groupby("metro").size().reset_index(name="searches").sort_values("searches", ascending=False), use_container_width=True, hide_index=True)

    st.subheader("Recent events")
    if df.empty:
        st.caption("No analytics events recorded yet.")
    else:
        recent = df.sort_values("created_at", ascending=False).head(50)
        st.dataframe(
            recent[[c for c in ["created_at", "event_name", "actor_role", "target_type", "target_id", "category", "metro"] if c in recent.columns]],
            use_container_width=True,
            hide_index=True,
        )


def rep_score(rep: dict, rating: float | None = None) -> int:
    """Best-match score (0–100): deal strength (40) + rating (35) + response speed (25)."""
    deal = rep.get("deal_strength", 0) * 40
    rt = rep.get("rating", 4.0) if rating is None else rating
    rating_c = ((rt - 3.0) / 2.0) * 35
    hrs = RESPONSE_HOURS.get(rep.get("response", "Within 24 hrs"), 24)
    resp = (1 - min(hrs, 48) / 48) * 25
    return int(max(0, min(100, round(deal + rating_c + resp))))


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
def switch_audience(label: str):
    st.session_state["audience_mode"] = label


def switch_to_rep_search(keyword: str = "", metro: str = "", category: str = ""):
    st.session_state["audience_mode"] = "🛍️ Company — find reps"
    if keyword:
        st.session_state["rep_keyword"] = keyword
    if metro:
        st.session_state["rep_metros"] = [metro]
    if category:
        st.session_state["rep_categories"] = [category]


def activate_auth_session(data: dict, role_hint: str = "rep", display_name: str = ""):
    session = auth_session_from_response(data)
    st.session_state["auth_session"] = {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "user_id": session.user_id,
        "email": session.email,
    }
    profile = fetch_account_profile(session.user_id)
    if not profile:
        profile = upsert_account_profile(session.user_id, session.email, role_hint, display_name)
    st.session_state["account_profile"] = profile
    refresh_admin_verified(session.user_id)


def render_auth_panel():
    with st.sidebar:
        st.subheader("Account")
        if not SUPABASE_ON:
            st.caption("Demo mode. Add Supabase secrets to enable accounts.")
            return
        session = current_auth_session()
        if session:
            role = current_account_role()
            email = current_auth_email() or current_account_profile().get("email") or "Signed in"
            badge = "admin" if is_admin_role(role, current_admin_verified()) else role
            plan = plan_label(current_account_profile().get("subscription_plan"))
            suffix = "" if monetization_enforced() else " · dev unrestricted"
            st.caption(f"{email} · {badge} · {plan}{suffix}")
            if st.button("Sign out", use_container_width=True):
                try:
                    auth_rest_request("logout", {}, bearer=session.get("access_token", ""))
                except Exception:
                    pass
                for key in ["auth_session", "account_profile", "admin_verified"]:
                    st.session_state.pop(key, None)
                st.rerun()
            return

        auth_tab, signup_tab, reset_tab = st.tabs(["Sign in", "Sign up", "Reset"])
        with auth_tab:
            email = st.text_input("Email", key="auth_signin_email")
            password = st.text_input("Password", type="password", key="auth_signin_password")
            if st.button("Sign in", type="primary", use_container_width=True, key="auth_signin_btn"):
                if not (email and password):
                    st.error("Enter your email and password.")
                else:
                    try:
                        data = auth_rest_request("token?grant_type=password", {
                            "email": normalize_owner_email(email),
                            "password": password,
                        })
                        activate_auth_session(data)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Sign in failed: {exc}")
        with signup_tab:
            display_name = st.text_input("Name", key="auth_signup_name")
            signup_email = st.text_input("Email", key="auth_signup_email")
            signup_password = st.text_input("Password", type="password", key="auth_signup_password")
            role_label = st.selectbox("Account type", ["rep", "company"], format_func=lambda v: v.title(), key="auth_signup_role")
            if st.button("Create account", type="primary", use_container_width=True, key="auth_signup_btn"):
                if not (signup_email and signup_password):
                    st.error("Enter an email and password.")
                elif len(signup_password) < 8:
                    st.error("Use a password with at least 8 characters.")
                else:
                    role = public_signup_role(role_label)
                    try:
                        data = auth_rest_request("signup", {
                            "email": normalize_owner_email(signup_email),
                            "password": signup_password,
                            "data": {"role": role, "display_name": display_name.strip()},
                        })
                        if data.get("access_token") or data.get("session"):
                            activate_auth_session(data, role, display_name)
                            track_once(
                                f"signup_{current_auth_user_id()}",
                                "signup",
                                target_type="account",
                                target_id=current_auth_user_id(),
                                metadata={"role": role},
                            )
                            st.rerun()
                        else:
                            track_event("signup", target_type="account", metadata={"role": role})
                            st.success("Account created. Check your email to confirm it, then sign in.")
                    except Exception as exc:
                        st.error(f"Sign up failed: {exc}")
        with reset_tab:
            reset_email = st.text_input("Email", key="auth_reset_email")
            if st.button("Send reset email", use_container_width=True, key="auth_reset_btn"):
                if not reset_email:
                    st.error("Enter your email.")
                else:
                    try:
                        auth_rest_request("recover", {"email": normalize_owner_email(reset_email)})
                        st.success("If that email has an account, Supabase will send a reset link.")
                    except Exception as exc:
                        st.error(f"Reset request failed: {exc}")


st.set_page_config(page_title=APP_TITLE, page_icon="📍", layout="wide")

review_token_from_url = st.query_params.get("review_token", "")


def render_verified_review_page(token: str):
    st.title("Leave a verified review")
    try:
        rep, lead, err = rep_for_review_token(token)
    except Exception as exc:
        st.error(f"Couldn't verify that review link: {exc}")
        st.stop()
    if err:
        st.error(err)
        st.stop()
    st.caption(f"Your review is tied to your intro request for {rep['company']}.")
    with st.form("verified_review", clear_on_submit=True):
        rating = st.slider("Your rating", 1, 5, 5)
        name = st.text_input("Your name", value=lead.get("customer_name", "") or "")
        title = st.text_input("Review title")
        comment = st.text_area("Comment (optional)")
        sent = st.form_submit_button("Submit verified review")
    if sent:
        try:
            insert_review(rep, rating, name.strip(), title.strip(), comment.strip(), token)
            st.success("Thanks — your review was submitted for moderation.")
            st.query_params.clear()
        except Exception as exc:
            st.error(f"Couldn't save review: {exc}")


st.markdown(
    """
    <style>
      .block-container {padding-top: 1.4rem; max-width: 1400px;}
      .prospect {border:1px solid rgba(128,128,128,.25); border-radius:12px; padding:14px 16px; margin-bottom:12px;}
      .pname {font-size:1.05rem; font-weight:700; line-height:1.2;}
      .pmeta {color:#7d8a86; font-size:.85rem;}
      .badge {display:inline-block; font-size:.72rem; font-weight:600; padding:2px 8px;
              border-radius:6px; margin:2px 4px 2px 0; background:rgba(128,128,128,.15);}
      .b-hot {background:#c6432a22; color:#c6432a;} .b-warm {background:#b67a1e22; color:#b67a1e;}
      .b-cool {background:#3e7c6422; color:#3e7c64;} .b-gap {background:#c9781f22; color:#c9781f;}
      .b-verified {background:#0e5a5422; color:#0e5a54;} .b-new {background:#b67a1e22; color:#b67a1e;}
      .rep-card {border:1px solid rgba(128,128,128,.24); border-radius:8px; padding:16px; margin-bottom:14px;
                 background:rgba(255,255,255,.02);}
      .rep-head {display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:8px;}
      .rep-name {font-size:1.12rem; font-weight:750; line-height:1.15;}
      .rep-company {color:#7d8a86; font-size:.84rem; margin-top:2px;}
      .rep-headline {font-size:.95rem; font-weight:650; margin:8px 0 6px;}
      .rep-badges {margin:8px 0 10px;}
      .rep-badge-priority {background:#0e5a5422; color:#0e5a54;}
      .rep-badge-territory {background:#22577a22; color:#22577a;}
      .rep-badge-category {background:#6a4c9322; color:#6a4c93;}
      .availability-panel {border:1px solid #0e5a5444; border-radius:8px; padding:10px 12px;
                           background:#0e5a5411; margin:8px 0 12px;}
      .availability-title {font-size:.72rem; font-weight:800; letter-spacing:.07em; text-transform:uppercase; color:#0e5a54;}
      .availability-value {font-size:1.05rem; font-weight:750; margin-top:2px;}
      .rep-bio {color:#53615d; font-size:.9rem; line-height:1.45; margin:8px 0 10px;}
      .rep-grid {display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 14px; margin-top:8px;}
      .rep-field-label {color:#7d8a86; font-size:.68rem; font-weight:700; letter-spacing:.06em; text-transform:uppercase;}
      .rep-field-value {font-size:.86rem; line-height:1.35;}
      .rep-links a {font-size:.86rem; font-weight:650; text-decoration:none;}
      .deal {margin:8px 0 4px; padding:9px 12px; border-radius:9px; font-weight:600; font-size:.9rem;
             background:linear-gradient(90deg,#c9781f22,#0e5a5411); border:1px solid #c9781f44; color:inherit;}
      .deal b {color:#c9781f;}
      .matchbox {text-align:center; border:1px solid rgba(128,128,128,.25); border-radius:12px; padding:8px 4px;}
      .matchnum {font-size:1.7rem; font-weight:800; line-height:1; color:#0e5a54;}
      .matchlbl {font-size:.62rem; letter-spacing:.1em; text-transform:uppercase; color:#7d8a86;}
      .stars {color:#b67a1e; letter-spacing:1px;}
      .home-hero {padding:34px 0 18px;}
      .home-kicker {font-size:.78rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; color:#0e5a54;}
      .home-title {font-size:clamp(2.1rem, 4vw, 4.2rem); font-weight:850; line-height:1.02; margin:8px 0 10px;}
      .home-subtitle {font-size:1.05rem; color:#53615d; max-width:760px; line-height:1.5;}
      .home-path {border:1px solid rgba(128,128,128,.24); border-radius:8px; padding:18px; background:#fff; min-height:160px;}
      .home-path-title {font-size:.75rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; color:#7d8a86;}
      .home-path-action {font-size:1.45rem; font-weight:800; margin:6px 0;}
      .home-section {margin-top:28px;}
      .home-section h3 {font-size:1.15rem; margin-bottom:4px;}
      .home-muted {color:#53615d; line-height:1.45;}
      .home-mini-grid {display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px;}
      .home-mini {border:1px solid rgba(128,128,128,.22); border-radius:8px; padding:12px; background:#fff;}
      .home-mini-title {font-weight:750;}
      .home-count {font-size:1.6rem; font-weight:850; color:#0e5a54;}
      @media (max-width: 700px) {
        .rep-head {display:block;}
        .rep-grid {grid-template-columns:1fr;}
        .rep-card {padding:13px;}
        .home-mini-grid {grid-template-columns:1fr;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)

if review_token_from_url:
    render_verified_review_page(review_token_from_url)
    st.stop()

# ---- Audience switch (who's using the app right now) ----
render_auth_panel()
with st.sidebar:
    st.divider()
    audience_options = [
        "🏠 Home",
        "🧭 Sales rep — find customers",
        "🛍️ Company — find reps",
        "🏢 Rep — find companies",
        "📊 Territory Intelligence",
        "⭐ Saved Reps",
        "⭐ Saved Opportunities",
    ]
    if LIVE_WRITES_ON:
        audience_options.append("🛡️ Admin Dashboard")
    audience = st.radio(
        "I am a…",
        audience_options,
        label_visibility="collapsed",
        key="audience_mode",
)
home_mode = audience.startswith("🏠")
rep_mode = audience.startswith("🧭")
company_directory_mode = audience.startswith("🏢")
territory_intelligence_mode = audience.startswith("📊")
saved_reps_mode = audience.startswith("⭐ Saved Reps")
saved_opportunities_mode = audience.startswith("⭐ Saved Opportunities")
admin_dashboard_mode = audience.startswith("🛡️")
public_seo_mode = bool(st.query_params.get("territory", "") or st.query_params.get("category", ""))
if st.query_params.get("opportunity", ""):
    public_seo_mode = False
    home_mode = False
    rep_mode = False
    company_directory_mode = True
    territory_intelligence_mode = False
    saved_reps_mode = False
    saved_opportunities_mode = False
    admin_dashboard_mode = False
elif st.query_params.get("company", ""):
    public_seo_mode = False
    home_mode = False
    rep_mode = False
    company_directory_mode = True
    territory_intelligence_mode = False
    saved_reps_mode = False
    saved_opportunities_mode = False
    admin_dashboard_mode = False
elif st.query_params.get("rep", ""):
    public_seo_mode = False
    home_mode = False
    rep_mode = False
    company_directory_mode = False
    territory_intelligence_mode = False
    saved_reps_mode = False
    saved_opportunities_mode = False
    admin_dashboard_mode = False
elif public_seo_mode:
    home_mode = False
    rep_mode = False
    company_directory_mode = False
    territory_intelligence_mode = False
    saved_reps_mode = False
    saved_opportunities_mode = False
    admin_dashboard_mode = False

if home_mode:
    st.title("Territory Prospector")
    st.caption("Find the right sales rep for any territory.")
elif rep_mode:
    st.title("📍 Territory Prospector")
    st.caption("Find new business customers by area & category — live data from OpenStreetMap, no API key required.")
elif company_directory_mode:
    st.title("🏢 Company Directory")
    st.caption("Browse companies looking for sales representation and new product-line coverage.")
elif territory_intelligence_mode:
    st.title("📊 Territory Intelligence")
    st.caption("Marketplace indicators from listed reps, company profiles, and active opportunities.")
elif saved_reps_mode:
    st.title("⭐ Saved Reps")
    st.caption("Your anonymous session shortlist. These saves are structured to migrate to accounts later.")
elif saved_opportunities_mode:
    st.title("⭐ Saved Opportunities")
    st.caption("Saved companies and opportunities for this session.")
elif admin_dashboard_mode:
    st.title("🛡️ Admin Dashboard")
    st.caption("Review, moderate, and manage marketplace activity.")
elif public_seo_mode:
    st.title("Territory Prospector")
    st.caption("Public marketplace page backed by active listings.")
else:
    st.title("🛍️ Find a Rep · Best Deals")
    st.caption("Tell us what you need and where — matched reps compete on their offer, rating, and response time.")

# ---- Sidebar controls (per audience) ----
product_profile = next(iter(PRODUCT_PROFILES))
metro = next(iter(METROS))
custom = ""
cats = []
cap = 200
go = False
min_score = 0
only_no_web = False
indie_only = False
heat_filter = []
sort_by = "Lead score"
with st.sidebar:
    st.divider()
    if rep_mode:
        st.header("Search a territory")
        product_profile = st.selectbox(
            "What do you sell?",
            list(PRODUCT_PROFILES.keys()),
            index=0,
            help="Changes lead-score weights and the recommended sales approach.",
        )
        metro = st.selectbox("Metro area", list(METROS.keys()), index=0)
        custom = st.text_input("…or type any city / area", placeholder="e.g. Boise, ID", help="Uses OpenStreetMap geocoding.")
        cats = st.multiselect(
            "Categories", list(CATEGORIES.keys()),
            default=["Restaurant & Café", "Fitness & Gym", "Beauty & Spa"],
        )
        cap = st.slider("Max results", 50, 400, 200, step=50, help="Higher = more coverage but slower queries.")
        go = st.button("🔍 Search territory", type="primary", use_container_width=True)
        st.divider()
        st.subheader("Refine results")
        min_score = st.slider("Min lead score", 0, 100, 0, step=5)
        only_no_web = st.toggle("Only businesses without a website", value=False)
        indie_only = st.toggle("Independents only (hide chains)", value=False)
        heat_filter = st.multiselect("Lead heat", ["Hot", "Warm", "Cool"], default=[])
        sort_by = st.selectbox("Sort by", ["Lead score", "Name (A–Z)", "Category"])
        st.divider()
        st.subheader("Pipeline sync")
        pipeline_owner_email = st.text_input(
            "Rep email",
            value=st.session_state.get("pipeline_owner_email", ""),
            placeholder="you@company.com",
            help="Used to load/save your private Supabase pipeline when service_role is configured.",
        )
        st.session_state["pipeline_owner_email"] = pipeline_owner_email
        pipeline_access_code = st.text_input(
            "Pipeline code",
            value=st.session_state.get("pipeline_access_code", ""),
            type="password",
            help="Use the same private code every time you load or save this pipeline.",
        )
        st.session_state["pipeline_access_code"] = pipeline_access_code
        auth_token = st.text_input(
            "Supabase auth token",
            value=st.session_state.get("supabase_auth_token", ""),
            type="password",
            help="Optional. Paste a Supabase Auth access token to bind pipeline rows to your user account.",
        )
        st.session_state["supabase_auth_token"] = auth_token
    elif territory_intelligence_mode:
        st.header("Territory Intelligence")
        ti_metro = st.selectbox("Metro", [""] + list(METROS.keys()), format_func=lambda v: v or "Any metro", key="ti_metro")
        ti_state = st.selectbox("State", [""] + US_STATES, format_func=lambda v: v or "Any state", key="ti_state")
        ti_category = st.selectbox("Category", [""] + list(CATEGORIES.keys()), format_func=lambda v: v or "Any category", key="ti_category")
        ti_industry = st.selectbox("Industry", [""] + INDUSTRY_OPTIONS, format_func=lambda v: v or "Any industry", key="ti_industry")
        if st.button("Clear dashboard filters", use_container_width=True):
            for key in ["ti_metro", "ti_state", "ti_category", "ti_industry"]:
                st.session_state.pop(key, None)
            st.rerun()
    elif saved_reps_mode or saved_opportunities_mode:
        st.header("Shortlists")
        st.caption("Collections: Saved, Contact Later, Strong Candidates")
    elif home_mode:
        st.header("Marketplace")
        st.caption("Choose a path on the homepage to start searching.")
    elif public_seo_mode:
        st.header("Public page")
        st.caption("Use the share URL on the page to revisit it.")
    elif not company_directory_mode:
        defaults = query_param_defaults()
        entitlements = current_entitlements()
        advanced_search_enabled = can_use_advanced_search(entitlements)
        st.header("What do you need?")
        if st.button("Clear filters", use_container_width=True):
            st.query_params.clear()
            for key in [
                "rep_keyword", "rep_categories", "rep_industries", "rep_metros", "rep_states",
                "rep_zip", "rep_customer_types", "rep_min_years", "rep_verified_only",
                "rep_open_only", "rep_availability", "rep_compensation_types", "rep_min_rating",
                "rep_territory_radius", "rep_sort",
            ]:
                st.session_state.pop(key, None)
            st.rerun()
        cust_keyword = st.text_input("Keyword", value=defaults["keyword"], key="rep_keyword",
                                     placeholder="Rep, company, line, or specialty")
        cust_categories = st.multiselect("Categories", list(CATEGORIES.keys()),
                                         default=defaults["categories"], key="rep_categories")
        cust_metros = st.multiselect("Metros", list(METROS.keys()),
                                     default=defaults["metros"], key="rep_metros")
        if advanced_search_enabled:
            cust_industries = st.multiselect("Industries", INDUSTRY_OPTIONS,
                                             default=defaults["industries"], key="rep_industries")
            cust_states = st.multiselect("States", US_STATES, default=defaults["states"], key="rep_states")
            cust_zip = st.text_input("ZIP code", value=defaults["zip_code"], key="rep_zip",
                                     placeholder="e.g. 95117")
            cust_customer_types = st.multiselect("Customer type", CUSTOMER_TYPE_OPTIONS,
                                                 default=defaults["customer_types"], key="rep_customer_types")
            cust_min_years = st.slider("Minimum experience", 0, 40, min(defaults["min_years"], 40),
                                       step=1, key="rep_min_years")
            cust_verified_only = st.toggle("Verified only", value=defaults["verified_only"], key="rep_verified_only")
            availability_default = defaults["availability"] or (["Open", "Selectively Open"] if defaults["open_only"] else [])
            cust_availability = st.multiselect("Availability", AVAILABILITY_STATUS_OPTIONS,
                                               default=availability_default, key="rep_availability")
            cust_compensation_types = st.multiselect("Compensation type", COMPENSATION_TYPE_OPTIONS,
                                                     default=defaults["compensation_types"], key="rep_compensation_types")
            cust_min_rating = st.slider("Minimum rating", 0.0, 5.0, min(defaults["min_rating"], 5.0),
                                        step=0.5, key="rep_min_rating")
            cust_territory_radius = st.slider("Minimum territory radius", 0, 150,
                                              min(defaults["territory_radius"], 150),
                                              step=5, key="rep_territory_radius",
                                              help="Use 0 for any radius.")
            sort_default = defaults["sort"] if defaults["sort"] in REP_SORT_OPTIONS else "Best Match"
            cust_sort = st.selectbox("Sort by", REP_SORT_OPTIONS, index=REP_SORT_OPTIONS.index(sort_default),
                                     key="rep_sort")
        else:
            entitlement_notice("Advanced search")
            cust_industries = []
            cust_states = []
            cust_zip = ""
            cust_customer_types = []
            cust_min_years = 0
            cust_verified_only = False
            cust_availability = []
            cust_compensation_types = []
            cust_min_rating = 0.0
            cust_territory_radius = 0
            cust_sort = "Best Match"
        cust_filters = {
            "keyword": cust_keyword.strip(),
            "categories": cust_categories,
            "industries": cust_industries,
            "metros": cust_metros,
            "states": cust_states,
            "zip_code": cust_zip.strip(),
            "customer_types": cust_customer_types,
            "min_years": cust_min_years,
            "verified_only": cust_verified_only,
            "open_only": False,
            "availability": cust_availability,
            "compensation_types": cust_compensation_types,
            "min_rating": cust_min_rating,
            "territory_radius": cust_territory_radius,
            "sort": cust_sort,
        }
        sync_rep_search_query(cust_filters)
    else:
        st.header("Company directory")
        company_keyword = st.text_input("Keyword", key="company_keyword", placeholder="Company, product, industry, or territory")
        company_category = st.multiselect("Categories", list(CATEGORIES.keys()), key="company_categories")
        company_metro = st.multiselect("Metros needed", list(METROS.keys()), key="company_metros")
        company_state = st.multiselect("States needed", US_STATES, key="company_states")
        st.divider()
        st.header("Opportunity filters")
        opp_territory = st.multiselect("Territory", list(METROS.keys()) + US_STATES, key="opp_territory")
        opp_category = st.multiselect("Opportunity category", list(CATEGORIES.keys()), key="opp_categories")
        opp_industry = st.multiselect("Opportunity industry", INDUSTRY_OPTIONS, key="opp_industries")
        opp_compensation = st.multiselect("Compensation", COMPENSATION_TYPE_OPTIONS, key="opp_compensation")
        opp_exclusive = st.toggle("Exclusive territory only", value=False, key="opp_exclusive")
        opp_experience = st.slider("Max required experience", 0, 20, 20, key="opp_experience")
        try:
            compare_reps = all_reps()
        except Exception:
            compare_reps = []
        rep_options = [
            "No rep selected",
            *[
                f"{r.get('name', 'Rep')} · {r.get('company') or 'Independent rep'}"
                for r in compare_reps[:100]
            ],
        ]
        compare_choice = st.selectbox("Potential match as", rep_options, key="opp_compare_rep")
        st.session_state["opp_compare_rep_id"] = ""
        if compare_choice != "No rep selected":
            idx = rep_options.index(compare_choice) - 1
            st.session_state["opp_compare_rep_id"] = compare_reps[idx].get("id", "")

# --------------------------------------------------------------------------- #
# Customer mode: find a rep + best deals
# --------------------------------------------------------------------------- #
def rep_profile_ref(rep: dict) -> str:
    return str(rep.get("profile_slug") or rep.get("id") or slugify(rep.get("company") or rep.get("name") or "rep"))


def compact_values(values, limit: int = 3, fallback: str = "Available on request") -> tuple[str, int]:
    cleaned = clean_list(values)
    if not cleaned:
        return fallback, 0
    shown = ", ".join(cleaned[:limit])
    more = max(0, len(cleaned) - limit)
    return shown, more


def compact_text(values, limit: int = 3, fallback: str = "Available on request") -> str:
    shown, more = compact_values(values, limit, fallback)
    return f"{shown} +{more} more" if more else shown


def share_url(**params) -> str:
    clean = {k: str(v) for k, v in params.items() if v not in (None, "")}
    query = urlencode(clean)
    base = _app_base_url()
    return f"{base}?{query}" if base else f"?{query}"


def render_shareable_url(label: str, **params):
    st.caption(label)
    st.code(share_url(**params), language=None)


def slug_match(value: str, ref: str) -> bool:
    return slugify(value or "") == (ref or "").strip().lower()


def title_description(title: str, description: str):
    st.header(title)
    st.caption(description)


def active_public_reps() -> list[dict]:
    return [
        rep for rep in all_reps()
        if rep.get("active", True) is not False
        and (rep.get("profile_status") or "active") == "active"
        and not rep.get("is_sample")
    ]


def active_public_companies() -> list[dict]:
    return [c for c in all_companies() if (c.get("profile_status") or "active") == "active"]


def active_public_opportunities() -> list[dict]:
    return [o for o in all_opportunities() if o.get("active", True)]


def public_list_text(values) -> str:
    cleaned = clean_list(values)
    return ", ".join(cleaned) if cleaned else ""


def commission_text(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):g}%"
    except (TypeError, ValueError):
        return str(value)


def rating_text(rating: float, rcount: int) -> str:
    return "Unrated" if rcount == 0 else f"{rating:.1f} ({rcount} reviews)"


def company_status(rep: dict) -> str:
    company = (rep.get("company") or "").strip()
    return company if company else "Independent rep"


def rep_links_html(rep: dict) -> str:
    links = []
    website = safe_public_url(rep.get("website") or "")
    linkedin = safe_public_url(rep.get("linkedin_url") or "")
    if website:
        links.append(f'<a href="{h(website)}" target="_blank" rel="noopener noreferrer">Website</a>')
    if linkedin:
        links.append(f'<a href="{h(linkedin)}" target="_blank" rel="noopener noreferrer">LinkedIn</a>')
    return " · ".join(links)


def render_rep_contact_sections(rep: dict, rcount: int, recent: list, key_prefix: str):
    with st.expander("Request an intro / claim this deal"):
        st.caption(f"Send your details to {rep.get('name', 'this rep')} at {company_status(rep)}. They'll reach out directly.")
        with st.form(f"lead_{key_prefix}_{rep['id']}", clear_on_submit=True):
            ln = st.text_input("Your name", key=f"ln_{key_prefix}_{rep['id']}")
            lc1, lc2 = st.columns(2)
            le = lc1.text_input("Your email", key=f"le_{key_prefix}_{rep['id']}")
            lp = lc2.text_input("Phone (optional)", key=f"lp_{key_prefix}_{rep['id']}")
            lm = st.text_area("What do you need?", key=f"lm_{key_prefix}_{rep['id']}",
                              placeholder="One line on what you're looking for...")
            sent = st.form_submit_button("Send my request")
        if sent:
            if not ln or not (le or lp):
                st.error("Add your name and an email or phone so the rep can reply.")
            else:
                submit_lead(rep, ln.strip(), le.strip(), lp.strip(), lm.strip())

    review_label = f"Verified reviews ({rcount}) · leave a review"
    with st.expander(review_label):
        if recent:
            for rv in recent:
                rn_ = int(rv.get("rating", 0) or 0)
                badge = " · Verified relationship" if rv.get("verified_relationship") or rv.get("verified") else ""
                title = (rv.get("title") or "").strip()
                body = (rv.get("review") or rv.get("comment") or "").strip()
                st.markdown(
                    f'<span class="stars">{"★" * rn_}{"☆" * (5 - rn_)}</span> '
                    f'**{h(rv.get("reviewer") or rv.get("customer_name") or "Anonymous")}**{h(badge)}'
                    + (f' — **{h(title)}**' if title else '')
                    + (f' — {h(body)}' if body else ''),
                    unsafe_allow_html=True,
                )
        elif rcount:
            st.caption("No written reviews yet.")
        with st.form(f"rev_{key_prefix}_{rep['id']}", clear_on_submit=True):
            rr = st.slider("Your rating", 1, 5, 5, key=f"rr_{key_prefix}_{rep['id']}")
            rn = st.text_input("Your name", key=f"rn_{key_prefix}_{rep['id']}")
            rt_title = st.text_input("Review title", key=f"rtitle_{key_prefix}_{rep['id']}")
            rc = st.text_area("Comment (optional)", key=f"rc_{key_prefix}_{rep['id']}")
            rt = ""
            if LIVE_WRITES_ON:
                rt = st.text_input(
                    "Verified review token",
                    value=review_token_from_url,
                    key=f"rt_{key_prefix}_{rep['id']}",
                    help="Customers receive a one-time token after requesting an intro.",
                )
            rsent = st.form_submit_button("Submit review")
        if rsent:
            try:
                if LIVE_WRITES_ON and not rt.strip():
                    st.error("Use the verified review link from your intro request email.")
                    st.stop()
                insert_review(rep, rr, rn.strip(), rt_title.strip(), rc.strip(), rt.strip())
                st.success("Thanks — your review was submitted for moderation.")
                st.rerun()
            except Exception as exc:
                st.error(f"Couldn't save review: {exc}")


def rep_card_summary(rep: dict, rating: float, rcount: int, distance: float | None = None) -> dict:
    categories = rep.get("industries") or rep.get("categories") or []
    territories = rep.get("states") or rep.get("zip_codes") or rep.get("metros") or []
    territory_text = compact_text(territories, 3, "Territory available on request")
    if distance is not None:
        territory_text = f"{distance:.1f} mi away · {territory_text}"
    return {
        "name": rep.get("name") or "Representative",
        "company": company_status(rep),
        "headline": rep.get("headline") or rep.get("blurb") or "Sales representative available for new opportunities.",
        "categories": compact_text(categories, 3, "Categories available on request"),
        "territory": territory_text,
        "years": int(rep.get("years_experience") or 0),
        "rating": rating_text(rating, rcount),
        "compensation": format_compensation(rep),
        "lines": compact_text(rep.get("existing_lines"), 3, "No public line card yet"),
        "response": rep.get("response") or "Response time varies",
        "bio": rep.get("blurb") or rep.get("headline") or "No bio added yet.",
        "available": format_availability(rep),
        "links": rep_links_html(rep),
    }


def rep_keyword_text(rep: dict) -> str:
    fields = [
        rep.get("name"), rep.get("company"), rep.get("headline"), rep.get("blurb"),
        rep.get("deal"), rep.get("website"), rep.get("linkedin_url"), rep.get("service_area"),
        rep.get("response"),
    ]
    for key in ["categories", "industries", "metros", "states", "zip_codes", "customer_types",
                "compensation_types", "existing_lines", "preferred_categories",
                "preferred_company_types", "preferred_compensation"]:
        fields.extend(clean_list(rep.get(key)))
    return " ".join(str(v) for v in fields if v).lower()


def values_overlap(rep_values, selected_values: list[str]) -> bool:
    selected = {v.lower() for v in clean_list(selected_values)}
    if not selected:
        return True
    existing = {v.lower() for v in clean_list(rep_values)}
    return bool(existing & selected)


def rep_has_state(rep: dict, states: list[str]) -> bool:
    selected = {s.upper() for s in clean_list(states)}
    if not selected:
        return True
    explicit = {s.upper() for s in clean_list(rep.get("states"))}
    if explicit & selected:
        return True
    metro_state_tokens = {
        metro.rsplit(",", 1)[-1].strip().upper()
        for metro in clean_list(rep.get("metros"))
        if "," in metro
    }
    return bool(metro_state_tokens & selected)


def rep_has_zip(rep: dict, zip_code: str) -> bool:
    zip_code = (zip_code or "").strip()
    if not zip_code:
        return True
    zips = {z.strip() for z in clean_list(rep.get("zip_codes"))}
    if zip_code in zips:
        return True
    return zip_code in str(rep.get("service_area") or "")


def rep_matches_filters(rep: dict, filters: dict) -> bool:
    keyword = (filters.get("keyword") or "").strip().lower()
    if keyword:
        haystack = rep_keyword_text(rep)
        terms = [term for term in re.split(r"\s+", keyword) if term]
        if not all(term in haystack for term in terms):
            return False
    if not values_overlap(rep.get("categories"), filters.get("categories", [])):
        return False
    if not values_overlap(rep.get("industries") or rep.get("categories"), filters.get("industries", [])):
        return False
    if not values_overlap(rep.get("metros"), filters.get("metros", [])):
        return False
    if not rep_has_state(rep, filters.get("states", [])):
        return False
    if not rep_has_zip(rep, filters.get("zip_code", "")):
        return False
    if not values_overlap(rep.get("customer_types"), filters.get("customer_types", [])):
        return False
    if int(rep.get("years_experience") or 0) < int(filters.get("min_years") or 0):
        return False
    if filters.get("verified_only") and not rep.get("verified"):
        return False
    if filters.get("open_only") and rep.get("open_to_new_lines") is False:
        return False
    if filters.get("availability"):
        allowed = {AVAILABILITY_STATUS_VALUES[v] for v in filters.get("availability", [])}
        if (rep.get("availability_status") or availability_status(rep).replace(" ", "_")) not in allowed:
            return False
    if not values_overlap(rep.get("compensation_types"), filters.get("compensation_types", [])):
        return False
    if int(rep.get("territory_radius") or rep.get("service_radius_miles") or 0) < int(filters.get("territory_radius") or 0):
        return False
    return True


def active_filter_labels(filters: dict) -> list[str]:
    labels = []
    if filters.get("keyword"):
        labels.append(f"keyword: {filters['keyword']}")
    for key, label in [
        ("categories", "category"),
        ("industries", "industry"),
        ("metros", "metro"),
        ("states", "state"),
        ("customer_types", "customer type"),
        ("availability", "availability"),
        ("compensation_types", "compensation"),
    ]:
        values = clean_list(filters.get(key))
        if values:
            labels.append(f"{label}: {', '.join(values[:2])}" + (f" +{len(values) - 2}" if len(values) > 2 else ""))
    if filters.get("zip_code"):
        labels.append(f"ZIP: {filters['zip_code']}")
    if filters.get("min_years"):
        labels.append(f"{filters['min_years']}+ years")
    if filters.get("verified_only"):
        labels.append("verified only")
    if filters.get("open_only"):
        labels.append("open to new lines")
    if filters.get("min_rating"):
        labels.append(f"{filters['min_rating']:g}+ rating")
    if filters.get("territory_radius"):
        labels.append(f"{filters['territory_radius']}+ mi radius")
    return labels


def sort_rep_matches(matches: list[tuple], sort_name: str) -> list[tuple]:
    if sort_name == "Highest Rated":
        return sorted(matches, key=lambda x: (x[2], x[3], x[1]), reverse=True)
    if sort_name == "Most Experienced":
        return sorted(matches, key=lambda x: (int(x[0].get("years_experience") or 0), x[1]), reverse=True)
    if sort_name == "Fastest Response":
        return sorted(matches, key=lambda x: (float(x[0].get("response_time_hours") or RESPONSE_HOURS.get(x[0].get("response"), 24)), -x[1]))
    if sort_name == "Newest":
        return sorted(matches, key=lambda x: str(x[0].get("created_at") or ""), reverse=True)
    return sorted(matches, key=lambda x: (x[7].score if x[7] and x[7].enough_context else x[1]), reverse=True)


def empty_search_suggestions(filters: dict) -> list[str]:
    suggestions = ["Clear one or two filters and search again."]
    if filters.get("min_rating"):
        suggestions.append("Lower the minimum rating to include newer reps with fewer reviews.")
    if filters.get("verified_only"):
        suggestions.append("Turn off verified-only to see reps who have not been reviewed by admin yet.")
    if filters.get("open_only"):
        suggestions.append("Include selective reps if the line is a strong fit.")
    if filters.get("availability"):
        suggestions.append("Broaden availability to include selectively open reps.")
    if filters.get("states") or filters.get("zip_code") or filters.get("metros"):
        suggestions.append("Broaden the geography by removing ZIP, state, or metro filters.")
    if filters.get("compensation_types"):
        suggestions.append("Remove compensation type if you can discuss terms directly.")
    return suggestions[:4]


def render_match_explanation(match: RepMatchResult, key: str):
    with st.expander("Why this match"):
        st.caption(match.confidence_label)
        details = [
            ("Territory overlap", match.territory_overlap),
            ("Category overlap", match.category_overlap),
            ("Compensation", match.compensation_compatibility),
            ("Availability", match.availability),
            ("Product-line conflict", match.product_line_conflict),
        ]
        for label, value in details:
            if value:
                st.write(f"**{label}:** {value}")
        if match.product_line_conflict_explanation:
            st.write(f"**Conflict rationale:** {match.product_line_conflict_explanation}")
        if match.public_conflict_details:
            st.write("**Public conflict details:** " + ", ".join(match.public_conflict_details[:3]))
        if match.explanations:
            st.caption("Short explanation")
            for reason in match.explanations[:6]:
                st.write(f"- {reason}")
        conflicts = match.possible_conflicts or match.confidence_notes
        if conflicts:
            st.caption("Possible conflicts")
            for note in conflicts[:4]:
                st.caption(f"- {note}")


def rep_card(rep: dict, score: int, rating: float, rcount: int, real: bool, recent: list,
             distance: float | None = None, match: RepMatchResult | None = None):
    summary = rep_card_summary(rep, rating, rcount, distance)
    stars = "★" * round(rating) + "☆" * (5 - round(rating))
    verified_badge = '<span class="badge b-verified">Verified</span>' if rep.get("verified") else '<span class="badge">Unverified</span>'
    featured_badge = '<span class="badge b-verified">Featured</span>' if rep.get("featured") else ""
    sample_badge = '<span class="badge">Sample listing</span>' if rep.get("is_sample") else ""
    years_text = f"{summary['years']} years" if summary["years"] else "Experience not listed"
    with st.container():
        st.markdown('<div class="rep-card">', unsafe_allow_html=True)
        c1, c2 = st.columns([5, 1.45])
        with c1:
            st.markdown(
                f'<div class="rep-name">{h(summary["name"])}</div>'
                f'<div class="rep-company">{h(summary["company"])}</div>'
                f'<div class="rep-headline">{h(summary["headline"])}</div>'
                f'<div class="rep-badges">'
                f'<span class="badge rep-badge-territory">{h(summary["territory"])}</span>'
                f'<span class="badge rep-badge-category">{h(summary["categories"])}</span>'
                f'<span class="badge rep-badge-priority">{h(summary["available"])}</span>'
                f'{verified_badge}{featured_badge}{sample_badge}'
                f'</div>'
                f'<div class="rep-bio">{h(summary["bio"])}</div>'
                f'<div class="rep-grid">'
                f'<div><div class="rep-field-label">Experience</div><div class="rep-field-value">{h(years_text)}</div></div>'
                f'<div><div class="rep-field-label">Rating</div><div class="rep-field-value"><span class="stars">{stars}</span> {h(summary["rating"])}</div></div>'
                f'<div><div class="rep-field-label">Compensation</div><div class="rep-field-value">{h(summary["compensation"])}</div></div>'
                f'<div><div class="rep-field-label">Existing Lines</div><div class="rep-field-value">{h(summary["lines"])}</div></div>'
                f'<div><div class="rep-field-label">Response Speed</div><div class="rep-field-value">{h(summary["response"])}</div></div>'
                f'<div><div class="rep-field-label">Primary Categories</div><div class="rep-field-value">{h(summary["categories"])}</div></div>'
                f'</div>'
                + (f'<div class="rep-links">{summary["links"]}</div>' if summary["links"] else ""),
                unsafe_allow_html=True,
            )
            render_rep_contact_sections(rep, rcount, recent, "card")
        with c2:
            if match and match.enough_context:
                st.markdown(
                    f'<div class="matchbox"><div class="matchnum">{match.score}%</div>'
                    f'<div class="matchlbl">{h(match.confidence_label)}</div></div>',
                    unsafe_allow_html=True,
                )
                render_match_explanation(match, f"card_{rep['id']}")
            render_save_controls("rep", rep.get("id"), f"rep_card_{rep['id']}", "Strong Candidates")
            if st.button("View Profile", key=f"view_profile_{rep['id']}", use_container_width=True):
                st.query_params["rep"] = rep_profile_ref(rep)
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


def find_rep_for_profile(ref: str, roster: list[dict]) -> dict | None:
    if not ref:
        return None
    for rep in roster:
        if ref in {str(rep.get("id")), str(rep.get("profile_slug") or "")}:
            return rep
    return None


def render_rep_profile_page(ref: str):
    roster = [rep for rep in all_reps() if rep.get("active", True) is not False and (rep.get("profile_status") or "active") == "active"]
    rep = find_rep_for_profile(ref, roster)
    if not rep:
        st.warning("That rep profile is not available.")
        if st.button("Back to marketplace"):
            if "rep" in st.query_params:
                del st.query_params["rep"]
            st.rerun()
        return

    summary = reviews_summary(all_reviews())
    rating, rcount, real = effective_rating(rep, summary)
    recent = summary.get(normalize_rep_review_id(rep.get("id", "")), {}).get("recent", [])
    score = rep_score(rep, rating)
    card = rep_card_summary(rep, rating, rcount)
    profile_context = query_param_defaults()
    match = score_rep_match(rep, profile_context, rating)
    stars = "★" * round(rating) + "☆" * (5 - round(rating))
    track_once(
        f"rep_profile_view_{rep.get('id')}",
        "rep_profile_view",
        target_type="rep",
        target_id=str(rep.get("id") or ""),
        category=clean_list(rep.get("categories"))[0] if clean_list(rep.get("categories")) else "",
        metro=clean_list(rep.get("metros"))[0] if clean_list(rep.get("metros")) else "",
    )

    if st.button("Back to marketplace"):
        if "rep" in st.query_params:
            del st.query_params["rep"]
        st.rerun()

    title_description(
        f"{card['name']} · {card['company']}",
        f"{card['headline']} Territory: {card['territory']}. Categories: {card['categories']}.",
    )
    render_shareable_url("Share this rep profile", rep=rep_profile_ref(rep))

    top1, top2 = st.columns([4, 1.2])
    with top1:
        st.markdown(
            f'<div class="rep-card">'
            f'<div class="rep-name">{h(card["name"])}</div>'
            f'<div class="rep-company">{h(card["company"])}</div>'
            f'<div class="rep-headline">{h(card["headline"])}</div>'
            f'<div class="availability-panel"><div class="availability-title">Availability</div>'
            f'<div class="availability-value">{h(card["available"])}</div></div>'
            f'<div class="rep-badges">'
            f'<span class="badge rep-badge-territory">{h(card["territory"])}</span>'
            f'<span class="badge rep-badge-category">{h(card["categories"])}</span>'
            f'<span class="badge rep-badge-priority">{h(card["available"])}</span>'
            + ('<span class="badge b-verified">Verified</span>' if rep.get("verified") else '<span class="badge">Unverified</span>')
            + ('<span class="badge b-verified">Featured</span>' if rep.get("featured") else '')
            + '</div>'
            f'<div class="rep-bio">{h(card["bio"])}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with top2:
        if match.enough_context:
            st.markdown(
                f'<div class="matchbox"><div class="matchnum">{match.score}%</div>'
                f'<div class="matchlbl">{h(match.confidence_label)}</div></div>',
                unsafe_allow_html=True,
            )
            render_match_explanation(match, f"profile_{rep['id']}")
        render_save_controls("rep", rep.get("id"), f"rep_profile_{rep['id']}", "Strong Candidates")

    full_profile_enabled = can_view_full_profile(current_entitlements())
    if not full_profile_enabled:
        entitlement_notice("Full representative profiles")
        info1, info2, info3 = st.columns(3)
        info1.metric("Rating", card["rating"])
        info2.metric("Territory", compact_text(rep.get("metros") or rep.get("states"), 2, "Listed on request"))
        info3.metric("Availability", card["available"])
        return

    info1, info2, info3 = st.columns(3)
    info1.metric("Rating", card["rating"])
    info2.metric("Experience", f"{card['years']} yrs" if card["years"] else "Not listed")
    info3.metric("Response", card["response"])

    detail1, detail2 = st.columns(2)
    with detail1:
        st.subheader("Territory")
        st.write(format_territories(rep))
        st.subheader("Categories")
        st.write(format_industries(rep))
        st.subheader("Compensation")
        st.write(card["compensation"])
    with detail2:
        st.subheader("Availability")
        st.write(card["available"])
        preferred_categories = public_list_text(rep.get("preferred_categories"))
        preferred_company_types = public_list_text(rep.get("preferred_company_types"))
        preferred_compensation = public_list_text(rep.get("preferred_compensation"))
        minimum_commission = commission_text(rep.get("minimum_commission"))
        notes_for_companies = (rep.get("notes_for_companies") or "").strip()
        if any([preferred_categories, preferred_company_types, preferred_compensation, minimum_commission, notes_for_companies]):
            st.subheader("Opportunity preferences")
            if preferred_categories:
                st.write(f"Preferred categories: {preferred_categories}")
            if preferred_company_types:
                st.write(f"Preferred company types: {preferred_company_types}")
            if preferred_compensation:
                st.write(f"Preferred compensation: {preferred_compensation}")
            if minimum_commission:
                st.write(f"Minimum commission: {minimum_commission}")
            if notes_for_companies:
                st.write(notes_for_companies)
        st.subheader("Existing product lines")
        st.write(card["lines"])
        if card["links"]:
            st.markdown(f'<div class="rep-links">{card["links"]}</div>', unsafe_allow_html=True)

    st.divider()
    render_recommended_opportunities(rep, rating)

    st.divider()
    st.markdown(f'<span class="stars">{stars}</span> {h(card["rating"])}', unsafe_allow_html=True)
    render_rep_contact_sections(rep, rcount, recent, "profile")
    render_connection_request_form(rep)
    render_profile_claim_form(rep)


def render_profile_claim_form(rep: dict):
    if rep.get("claimed") is True:
        return
    st.divider()
    with st.expander("Is this you? Claim this profile."):
        st.caption(
            "Submitting a claim creates a review request for the marketplace admin. "
            "It does not grant profile access or reveal private listing data."
        )
        if not is_claimable_rep(rep):
            st.info("Claim requests are available for live database profiles only.")
            return
        if not LIVE_WRITES_ON:
            st.info("Claim requests need live Supabase plus `[supabase].service_key` configured.")
            return
        with st.form(f"profile_claim_{rep.get('id')}"):
            claimant_name = st.text_input("Your name", key=f"claim_name_{rep.get('id')}")
            claimant_email = st.text_input("Business email", key=f"claim_email_{rep.get('id')}")
            message = st.text_area("Anything the admin should verify?", key=f"claim_message_{rep.get('id')}")
            submitted = st.form_submit_button("Submit claim request")
        if submitted:
            try:
                insert_profile_claim_db(rep, claimant_email, claimant_name, message)
                st.success("Claim request submitted for admin review.")
                if not EMAIL_ON:
                    st.caption("Email verification is prepared in the database, but no mail provider is configured yet.")
            except Exception as exc:
                st.error(f"Couldn't submit claim request: {exc}")


def entity_id_key(value: str) -> str:
    return str(value or "").replace("db-", "").replace("co-", "").replace("opp-", "")


def connection_status_badge(status: str) -> str:
    status = normalize_connection_status(status)
    labels = {
        "pending": "Pending",
        "accepted": "Accepted",
        "declined": "Declined",
        "withdrawn": "Withdrawn",
    }
    return labels.get(status, "Pending")


def render_connection_request_form(rep: dict):
    st.divider()
    with st.expander("Company connection request"):
        if not can_contact_rep(current_entitlements()):
            entitlement_notice("Connection requests")
            return
        st.caption("Express interest without exposing private contact details. Contact information appears only after the rep accepts.")
        companies = all_companies()
        if not companies:
            st.info("Create a company profile first, then send connection requests.")
            return
        if SUPABASE_ON and not SUPABASE_SERVICE_KEY:
            st.info("Live connection requests need `[supabase].service_key` configured.")
            return
        company_options = {f"{c.get('name')} ({c.get('id')})": c for c in companies}
        with st.form(f"connect_{rep.get('id')}"):
            company_label = st.selectbox("Company profile", list(company_options.keys()), key=f"connect_company_{rep.get('id')}")
            company = company_options[company_label]
            company_opps = all_opportunities(company.get("id"))
            opp_options = {"No specific opportunity": None}
            opp_options.update({f"{o.get('title')} ({o.get('id')})": o.get("id") for o in company_opps})
            opp_label = st.selectbox("Related opportunity", list(opp_options.keys()), key=f"connect_opp_{rep.get('id')}")
            message = st.text_area("Message", key=f"connect_message_{rep.get('id')}", placeholder="Briefly explain the line, territory, and next step.")
            sent = st.form_submit_button("Send connection request")
        if sent:
            try:
                connection, created = insert_connection_request(company, rep, opp_options[opp_label], message)
                if created:
                    st.success("Connection request sent. The rep can accept or decline it.")
                else:
                    st.info(f"A request already exists with status: {connection_status_badge(connection.get('status'))}.")
            except Exception as exc:
                st.error(f"Couldn't send connection request: {exc}")


def render_company_contact_after_accept(company: dict):
    st.caption("Accepted contact")
    contact_name = company.get("contact_name") or company.get("name") or "Company contact"
    contact_email = company.get("contact_email") or "Contact email not listed"
    website = safe_public_url(company.get("website") or "")
    st.write(f"{contact_name} · {contact_email}")
    if website:
        st.markdown(f'<div class="rep-links"><a href="{h(website)}" target="_blank" rel="noopener noreferrer">Website</a></div>', unsafe_allow_html=True)


def render_rep_contact_after_accept(rep: dict):
    st.caption("Accepted contact")
    st.write(f"{rep.get('name') or 'Rep'} · {rep.get('email') or 'Email not listed'}")
    if rep.get("phone"):
        st.write(rep.get("phone"))


def render_rep_connection_inbox(rep: dict):
    rep_id = entity_id_key(rep.get("id"))
    connections = all_connections(rep_id=rep_id)
    if not connections:
        st.info("No company connection requests yet.")
        return
    companies_by_id = {entity_id_key(c.get("id")): c for c in all_companies()}
    opportunities_by_id = {entity_id_key(o.get("id")): o for o in all_opportunities()}
    for connection in connections:
        company = companies_by_id.get(str(connection.get("company_id")), {})
        opportunity = opportunities_by_id.get(str(connection.get("opportunity_id")), {})
        status = normalize_connection_status(connection.get("status"))
        st.markdown(
            f'<div class="rep-card">'
            f'<div class="rep-name">{h(company.get("name") or "Company")}</div>'
            f'<div class="rep-company">{h(connection_status_badge(status))}'
            + (f' · {h(opportunity.get("title"))}' if opportunity else "")
            + '</div>'
            f'<div class="rep-bio">{h(connection.get("message") or "No message included.")}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if contact_visible(connection):
            render_company_contact_after_accept(company)
        elif status == "pending":
            a1, a2 = st.columns(2)
            if a1.button("Accept", key=f"accept_conn_{connection.get('id')}", use_container_width=True):
                update_connection_status(connection, "accepted")
                st.success("Connection accepted. Business contact details are now visible.")
                st.rerun()
            if a2.button("Decline", key=f"decline_conn_{connection.get('id')}", use_container_width=True):
                update_connection_status(connection, "declined")
                st.success("Connection declined.")
                st.rerun()


def render_company_connection_outbox(companies: list[dict]):
    st.subheader("Sent Connection Requests")
    if not companies:
        st.info("Create a company profile before sending connection requests.")
        return
    company_options = {f"{c.get('name')} ({c.get('id')})": c for c in companies}
    selected = st.selectbox("Company", list(company_options.keys()), key="company_connections_select")
    company = company_options[selected]
    connections = all_connections(company_id=entity_id_key(company.get("id")))
    if not connections:
        st.info("No sent connection requests for this company yet.")
        return
    reps_by_id = {entity_id_key(r.get("id")): r for r in all_reps()}
    opportunities_by_id = {entity_id_key(o.get("id")): o for o in all_opportunities(company.get("id"))}
    for connection in connections:
        rep = reps_by_id.get(str(connection.get("rep_id")), {})
        opportunity = opportunities_by_id.get(str(connection.get("opportunity_id")), {})
        status = normalize_connection_status(connection.get("status"))
        st.markdown(
            f'<div class="rep-card">'
            f'<div class="rep-name">{h(rep.get("name") or "Rep")}</div>'
            f'<div class="rep-company">{h(rep.get("company") or "Independent rep")} · {h(connection_status_badge(status))}</div>'
            + (f'<div class="rep-headline">{h(opportunity.get("title"))}</div>' if opportunity else '')
            + f'<div class="rep-bio">{h(connection.get("message") or "No message included.")}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if contact_visible(connection):
            render_rep_contact_after_accept(rep)
        elif status == "pending":
            if st.button("Withdraw request", key=f"withdraw_conn_{connection.get('id')}", use_container_width=True):
                update_connection_status(connection, "withdrawn")
                st.success("Connection request withdrawn.")
                st.rerun()


def collection_label_for(target_type: str, target_id) -> str:
    for item in session_shortlist_items():
        if item.get("target_type") == target_type and str(item.get("target_id")) == str(target_id):
            return item.get("collection") or "Saved"
    return "Saved"


def render_saved_reps_page():
    saved_ids = shortlist_targets("rep")
    if not saved_ids:
        st.info("No saved reps yet. Use Save on rep cards or profiles to build a shortlist.")
        return
    summary = reviews_summary(all_reviews())
    reps = [
        rep for rep in all_reps()
        if str(rep.get("id")) in saved_ids and rep.get("active", True) is not False
    ]
    st.subheader(f"{len(reps)} saved reps")
    if not reps:
        st.info("Your saved rep IDs are still here, but those reps are not currently visible in the marketplace.")
        return
    for rep in reps:
        rating, rcount, real = effective_rating(rep, summary)
        recent = summary.get(normalize_rep_review_id(rep.get("id", "")), {}).get("recent", [])
        st.caption(collection_label_for("rep", rep.get("id")))
        rep_card(rep, rep_score(rep, rating), rating, rcount, real, recent)


def render_saved_opportunities_page():
    saved_opportunity_ids = shortlist_targets("opportunity")
    saved_company_ids = shortlist_targets("company")
    opportunities = [o for o in all_opportunities() if str(o.get("id")) in saved_opportunity_ids]
    companies = [c for c in all_companies() if str(c.get("id")) in saved_company_ids]
    companies_by_id = {str(c.get("id", "")).replace("co-", ""): c for c in all_companies()}
    compare_rep = selected_compare_rep()

    st.subheader(f"{len(opportunities)} saved opportunities")
    if not opportunities:
        st.info("No saved opportunities yet. Use Save on opportunity cards or detail pages.")
    for opportunity in opportunities:
        st.caption(collection_label_for("opportunity", opportunity.get("id")))
        opportunity_card(opportunity, companies_by_id, compare_rep)

    st.divider()
    st.subheader(f"{len(companies)} saved companies")
    if not companies:
        st.caption("No saved companies yet.")
    for company in companies:
        st.caption(collection_label_for("company", company.get("id")))
        company_card(company)


def render_admin_dashboard():
    if not render_admin_gate():
        return

    try:
        reps = fetch_table_db("reps", 250)
        companies = fetch_table_db("companies", 250)
        opportunities = fetch_table_db("opportunities", 250)
        claims = fetch_table_db("profile_claims", 250)
        reviews = fetch_table_db("reviews", 250)
        reports = fetch_table_db("content_reports", 250)
        connections = fetch_table_db("connections", 250)
        accounts = fetch_table_db("account_profiles", 250)
        events = fetch_table_db("marketplace_events", 1000)
    except Exception as exc:
        st.error(f"Admin data unavailable: {exc}")
        return

    pending_claims = [c for c in claims if normalize_claim_status(c.get("status")) == "pending"]
    pending_reviews = [r for r in reviews if normalize_review_status(r.get("status")) == "pending"]
    pending_reports = [r for r in reports if (r.get("status") or "pending") == "pending"]
    active_opps = [o for o in opportunities if o.get("active") is not False]
    open_connections = [c for c in connections if normalize_connection_status(c.get("status")) == "pending"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Reps", len(reps), f"{sum(1 for r in reps if r.get('verified'))} verified")
    m2.metric("Companies", len(companies), f"{sum(1 for c in companies if c.get('verified'))} verified")
    m3.metric("Opportunities", len(active_opps), f"{sum(1 for o in opportunities if o.get('featured'))} featured")
    m4.metric("Queue", len(pending_claims) + len(pending_reviews) + len(pending_reports), "claims + reviews + reports")

    c1, c2, c3 = st.columns(3)
    c1.metric("Connections", len(connections), f"{len(open_connections)} pending")
    c2.metric("Recent Signups", len(accounts))
    c3.metric("Reports", len(reports), f"{len(pending_reports)} pending")

    admin_name = current_auth_email() or "admin"
    tabs = st.tabs([
        "Reps", "Companies", "Claims", "Reviews", "Opportunities",
        "Reports", "Signups", "Connections", "Analytics",
    ])

    with tabs[0]:
        q = st.text_input("Search reps", key="admin_rep_q")
        status = st.selectbox("Rep status", ["All", "draft", "active", "hidden", "suspended"], key="admin_rep_status")
        filtered = admin_filter_rows(reps, q, status)
        admin_dataframe(filtered, [
            "id", "created_at", "name", "company", "email", "verified", "featured",
            "profile_status", "active", "claimed", "source", "last_active_at",
        ], "No reps match those filters.")
        options = {f"{r.get('id')} · {r.get('company') or r.get('name') or 'Rep'}": r for r in filtered}
        if options:
            selected = options[st.selectbox("Selected rep", list(options.keys()), key="admin_rep_select")]
            feature_allowed = can_be_featured(record_entitlements(selected, "rep"))
            a, b, c, d = st.columns(4)
            if a.button("Verify", key="admin_rep_verify", use_container_width=True):
                update_rep_db(selected["id"], {"verified": True})
                st.success("Rep verified.")
                st.rerun()
            if b.button("Feature" if not selected.get("featured") else "Unfeature", key="admin_rep_feature", use_container_width=True, disabled=not feature_allowed):
                update_rep_db(selected["id"], {"featured": not bool(selected.get("featured"))})
                st.rerun()
            if not feature_allowed:
                b.caption("Featured placement requires an eligible plan when monetization is enforced.")
            if c.button("Hide", key="admin_rep_hide", use_container_width=True):
                update_rep_db(selected["id"], {"profile_status": "hidden", "active": False})
                st.rerun()
            if d.button("Suspend", key="admin_rep_suspend", use_container_width=True):
                update_rep_db(selected["id"], {"profile_status": "suspended", "active": False})
                st.rerun()
            new_status = st.selectbox(
                "Set rep profile status",
                ["draft", "active", "hidden", "suspended"],
                index=["draft", "active", "hidden", "suspended"].index(selected.get("profile_status") or "active"),
                key="admin_rep_set_status",
            )
            if st.button("Save rep status", key="admin_rep_save_status"):
                update_rep_db(selected["id"], {"profile_status": new_status, "active": new_status == "active"})
                st.rerun()

    with tabs[1]:
        q = st.text_input("Search companies", key="admin_company_q")
        status = st.selectbox("Company status", ["All", "draft", "active", "hidden", "suspended"], key="admin_company_status")
        filtered = admin_filter_rows(companies, q, status)
        admin_dataframe(filtered, [
            "id", "created_at", "name", "contact_email", "verified", "featured",
            "profile_status", "source", "updated_at",
        ], "No companies match those filters.")
        options = {f"{c.get('id')} · {c.get('name') or 'Company'}": c for c in filtered}
        if options:
            selected = options[st.selectbox("Selected company", list(options.keys()), key="admin_company_select")]
            feature_allowed = can_be_featured(record_entitlements(selected, "company"))
            a, b, c, d = st.columns(4)
            if a.button("Verify company", key="admin_company_verify", use_container_width=True):
                update_company_db(selected["id"], {"verified": True})
                st.rerun()
            if b.button("Feature" if not selected.get("featured") else "Unfeature", key="admin_company_feature", use_container_width=True, disabled=not feature_allowed):
                update_company_db(selected["id"], {"featured": not bool(selected.get("featured"))})
                st.rerun()
            if not feature_allowed:
                b.caption("Featured placement requires an eligible plan when monetization is enforced.")
            if c.button("Hide company", key="admin_company_hide", use_container_width=True):
                update_company_db(selected["id"], {"profile_status": "hidden"})
                st.rerun()
            if d.button("Suspend company", key="admin_company_suspend", use_container_width=True):
                update_company_db(selected["id"], {"profile_status": "suspended"})
                st.rerun()
            new_status = st.selectbox(
                "Set company profile status",
                ["draft", "active", "hidden", "suspended"],
                index=["draft", "active", "hidden", "suspended"].index(selected.get("profile_status") or "active"),
                key="admin_company_set_status",
            )
            if st.button("Save company status", key="admin_company_save_status"):
                update_company_db(selected["id"], {"profile_status": new_status})
                st.rerun()

    with tabs[2]:
        q = st.text_input("Search claims", key="admin_claim_q")
        status = st.selectbox("Claim status", ["All", "pending", "approved", "rejected"], key="admin_claim_status")
        filtered = admin_filter_rows(claims, q, status, "status")
        admin_dataframe(filtered, [
            "id", "rep_id", "created_at", "claimant_email", "claimant_name",
            "status", "reviewed_at", "reviewed_by", "message", "admin_notes",
        ], "No profile claims match those filters.")
        options = {f"Claim {c.get('id')} · rep {c.get('rep_id')} · {c.get('claimant_email')}": c for c in filtered}
        if options:
            selected = options[st.selectbox("Selected claim", list(options.keys()), key="admin_claim_select_dashboard")]
            notes = st.text_area("Claim admin notes", key="admin_claim_notes_dashboard")
            a, b = st.columns(2)
            if a.button("Approve claim", key="admin_claim_approve_dashboard", use_container_width=True):
                approve_profile_claim(selected, admin_name, notes)
                st.success("Claim approved.")
                st.rerun()
            if b.button("Reject claim", key="admin_claim_reject_dashboard", use_container_width=True):
                reject_profile_claim(selected, admin_name, notes)
                st.success("Claim rejected.")
                st.rerun()

    with tabs[3]:
        q = st.text_input("Search reviews", key="admin_review_q")
        status = st.selectbox("Review status", ["All", "pending", "approved", "rejected"], key="admin_review_status")
        filtered = admin_filter_rows(reviews, q, status, "status")
        admin_dataframe(filtered, [
            "id", "rep_id", "lead_id", "created_at", "reviewer", "rating", "title",
            "review", "verified_relationship", "status", "reviewed_at", "reviewed_by",
        ], "No reviews match those filters.")
        options = {f"Review {r.get('id')} · rep {r.get('rep_id')} · {r.get('rating')} stars": r for r in filtered}
        if options:
            selected = options[st.selectbox("Selected review", list(options.keys()), key="admin_review_select_dashboard")]
            notes = st.text_area("Review moderation notes", key="admin_review_notes_dashboard")
            a, b = st.columns(2)
            if a.button("Approve review", key="admin_review_approve_dashboard", use_container_width=True):
                moderate_review(selected, "approved", admin_name, notes)
                st.success("Review approved.")
                st.rerun()
            if b.button("Reject review", key="admin_review_reject_dashboard", use_container_width=True):
                moderate_review(selected, "rejected", admin_name, notes)
                st.success("Review rejected.")
                st.rerun()

    with tabs[4]:
        q = st.text_input("Search opportunities", key="admin_opp_q")
        active_filter = st.selectbox("Opportunity state", ["All", "active", "inactive"], key="admin_opp_active")
        filtered = admin_filter_rows(opportunities, q)
        if active_filter == "active":
            filtered = [o for o in filtered if o.get("active") is not False]
        elif active_filter == "inactive":
            filtered = [o for o in filtered if o.get("active") is False]
        admin_dataframe(filtered, [
            "id", "company_id", "created_at", "title", "active", "featured",
            "categories", "metros", "states", "expires_at", "application_count",
        ], "No opportunities match those filters.")
        options = {f"{o.get('id')} · {o.get('title') or 'Opportunity'}": o for o in filtered}
        if options:
            selected = options[st.selectbox("Selected opportunity", list(options.keys()), key="admin_opp_select")]
            feature_allowed = can_be_featured(record_entitlements(selected, "company"))
            a, b, c = st.columns(3)
            if a.button("Feature" if not selected.get("featured") else "Unfeature", key="admin_opp_feature", use_container_width=True, disabled=not feature_allowed):
                update_opportunity_db(selected["id"], {"featured": not bool(selected.get("featured"))})
                st.rerun()
            if not feature_allowed:
                a.caption("Featured placement requires an eligible plan when monetization is enforced.")
            if b.button("Hide opportunity", key="admin_opp_hide", use_container_width=True):
                update_opportunity_db(selected["id"], {"active": False})
                st.rerun()
            if c.button("Reactivate", key="admin_opp_reactivate", use_container_width=True):
                update_opportunity_db(selected["id"], {"active": True})
                st.rerun()

    with tabs[5]:
        q = st.text_input("Search reports", key="admin_report_q")
        status = st.selectbox("Report status", ["All", "pending", "reviewed", "dismissed"], key="admin_report_status")
        filtered = admin_filter_rows(reports, q, status, "status")
        admin_dataframe(filtered, [
            "id", "created_at", "target_type", "target_id", "reason", "details",
            "reporter_email", "status", "reviewed_at", "reviewed_by", "admin_notes",
        ], "No reported content matches those filters.")
        options = {f"Report {r.get('id')} · {r.get('target_type')} {r.get('target_id')} · {r.get('reason')}": r for r in filtered}
        if options:
            selected = options[st.selectbox("Selected report", list(options.keys()), key="admin_report_select")]
            notes = st.text_area("Report admin notes", key="admin_report_notes")
            a, b = st.columns(2)
            if a.button("Mark reviewed", key="admin_report_reviewed", use_container_width=True):
                update_content_report_db(selected["id"], {
                    "status": "reviewed", "reviewed_at": datetime.utcnow().isoformat(),
                    "reviewed_by": admin_name, "admin_notes": notes.strip(),
                })
                st.rerun()
            if b.button("Dismiss report", key="admin_report_dismiss", use_container_width=True):
                update_content_report_db(selected["id"], {
                    "status": "dismissed", "reviewed_at": datetime.utcnow().isoformat(),
                    "reviewed_by": admin_name, "admin_notes": notes.strip(),
                })
                st.rerun()

    with tabs[6]:
        q = st.text_input("Search signups", key="admin_signup_q")
        role = st.selectbox("Role", ["All", "rep", "company", "admin"], key="admin_signup_role")
        filtered = admin_filter_rows(accounts, q, role, "role")
        admin_dataframe(filtered, [
            "user_id", "created_at", "updated_at", "email", "display_name", "role",
        ], "No account profiles match those filters.")

    with tabs[7]:
        q = st.text_input("Search connections", key="admin_connection_q")
        status = st.selectbox("Connection status", ["All", "pending", "accepted", "declined", "withdrawn"], key="admin_connection_status")
        filtered = admin_filter_rows(connections, q, status, "status")
        admin_dataframe(filtered, [
            "id", "created_at", "updated_at", "company_id", "rep_id", "opportunity_id",
            "status", "initiated_by", "message", "owner_user_id",
        ], "No connections match those filters.")

    with tabs[8]:
        render_admin_analytics(events, reps, companies, opportunities, connections)


def homepage_data() -> tuple[list[dict], list[dict], list[dict]]:
    try:
        reps = all_reps()
    except Exception:
        reps = []
    try:
        companies = all_companies()
    except Exception:
        companies = []
    try:
        opportunities = all_opportunities()
    except Exception:
        opportunities = []
    reps = [
        r for r in reps
        if r.get("active", True) is not False
        and (r.get("profile_status") or "active") == "active"
        and not r.get("is_sample")
    ]
    companies = [c for c in companies if (c.get("profile_status") or "active") == "active"]
    opportunities = [o for o in opportunities if o.get("active") is not False]
    return reps, companies, opportunities


def render_homepage():
    reps, companies, opportunities = homepage_data()
    live_counts = SUPABASE_ON
    featured_reps = [r for r in reps if r.get("featured")][:3]
    if not featured_reps:
        featured_reps = [r for r in reps if r.get("verified")][:3]
    featured_opps = [o for o in opportunities if o.get("featured")][:3]
    if not featured_opps:
        featured_opps = opportunities[:3]
    companies_by_id = {str(c.get("id", "")).replace("co-", ""): c for c in companies}

    st.markdown(
        '<div class="home-hero">'
        '<div class="home-kicker">Territory Prospector</div>'
        '<div class="home-title">Find the right sales rep for any territory.</div>'
        '<div class="home-subtitle">Search representative profiles, product-line opportunities, and marketplace territory signals from one focused workspace.</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    p1, p2 = st.columns(2)
    with p1:
        st.markdown(
            '<div class="home-path"><div class="home-path-title">For Companies</div>'
            '<div class="home-path-action">Find Sales Reps</div>'
            '<div class="home-muted">Search by territory, category, industry, experience, availability, and compensation fit.</div></div>',
            unsafe_allow_html=True,
        )
        st.button("Find Sales Reps", type="primary", use_container_width=True,
                  on_click=switch_audience, args=("🛍️ Company — find reps",), key="home_find_reps")
    with p2:
        st.markdown(
            '<div class="home-path"><div class="home-path-title">For Sales Reps</div>'
            '<div class="home-path-action">Find Product Lines</div>'
            '<div class="home-muted">Browse companies and opportunities looking for independent sales coverage.</div></div>',
            unsafe_allow_html=True,
        )
        st.button("Find Product Lines", type="primary", use_container_width=True,
                  on_click=switch_audience, args=("🏢 Rep — find companies",), key="home_find_lines")

    label = "Live marketplace counts" if live_counts else "Demo mode"
    st.markdown(f'<div class="home-section"><h3>{h(label)}</h3></div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Active reps", len(reps) if live_counts else "Connect Supabase")
    c2.metric("Companies", len(companies) if live_counts else "For live counts")
    c3.metric("Active opportunities", len(opportunities) if live_counts else "For live counts")

    st.markdown('<div class="home-section"><h3>Rep search</h3><div class="home-muted">Start simple. Refine with advanced filters on the results page.</div></div>', unsafe_allow_html=True)
    s1, s2, s3 = st.columns(3)
    quick_keyword = s1.text_input("Keyword", key="home_keyword", placeholder="security, POS, payroll")
    quick_metro = s2.selectbox("Territory", [""] + list(METROS.keys()), format_func=lambda v: v or "Any territory", key="home_metro")
    quick_category = s3.selectbox("Category", [""] + list(CATEGORIES.keys()), format_func=lambda v: v or "Any category", key="home_category")
    st.button(
        "Search Representatives",
        type="primary",
        use_container_width=True,
        on_click=switch_to_rep_search,
        args=(quick_keyword.strip(), quick_metro, quick_category),
        key="home_search_reps",
    )

    territory_counts: dict[str, int] = {}
    for rep in reps:
        for metro_name in clean_list(rep.get("metros")):
            territory_counts[metro_name] = territory_counts.get(metro_name, 0) + 1
    st.markdown('<div class="home-section"><h3>Browse reps by territory</h3></div>', unsafe_allow_html=True)
    top_territories = sorted(territory_counts.items(), key=lambda row: (-row[1], row[0]))[:6]
    if top_territories:
        cols = st.columns(3)
        for idx, (metro_name, count) in enumerate(top_territories):
            with cols[idx % 3]:
                st.markdown(
                    f'<div class="home-mini"><div class="home-mini-title">{h(metro_name)}</div>'
                    f'<div class="home-muted">{count} active rep{"s" if count != 1 else ""}</div></div>',
                    unsafe_allow_html=True,
                )
                if st.button("Open Territory", key=f"home_territory_{idx}", use_container_width=True):
                    st.query_params["territory"] = slugify(metro_name)
                    st.rerun()
    else:
        st.caption("Territory browsing will appear as live rep profiles add territory coverage.")

    category_counts: dict[str, int] = {}
    for rep in reps:
        for category_name in clean_list(rep.get("categories")):
            category_counts[category_name] = category_counts.get(category_name, 0) + 1
    st.markdown('<div class="home-section"><h3>Browse by category</h3></div>', unsafe_allow_html=True)
    top_categories = sorted(category_counts.items(), key=lambda row: (-row[1], row[0]))[:6]
    if top_categories:
        cols = st.columns(3)
        for idx, (category_name, count) in enumerate(top_categories):
            with cols[idx % 3]:
                st.markdown(
                    f'<div class="home-mini"><div class="home-mini-title">{h(category_name)}</div>'
                    f'<div class="home-muted">{count} active rep{"s" if count != 1 else ""}</div></div>',
                    unsafe_allow_html=True,
                )
                if st.button("Open Category", key=f"home_category_page_{idx}", use_container_width=True):
                    st.query_params["category"] = slugify(category_name)
                    st.rerun()
    else:
        st.caption("Category pages will appear as live rep profiles add category coverage.")

    st.markdown('<div class="home-section"><h3>Featured representatives</h3></div>', unsafe_allow_html=True)
    if featured_reps:
        summary = reviews_summary(all_reviews())
        for rep in featured_reps:
            rating, rcount, real = effective_rating(rep, summary)
            rep_card(rep, rep_score(rep, rating), rating, rcount, real, [], None, None)
    else:
        st.caption("Featured representatives will appear after admins feature or verify live profiles.")

    st.markdown('<div class="home-section"><h3>Featured opportunities</h3></div>', unsafe_allow_html=True)
    if featured_opps:
        for opportunity in featured_opps:
            opportunity_card(opportunity, companies_by_id, None)
    else:
        st.caption("Featured opportunities will appear as companies post active opportunities.")

    st.markdown('<div class="home-section"><h3>How it works for companies</h3></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="home-mini-grid">'
        '<div class="home-mini"><div class="home-mini-title">Search</div><div class="home-muted">Filter reps by territory, category, industry, customer type, and availability.</div></div>'
        '<div class="home-mini"><div class="home-mini-title">Compare</div><div class="home-muted">Review experience, verification, compensation preferences, and match context.</div></div>'
        '<div class="home-mini"><div class="home-mini-title">Connect</div><div class="home-muted">Send a connection request without exposing private contact details too early.</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="home-section"><h3>How it works for reps</h3></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="home-mini-grid">'
        '<div class="home-mini"><div class="home-mini-title">Create a profile</div><div class="home-muted">Show your territories, product categories, existing lines, and availability.</div></div>'
        '<div class="home-mini"><div class="home-mini-title">Browse lines</div><div class="home-muted">Find companies and opportunities seeking independent representation.</div></div>'
        '<div class="home-mini"><div class="home-mini-title">Build a pipeline</div><div class="home-muted">Use the prospecting workspace to organize local outreach and follow-ups.</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="home-section"><h3>Territory Intelligence</h3><div class="home-muted">Compare marketplace supply and demand by metro, state, category, and industry. Indicators use available marketplace data only.</div></div>', unsafe_allow_html=True)
    st.button("Open Territory Intelligence", use_container_width=True,
              on_click=switch_audience, args=("📊 Territory Intelligence",), key="home_ti")

    st.markdown('<div class="home-section"><h3>Trust and verification</h3><div class="home-muted">Profiles can be verified, reviews require moderation, claims go through admin review, and admin-only tools can hide or suspend profiles when needed.</div></div>', unsafe_allow_html=True)

    st.markdown('<div class="home-section"><h3>Join the marketplace</h3><div class="home-muted">Companies can list opportunities. Reps can publish a profile and mark whether they are open to new lines.</div></div>', unsafe_allow_html=True)
    j1, j2 = st.columns(2)
    j1.button("List a Rep Profile", use_container_width=True,
              on_click=switch_audience, args=("🛍️ Company — find reps",), key="home_join_rep")
    j2.button("List a Company Opportunity", use_container_width=True,
              on_click=switch_audience, args=("🏢 Rep — find companies",), key="home_join_company")


def render_marketplace():
    track_once(
        f"search_{filter_key(cust_filters)}",
        "search",
        category=clean_list(cust_filters.get("categories"))[0] if clean_list(cust_filters.get("categories")) else "",
        metro=clean_list(cust_filters.get("metros"))[0] if clean_list(cust_filters.get("metros")) else "",
        metadata={
            "has_keyword": bool(cust_filters.get("keyword")),
            "categories": clean_list(cust_filters.get("categories")),
            "metros": clean_list(cust_filters.get("metros")),
            "states": clean_list(cust_filters.get("states")),
            "sort": cust_filters.get("sort"),
        },
    )
    roster = all_reps(cust_filters)
    summary = reviews_summary(all_reviews())
    matched = []
    for rep in roster:
        if rep.get("active", True) is False:   # paused listings hidden from customers
            continue
        if (rep.get("profile_status") or "active") != "active":
            continue
        if not rep_matches_filters(rep, cust_filters):
            continue
        rating, rcount, real = effective_rating(rep, summary)
        if rating < cust_min_rating:
            continue
        recent = summary.get(normalize_rep_review_id(rep.get("id", "")), {}).get("recent", [])
        match = score_rep_match(rep, cust_filters, rating)
        matched.append((rep, rep_score(rep, rating), rating, rcount, real, recent, None, match))

    matched = sort_rep_matches(matched, cust_sort)

    st.caption("🟢 Live marketplace — reps below are shared database listings. Sample listings are labeled."
               if SUPABASE_ON else
               "🟡 Demo mode — sample reps + this-browser listings. Connect Supabase to go live (see README).")
    if SUPABASE_ON and not SUPABASE_SERVICE_KEY:
        st.warning("Live marketplace is read-only until `[supabase].service_key` is configured for server-side submissions.")
    st.subheader(f"{len(matched)} reps found")
    filter_labels = active_filter_labels(cust_filters)
    if filter_labels:
        st.caption("Filters: " + " · ".join(filter_labels))
    else:
        st.caption("Showing active marketplace reps. Add filters in the sidebar to narrow the list.")

    if SUPABASE_ON and not roster:
        st.info("The live marketplace has no reps yet. Be the first to list yourself below — "
                "or load a sample roster to explore the experience.")
        if st.button("Load 16 sample reps into the marketplace", disabled=not LIVE_WRITES_ON):
            try:
                insert_reps_db(REPS_SEED)
                st.success("Sample roster loaded into the shared marketplace.")
                st.rerun()
            except Exception as exc:
                st.error(f"Couldn't load samples: {exc}")

    if not matched:
        st.info("No reps match those filters yet.")
        for suggestion in empty_search_suggestions(cust_filters):
            st.caption(f"- {suggestion}")
    else:
        fast = sum(1 for m in matched if float(m[0].get("response_time_hours") or RESPONSE_HOURS.get(m[0].get("response"), 24)) <= 2)
        m1, m2, m3 = st.columns(3)
        m1.metric("Reps matched", len(matched))
        top_match = matched[0][7]
        m2.metric("Top match", f"{top_match.score}%" if top_match and top_match.enough_context else matched[0][1])
        m3.metric("⏱ Reply within ~2 hrs", fast)
        st.divider()
        for rep, sc, rating, rcount, real, recent, distance, match in matched:
            rep_card(rep, sc, rating, rcount, real, recent, distance, match)

    st.divider()
    with st.expander("🙋 List yourself as a rep — get found by customers"):
        code_info = st.session_state.get("new_edit_code")
        if code_info:
            st.success(f"✅ {code_info['company']} is listed! Save your edit code below to edit, "
                       "pause, or remove your listing later"
                       + (" (also emailed to you)." if code_info.get("emailed") else "."))
            st.code(code_info["code"], language=None)
            if st.button("Got it — hide code"):
                st.session_state.pop("new_edit_code", None)
                st.rerun()
        with st.form("list_rep", clear_on_submit=True):
            colA, colB = st.columns(2)
            f_name = colA.text_input("Your name")
            f_company = colB.text_input("Company")
            f_cats = st.multiselect("Categories you serve", list(CATEGORIES.keys()))
            f_metros = st.multiselect("Territories you cover", list(METROS.keys()))
            f_service_area = st.text_input("Service-area center", placeholder="e.g. 95117 or Palo Alto, CA")
            f_service_radius = st.slider("Service radius (miles)", 5, 150, 25, step=5)
            f_headline = st.text_input("Profile headline", placeholder="e.g. Independent med-device rep covering Northern CA")
            f_years = st.number_input("Years of experience", min_value=0, max_value=60, value=0, step=1)
            f_industries = st.text_input("Industries", placeholder="Comma-separated, e.g. medical, dental, security")
            f_customer_types = st.text_input("Customer types", placeholder="Comma-separated, e.g. SMB, enterprise, clinics")
            f_states = st.text_input("States covered", placeholder="Comma-separated, e.g. CA, NV")
            f_zip_codes = st.text_input("ZIP codes covered", placeholder="Comma-separated")
            f_availability = st.selectbox("Availability for new lines", AVAILABILITY_STATUS_OPTIONS, index=0)
            f_preferred_categories = st.text_input("Preferred categories", placeholder="Comma-separated categories you want more of")
            f_preferred_company_types = st.text_input("Preferred company types", placeholder="Comma-separated, e.g. startups, franchises, SMB")
            compA, compB = st.columns(2)
            f_commission_min = compA.number_input("Min commission %", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
            f_commission_max = compB.number_input("Max commission %", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
            f_comp_types = st.text_input("Compensation types", placeholder="Comma-separated, e.g. commission, retainer, bonus")
            f_preferred_compensation = st.text_input("Preferred compensation", placeholder="Comma-separated, e.g. commission, retainer")
            f_minimum_commission = st.number_input("Minimum commission %", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
            f_existing_lines = st.text_input("Existing lines", placeholder="Comma-separated public line card")
            f_competing_lines = st.text_input("Potentially competing lines or categories", placeholder="Comma-separated; used for conflict checks")
            f_notes_for_companies = st.text_area("Notes for companies", placeholder="Public notes about what opportunities are a good fit")
            f_website = st.text_input("Website", placeholder="https://...")
            f_linkedin = st.text_input("LinkedIn URL", placeholder="https://www.linkedin.com/in/...")
            f_deal = st.text_input("Your headline deal", placeholder="e.g. 20% off first order")
            f_strength = st.slider("How strong is this offer?", 0.0, 1.0, 0.5, step=0.05,
                                   help="Ranks you on 'best deal'. 1.0 = a standout offer.")
            f_resp = st.selectbox("Typical response time", RESPONSE_OPTS, index=2)
            f_blurb = st.text_input("One-line description", placeholder="What you sell, in a sentence")
            colC, colD = st.columns(2)
            f_email = colC.text_input("Contact email")
            f_phone = colD.text_input("Contact phone")
            submitted = st.form_submit_button("➕ Add my listing")
        if submitted:
            if SUPABASE_ON and not current_auth_session():
                st.error("Sign in with a rep account before creating a live listing.")
            elif SUPABASE_ON and not can_create_rep(current_account_role()):
                st.error("Your account role cannot create rep listings.")
            elif not (f_name and f_company and f_cats and (f_metros or f_service_area) and f_deal and f_email):
                st.error("Fill name, company, email, at least one category, a territory or service-area center, and your deal.")
            elif f_commission_min and f_commission_max and f_commission_min > f_commission_max:
                st.error("Min commission cannot be greater than max commission.")
            elif rate_limited():
                st.error("You've added several listings this session. Please try again later.")
            else:
                verr = validate_listing(f_name, f_company, f_email, f_deal, f_blurb, all_reps())
                if verr:
                    st.error(verr)
                else:
                    code = secrets.token_hex(4)
                    service_lat = service_lon = None
                    if f_service_area.strip():
                        service_bbox = geocode_area(f_service_area.strip())
                        if service_bbox is None:
                            st.error("Couldn't locate that service-area center. Try a city + state or ZIP.")
                            st.stop()
                        service_lat, service_lon = bbox_center(service_bbox)
                    new_rep = {
                        "name": f_name, "company": f_company, "categories": f_cats,
                        "metros": f_metros, "deal": f_deal, "deal_strength": f_strength,
                        "rating": 0.0, "reviews": 0, "response": f_resp, "verified": False,
                        "blurb": f_blurb or "New rep listing.", "email": f_email, "phone": f_phone or "—",
                        "active": True, "is_sample": False,
                        "service_area": f_service_area.strip(), "service_lat": service_lat,
                        "service_lon": service_lon, "service_radius_miles": f_service_radius,
                        "profile_slug": f"{slugify(f_company)}-{secrets.token_hex(2)}",
                        "headline": f_headline.strip() or f_blurb or f_deal,
                        "years_experience": int(f_years or 0),
                        "industries": clean_list(f_industries),
                        "customer_types": clean_list(f_customer_types),
                        "states": clean_list(f_states),
                        "zip_codes": clean_list(f_zip_codes),
                        "territory_radius": f_service_radius,
                        "availability_status": AVAILABILITY_STATUS_VALUES[f_availability],
                        "open_to_new_lines": AVAILABILITY_STATUS_VALUES[f_availability] in {"open", "selectively_open"},
                        "commission_min": f_commission_min if f_commission_min else None,
                        "commission_max": f_commission_max if f_commission_max else None,
                        "compensation_types": clean_list(f_comp_types),
                        "preferred_categories": clean_list(f_preferred_categories),
                        "preferred_company_types": clean_list(f_preferred_company_types),
                        "preferred_compensation": clean_list(f_preferred_compensation),
                        "minimum_commission": f_minimum_commission if f_minimum_commission else None,
                        "notes_for_companies": f_notes_for_companies.strip(),
                        "existing_lines": clean_list(f_existing_lines),
                        "competing_lines": clean_list(f_competing_lines),
                        "website": safe_public_url(f_website),
                        "linkedin_url": safe_public_url(f_linkedin),
                        "profile_status": "active",
                        "claimed": False,
                        "claim_email": "",
                        "last_active_at": datetime.utcnow().isoformat(),
                        "response_rate": 0,
                        "response_time_hours": RESPONSE_HOURS.get(f_resp, 24),
                        "featured": False,
                        "source": "self_signup",
                        "owner_user_id": current_auth_user_id() or None,
                    }
                    if SUPABASE_ON:
                        new_rep["edit_code_hash"] = _hash_code(code)
                        try:
                            insert_reps_user_db([new_rep])
                            st.session_state["signups_this_session"] = st.session_state.get("signups_this_session", 0) + 1
                            try:
                                emailed = send_edit_code_email(f_email, f_company, code)
                            except Exception:
                                emailed = False
                            st.session_state["new_edit_code"] = {"code": code, "company": f_company, "emailed": emailed}
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Couldn't save your listing: {exc}")
                    else:
                        mine = st.session_state.setdefault("my_reps", [])
                        new_rep["id"] = f"me-{len(mine) + 1}"
                        new_rep["edit_code"] = code
                        mine.append(new_rep)
                        st.session_state["signups_this_session"] = st.session_state.get("signups_this_session", 0) + 1
                        st.session_state["new_edit_code"] = {"code": code, "company": f_company, "emailed": False}
                        st.rerun()
        st.caption(
            "🟢 Live: new listings save to the shared database and appear for every visitor."
            if SUPABASE_ON else
            "🟡 Demo: listings live in this browser session only. Add Supabase secrets to go live (see README)."
        )

    with st.expander("🔧 Manage your listing (edit / pause / remove)"):
        if not SUPABASE_ON:
            st.caption("Listing management needs the live marketplace (Supabase) configured.")
        elif not SUPABASE_SERVICE_KEY:
            st.caption("Editing or removing a listing needs a Supabase **service_role** key in "
                       "secrets (`[supabase].service_key`).")
        else:
            mc1, mc2 = st.columns(2)
            m_email = mc1.text_input("Listing email", key="mng_email")
            m_code = mc2.text_input("Edit code", key="mng_code", type="password")
            if st.button("Find my listing") and m_email.strip() and m_code.strip():
                try:
                    rows = find_listings_by_email(m_email.strip())
                    match = [r for r in rows if r.get("edit_code_hash") == _hash_code(m_code)]
                    if not match:
                        st.error("No listing found for that email + code.")
                        st.session_state.pop("managing", None)
                    elif match[0].get("owner_user_id") and match[0].get("owner_user_id") != current_auth_user_id():
                        st.error("That listing belongs to another signed-in account.")
                        st.session_state.pop("managing", None)
                    else:
                        st.session_state["managing"] = match[0]
                except Exception as exc:
                    st.error(f"Lookup failed: {exc}")
            mrep = st.session_state.get("managing")
            if mrep:
                active = mrep.get("active", True) is not False
                st.markdown(f"**Editing: {mrep['company']}** — {'🟢 live' if active else '⏸ paused'}")
                e_deal = st.text_input("Deal", value=mrep.get("deal", ""), key="ed_deal")
                e_str = st.slider("Offer strength", 0.0, 1.0, float(mrep.get("deal_strength", 0.5) or 0.5),
                                  step=0.05, key="ed_str")
                e_resp = st.selectbox("Response time", RESPONSE_OPTS,
                                      index=RESPONSE_OPTS.index(mrep["response"]) if mrep.get("response") in RESPONSE_OPTS else 2,
                                      key="ed_resp")
                e_cats = st.multiselect("Categories", list(CATEGORIES.keys()),
                                        default=[c for c in mrep.get("categories", []) if c in CATEGORIES], key="ed_cats")
                e_metros = st.multiselect("Territories", list(METROS.keys()),
                                          default=[m for m in mrep.get("metros", []) if m in METROS], key="ed_metros")
                e_service_area = st.text_input("Service-area center", value=mrep.get("service_area", "") or "", key="ed_service_area")
                e_service_radius = st.slider("Service radius (miles)", 5, 150,
                                             int(mrep.get("service_radius_miles", 25) or 25),
                                             step=5, key="ed_service_radius")
                e_headline = st.text_input("Profile headline", value=mrep.get("headline", "") or "", key="ed_headline")
                e_years = st.number_input("Years of experience", min_value=0, max_value=60,
                                          value=int(mrep.get("years_experience", 0) or 0), step=1, key="ed_years")
                e_industries = st.text_input("Industries", value=", ".join(clean_list(mrep.get("industries"))), key="ed_industries")
                e_customer_types = st.text_input("Customer types", value=", ".join(clean_list(mrep.get("customer_types"))), key="ed_customer_types")
                e_states = st.text_input("States covered", value=", ".join(clean_list(mrep.get("states"))), key="ed_states")
                e_zip_codes = st.text_input("ZIP codes covered", value=", ".join(clean_list(mrep.get("zip_codes"))), key="ed_zip_codes")
                e_availability_value = mrep.get("availability_status") or availability_status(mrep).replace(" ", "_")
                e_availability_label = AVAILABILITY_STATUS_LABELS.get(e_availability_value, "Open")
                e_availability = st.selectbox(
                    "Availability for new lines",
                    AVAILABILITY_STATUS_OPTIONS,
                    index=AVAILABILITY_STATUS_OPTIONS.index(e_availability_label),
                    key="ed_availability",
                )
                e_preferred_categories = st.text_input("Preferred categories", value=", ".join(clean_list(mrep.get("preferred_categories"))), key="ed_preferred_categories")
                e_preferred_company_types = st.text_input("Preferred company types", value=", ".join(clean_list(mrep.get("preferred_company_types"))), key="ed_preferred_company_types")
                ec1, ec2 = st.columns(2)
                e_commission_min = ec1.number_input("Min commission %", min_value=0.0, max_value=100.0,
                                                    value=float(mrep.get("commission_min") or 0), step=0.5, key="ed_commission_min")
                e_commission_max = ec2.number_input("Max commission %", min_value=0.0, max_value=100.0,
                                                    value=float(mrep.get("commission_max") or 0), step=0.5, key="ed_commission_max")
                e_comp_types = st.text_input("Compensation types", value=", ".join(clean_list(mrep.get("compensation_types"))), key="ed_comp_types")
                e_preferred_compensation = st.text_input("Preferred compensation", value=", ".join(clean_list(mrep.get("preferred_compensation"))), key="ed_preferred_compensation")
                e_minimum_commission = st.number_input("Minimum commission %", min_value=0.0, max_value=100.0,
                                                       value=float(mrep.get("minimum_commission") or 0), step=0.5, key="ed_minimum_commission")
                e_existing_lines = st.text_input("Existing lines", value=", ".join(clean_list(mrep.get("existing_lines"))), key="ed_existing_lines")
                e_competing_lines = st.text_input("Potentially competing lines or categories", value=", ".join(clean_list(mrep.get("competing_lines"))), key="ed_competing_lines")
                e_notes_for_companies = st.text_area("Notes for companies", value=mrep.get("notes_for_companies", "") or "", key="ed_notes_for_companies")
                e_website = st.text_input("Website", value=mrep.get("website", "") or "", key="ed_website")
                e_linkedin = st.text_input("LinkedIn URL", value=mrep.get("linkedin_url", "") or "", key="ed_linkedin")
                e_blurb = st.text_input("Description", value=mrep.get("blurb", ""), key="ed_blurb")
                b1, b2, b3 = st.columns(3)
                if b1.button("💾 Save changes"):
                    try:
                        if e_commission_min and e_commission_max and e_commission_min > e_commission_max:
                            st.error("Min commission cannot be greater than max commission.")
                            st.stop()
                        service_lat = mrep.get("service_lat")
                        service_lon = mrep.get("service_lon")
                        if e_service_area.strip() and e_service_area.strip() != (mrep.get("service_area") or ""):
                            service_bbox = geocode_area(e_service_area.strip())
                            if service_bbox is None:
                                st.error("Couldn't locate that service-area center. Try a city + state or ZIP.")
                                st.stop()
                            service_lat, service_lon = bbox_center(service_bbox)
                        update_rep_db(mrep["id"], {"deal": e_deal, "deal_strength": e_str,
                                                   "response": e_resp, "categories": e_cats,
                                                   "metros": e_metros, "blurb": e_blurb,
                                                   "service_area": e_service_area.strip(),
                                                   "service_lat": service_lat,
                                                   "service_lon": service_lon,
                                                   "service_radius_miles": e_service_radius,
                                                   "headline": e_headline.strip(),
                                                   "years_experience": int(e_years or 0),
                                                   "industries": clean_list(e_industries),
                                                   "customer_types": clean_list(e_customer_types),
                                                   "states": clean_list(e_states),
                                                   "zip_codes": clean_list(e_zip_codes),
                                                   "territory_radius": e_service_radius,
                                                   "availability_status": AVAILABILITY_STATUS_VALUES[e_availability],
                                                   "open_to_new_lines": AVAILABILITY_STATUS_VALUES[e_availability] in {"open", "selectively_open"},
                                                   "commission_min": e_commission_min if e_commission_min else None,
                                                   "commission_max": e_commission_max if e_commission_max else None,
                                                   "compensation_types": clean_list(e_comp_types),
                                                   "preferred_categories": clean_list(e_preferred_categories),
                                                   "preferred_company_types": clean_list(e_preferred_company_types),
                                                   "preferred_compensation": clean_list(e_preferred_compensation),
                                                   "minimum_commission": e_minimum_commission if e_minimum_commission else None,
                                                   "notes_for_companies": e_notes_for_companies.strip(),
                                                   "existing_lines": clean_list(e_existing_lines),
                                                   "competing_lines": clean_list(e_competing_lines),
                                                   "website": safe_public_url(e_website),
                                                   "linkedin_url": safe_public_url(e_linkedin),
                                                   "last_active_at": datetime.utcnow().isoformat(),
                                                   "response_time_hours": RESPONSE_HOURS.get(e_resp, 24)})
                        st.session_state.pop("managing", None)
                        st.success("Saved.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Save failed: {exc}")
                if b2.button("⏸ Pause" if active else "▶️ Reactivate"):
                    try:
                        update_rep_db(mrep["id"], {"active": not active})
                        st.session_state.pop("managing", None)
                        st.success("Paused." if active else "Reactivated.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Update failed: {exc}")
                if b3.button("🗑 Delete"):
                    try:
                        delete_rep_db(mrep["id"])
                        st.session_state.pop("managing", None)
                        st.success("Listing deleted.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Delete failed: {exc}")

    with st.expander("📥 Are you a listed rep? Check your leads"):
        if not SUPABASE_ON:
            st.caption("The leads inbox needs the live marketplace (Supabase) configured — see README.")
        elif not SUPABASE_SERVICE_KEY:
            st.caption("The in-app inbox needs a Supabase **service_role** key in secrets "
                       "(`[supabase].service_key`). Leads are still emailed to reps without it.")
        else:
            rep_email = st.text_input("Your rep email", key="lead_lookup",
                                      placeholder="the email on your listing")
            if st.button("Show my leads") and rep_email.strip():
                try:
                    leads = fetch_leads_db(rep_email.strip())
                    if not leads:
                        st.info("No leads yet for that email.")
                    else:
                        cols = ["created_at", "customer_name", "customer_email",
                                "customer_phone", "message", "category", "metro"]
                        ldf = pd.DataFrame(leads)
                        st.caption(f"{len(ldf)} lead(s)")
                        st.dataframe(ldf[[c for c in cols if c in ldf.columns]],
                                     use_container_width=True, hide_index=True)
                except Exception as exc:
                    st.error(f"Couldn't load leads: {exc}")

            st.divider()
            if st.button("Show my connection requests") and rep_email.strip():
                try:
                    reps = find_listings_by_email(rep_email.strip())
                    if not reps:
                        st.info("No rep listing found for that email.")
                    else:
                        for rep_row in reps:
                            st.markdown(f"**{rep_row.get('company') or rep_row.get('name') or 'Rep listing'}**")
                            render_rep_connection_inbox(rep_row)
                except Exception as exc:
                    st.error(f"Couldn't load connection requests: {exc}")

    reqs = st.session_state.get("intro_requests", [])
    if reqs:
        st.divider()
        st.caption("Requests you've sent this session: " + ", ".join(r["company"] for r in reqs[-6:]))

    with st.expander("🛡️ Admin moderation"):
        if not LIVE_WRITES_ON:
            st.caption("Admin moderation needs live Supabase plus `[supabase].service_key`.")
        else:
            auth_admin_ok = is_admin_role(current_account_role(), current_admin_verified())
            admin_code = ""
            if not auth_admin_ok and _admin_code_hash():
                admin_code = st.text_input("Admin code", type="password", key="admin_code")
            admin_ok = auth_admin_ok or bool(admin_code and _hash_code(admin_code) == _admin_code_hash())
            if not admin_ok:
                st.caption("Sign in as an approved admin, or enter the configured admin code, to inspect and moderate marketplace data.")
            else:
                st.success("Admin access unlocked for this session.")
                try:
                    admin_reps = fetch_table_db("reps", 25)
                    admin_reviews = fetch_table_db("reviews", 25)
                    admin_leads = fetch_table_db("leads", 25)
                    admin_claims = fetch_table_db("profile_claims", 50)
                    pending_claims = [c for c in admin_claims if normalize_claim_status(c.get("status")) == "pending"]
                    st.caption(
                        f"Recent rows: {len(admin_reps)} reps · {len(admin_reviews)} reviews · "
                        f"{len(admin_leads)} leads · {len(pending_claims)} pending claims"
                    )
                    reps_df = pd.DataFrame(admin_reps)
                    if not reps_df.empty:
                        st.dataframe(
                            reps_df[[c for c in ["id", "company", "email", "verified", "claimed", "claim_email", "active", "is_sample", "source", "created_at"] if c in reps_df.columns]],
                            use_container_width=True, hide_index=True,
                        )
                    a1, a2, a3 = st.columns(3)
                    target_rep_id = a1.text_input("Rep DB id")
                    if a2.button("Verify rep") and target_rep_id.strip():
                        update_rep_db(target_rep_id.strip(), {"verified": True})
                        st.success("Rep verified.")
                        st.rerun()
                    if a3.button("Pause rep") and target_rep_id.strip():
                        update_rep_db(target_rep_id.strip(), {"active": False})
                        st.success("Rep paused.")
                        st.rerun()
                    with st.expander("Profile claim requests", expanded=bool(pending_claims)):
                        claims_df = pd.DataFrame(admin_claims)
                        if claims_df.empty:
                            st.caption("No profile claim requests yet.")
                        else:
                            st.dataframe(
                                claims_df[[c for c in [
                                    "id", "rep_id", "created_at", "claimant_email", "claimant_name",
                                    "status", "reviewed_at", "reviewed_by", "message", "admin_notes",
                                ] if c in claims_df.columns]],
                                use_container_width=True,
                                hide_index=True,
                            )
                            claim_options = {
                                f"Claim {c.get('id')} · rep {c.get('rep_id')} · {c.get('claimant_email')}": c
                                for c in admin_claims
                            }
                            selected_claim_label = st.selectbox("Claim to review", list(claim_options.keys()), key="admin_claim_select")
                            selected_claim = claim_options[selected_claim_label]
                            reviewer = st.text_input("Reviewer email/name", key="admin_claim_reviewer")
                            claim_notes = st.text_area("Admin notes", key="admin_claim_notes")
                            c_approve, c_reject = st.columns(2)
                            if c_approve.button("Approve claim", use_container_width=True):
                                approve_profile_claim(selected_claim, reviewer, claim_notes)
                                st.success("Claim approved. Profile marked claimed; no private edit access was granted.")
                                st.rerun()
                            if c_reject.button("Reject claim", use_container_width=True):
                                reject_profile_claim(selected_claim, reviewer, claim_notes)
                                st.success("Claim rejected.")
                                st.rerun()
                    with st.expander("Recent reviews"):
                        reviews_df = pd.DataFrame(admin_reviews)
                        if reviews_df.empty:
                            st.caption("No reviews yet.")
                        else:
                            st.dataframe(
                                reviews_df[[c for c in [
                                    "id", "rep_id", "lead_id", "created_at", "reviewer",
                                    "rating", "title", "review", "verified_relationship",
                                    "status", "reviewed_at", "reviewed_by", "moderation_notes",
                                ] if c in reviews_df.columns]],
                                use_container_width=True,
                                hide_index=True,
                            )
                            review_options = {
                                f"Review {rv.get('id')} · rep {rv.get('rep_id')} · {rv.get('rating')} stars · {normalize_review_status(rv.get('status'))}": rv
                                for rv in admin_reviews
                            }
                            selected_review_label = st.selectbox("Review to moderate", list(review_options.keys()), key="admin_review_select")
                            selected_review = review_options[selected_review_label]
                            review_moderator = st.text_input("Review moderator", key="admin_review_moderator")
                            review_notes = st.text_area("Review moderation notes", key="admin_review_notes")
                            r_approve, r_reject = st.columns(2)
                            if r_approve.button("Approve review", use_container_width=True):
                                moderate_review(selected_review, "approved", review_moderator, review_notes)
                                st.success("Review approved. It now counts toward public rating aggregates.")
                                st.rerun()
                            if r_reject.button("Reject review", use_container_width=True):
                                moderate_review(selected_review, "rejected", review_moderator, review_notes)
                                st.success("Review rejected.")
                                st.rerun()
                    with st.expander("Recent leads"):
                        leads_df = pd.DataFrame(admin_leads)
                        if leads_df.empty:
                            st.caption("No leads yet.")
                        else:
                            st.dataframe(
                                leads_df[[c for c in ["id", "created_at", "rep_company", "customer_name", "customer_email", "category", "metro", "review_token_used_at"] if c in leads_df.columns]],
                                use_container_width=True, hide_index=True,
                            )
                except Exception as exc:
                    st.error(f"Admin lookup failed: {exc}")


def company_profile_ref(company: dict) -> str:
    return str(company.get("slug") or company.get("id") or slugify(company.get("name") or "company"))


def company_public_text(company: dict) -> str:
    fields = [
        company.get("name"), company.get("description"), company.get("company_size"),
        company.get("headquarters"), company.get("opportunities"), company.get("website"),
    ]
    for key in ["industries", "categories", "states_needed", "metros_needed", "customer_types"]:
        fields.extend(clean_list(company.get(key)))
    return " ".join(str(v) for v in fields if v).lower()


def company_matches_directory_filters(company: dict) -> bool:
    keyword = st.session_state.get("company_keyword", "").strip().lower()
    if keyword:
        haystack = company_public_text(company)
        terms = [term for term in re.split(r"\s+", keyword) if term]
        if not all(term in haystack for term in terms):
            return False
    if not values_overlap(company.get("categories"), st.session_state.get("company_categories", [])):
        return False
    if not values_overlap(company.get("metros_needed"), st.session_state.get("company_metros", [])):
        return False
    if not values_overlap(company.get("states_needed"), st.session_state.get("company_states", [])):
        return False
    return True


def find_company_for_profile(ref: str, companies: list[dict]) -> dict | None:
    for company in companies:
        if ref in {str(company.get("id")), str(company.get("slug") or "")}:
            return company
    return None


def company_card(company: dict):
    badges = ""
    if company.get("verified"):
        badges += '<span class="badge b-verified">Verified</span>'
    badges += f'<span class="badge rep-badge-category">{h(compact_text(company.get("categories"), 3, "Categories not listed"))}</span>'
    badges += f'<span class="badge rep-badge-territory">{h(compact_text(company.get("metros_needed") or company.get("states_needed"), 3, "Territories flexible"))}</span>'
    st.markdown(
        f'<div class="rep-card">'
        f'<div class="rep-name">{h(company.get("name") or "Company")}</div>'
        f'<div class="rep-company">{h(company.get("company_size") or "Size not listed")}'
        + (f' · {h(company.get("headquarters"))}' if company.get("headquarters") else "")
        + '</div>'
        f'<div class="rep-badges">{badges}</div>'
        f'<div class="rep-bio">{h(company.get("description") or "No company overview added yet.")}</div>'
        f'<div class="rep-grid">'
        f'<div><div class="rep-field-label">Products / Services</div><div class="rep-field-value">{h(compact_text(company.get("categories"), 3, "Not listed"))}</div></div>'
        f'<div><div class="rep-field-label">Industries</div><div class="rep-field-value">{h(compact_text(company.get("industries"), 3, "Not listed"))}</div></div>'
        f'<div><div class="rep-field-label">Reps Needed In</div><div class="rep-field-value">{h(format_company_territory(company))}</div></div>'
        f'<div><div class="rep-field-label">Customer Types</div><div class="rep-field-value">{h(compact_text(company.get("customer_types"), 3, "Not listed"))}</div></div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if st.button("View Company Profile", key=f"view_company_{company['id']}", use_container_width=True):
        st.query_params["company"] = company_profile_ref(company)
        st.rerun()
    render_save_controls("company", company.get("id"), f"company_card_{company['id']}", "Contact Later")


def format_company_territory(company: dict) -> str:
    parts = []
    if clean_list(company.get("states_needed")):
        parts.append("States: " + compact_text(company.get("states_needed"), 4, ""))
    if clean_list(company.get("metros_needed")):
        parts.append("Metros: " + compact_text(company.get("metros_needed"), 4, ""))
    return " · ".join(p for p in parts if p) or "Territory needs not listed"


def render_company_profile_page(ref: str):
    company = find_company_for_profile(ref, all_companies())
    if not company:
        st.warning("That company profile is not available.")
        if st.button("Back to company directory"):
            if "company" in st.query_params:
                del st.query_params["company"]
            st.rerun()
        return
    if st.button("Back to company directory"):
        if "company" in st.query_params:
            del st.query_params["company"]
        st.rerun()
    track_once(
        f"company_profile_view_{company.get('id')}",
        "company_profile_view",
        target_type="company",
        target_id=str(company.get("id") or ""),
        category=clean_list(company.get("categories"))[0] if clean_list(company.get("categories")) else "",
        metro=clean_list(company.get("metros_needed"))[0] if clean_list(company.get("metros_needed")) else "",
    )
    title_description(
        company.get("name") or "Company profile",
        f"{company.get('description') or 'Company seeking sales representation.'} Territories: {format_company_territory(company)}.",
    )
    render_shareable_url("Share this company profile", company=company_profile_ref(company))
    st.markdown(
        f'<div class="rep-card">'
        f'<div class="rep-name">{h(company.get("name") or "Company")}</div>'
        f'<div class="rep-company">{h(company.get("company_size") or "Size not listed")}'
        + (f' · {h(company.get("headquarters"))}' if company.get("headquarters") else "")
        + '</div>'
        f'<div class="rep-badges">'
        + ('<span class="badge b-verified">Verified company</span>' if company.get("verified") else '<span class="badge">Unverified company</span>')
        + f'<span class="badge rep-badge-territory">{h(format_company_territory(company))}</span>'
        + '</div>'
        f'<div class="rep-bio">{h(company.get("description") or "No company overview added yet.")}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    render_save_controls("company", company.get("id"), f"company_profile_{company.get('id')}", "Contact Later")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Products / services")
        st.write(public_list_text(company.get("categories")) or "Not listed")
        st.subheader("Industries")
        st.write(public_list_text(company.get("industries")) or "Not listed")
        st.subheader("Customer types")
        st.write(public_list_text(company.get("customer_types")) or "Not listed")
    with c2:
        st.subheader("Territories where reps are needed")
        st.write(format_company_territory(company))
        st.subheader("Opportunities")
        st.write(company.get("opportunities") or "No specific opportunities listed yet.")
        website = safe_public_url(company.get("website") or "")
        if website:
            st.markdown(f'<div class="rep-links"><a href="{h(website)}" target="_blank" rel="noopener noreferrer">Website</a></div>', unsafe_allow_html=True)


def render_company_create_form():
    with st.expander("List your company — find sales reps"):
        code_info = st.session_state.get("new_company_edit_code")
        if code_info:
            st.success(f"{code_info['name']} is listed. Save this edit code to update the profile later.")
            st.code(code_info["code"], language=None)
        with st.form("create_company", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("Company name")
            website = c2.text_input("Website", placeholder="https://...")
            logo_url = st.text_input("Logo URL", placeholder="https://...")
            description = st.text_area("Company overview")
            categories = st.multiselect("Products / services", list(CATEGORIES.keys()))
            industries = st.text_input("Industries", placeholder="Comma-separated")
            size = st.selectbox("Company size", ["", "1-10", "11-50", "51-200", "201-1000", "1000+"])
            headquarters = st.text_input("Headquarters", placeholder="City, ST")
            states_needed = st.multiselect("States where reps are needed", US_STATES)
            metros_needed = st.multiselect("Metros where reps are needed", list(METROS.keys()))
            customer_types = st.text_input("Customer types", placeholder="Comma-separated, e.g. SMB, retail, clinics")
            opportunities = st.text_area("Opportunities for reps")
            p1, p2 = st.columns(2)
            contact_name = p1.text_input("Contact name")
            contact_email = p2.text_input("Contact email")
            submitted = st.form_submit_button("Add company profile")
        if submitted:
            if SUPABASE_ON and not current_auth_session():
                st.error("Sign in with a company account before creating a live company profile.")
            elif SUPABASE_ON and not can_create_company(current_account_role()):
                st.error("Your account role cannot create company profiles.")
            elif not (name and description and (categories or industries) and contact_email):
                st.error("Add company name, overview, contact email, and at least one product/service or industry.")
            elif "@" not in contact_email:
                st.error("Enter a valid contact email.")
            else:
                code = secrets.token_hex(4)
                company = {
                    "name": name.strip(),
                    "slug": f"{slugify(name)}-{secrets.token_hex(2)}",
                    "logo_url": safe_public_url(logo_url),
                    "website": safe_public_url(website),
                    "description": description.strip(),
                    "industries": clean_list(industries),
                    "categories": categories,
                    "company_size": size,
                    "headquarters": headquarters.strip(),
                    "states_needed": states_needed,
                    "metros_needed": metros_needed,
                    "customer_types": clean_list(customer_types),
                    "opportunities": opportunities.strip(),
                    "verified": False,
                    "profile_status": "active",
                    "contact_name": contact_name.strip(),
                    "contact_email": normalize_owner_email(contact_email),
                    "edit_code_hash": _hash_code(code),
                    "source": "self_signup",
                    "owner_user_id": current_auth_user_id() or None,
                }
                try:
                    if SUPABASE_ON:
                        insert_company_user_db(company)
                    else:
                        company["id"] = f"local-company-{len(st.session_state.setdefault('my_companies', [])) + 1}"
                        st.session_state.setdefault("my_companies", []).append(company)
                    st.session_state["new_company_edit_code"] = {"name": company["name"], "code": code}
                    st.rerun()
                except Exception as exc:
                    st.error(f"Couldn't save company profile: {exc}")


def render_company_edit_form():
    with st.expander("Manage your company profile"):
        if not SUPABASE_ON:
            st.caption("Company profile management needs Supabase configured. Demo companies last for this browser session.")
            return
        if not SUPABASE_SERVICE_KEY:
            st.caption("Editing company profiles needs `[supabase].service_key` configured.")
            return
        e1, e2 = st.columns(2)
        email = e1.text_input("Contact email", key="company_manage_email")
        code = e2.text_input("Edit code", type="password", key="company_manage_code")
        if st.button("Find company profile") and email.strip() and code.strip():
            try:
                matches = [c for c in find_companies_by_email(email.strip()) if c.get("edit_code_hash") == _hash_code(code)]
                if not matches:
                    st.error("No company profile found for that email + code.")
                    st.session_state.pop("managing_company", None)
                elif matches[0].get("owner_user_id") and matches[0].get("owner_user_id") != current_auth_user_id():
                    st.error("That company profile belongs to another signed-in account.")
                    st.session_state.pop("managing_company", None)
                else:
                    st.session_state["managing_company"] = matches[0]
            except Exception as exc:
                st.error(f"Lookup failed: {exc}")
        company = st.session_state.get("managing_company")
        if company:
            st.markdown(f"**Editing: {company['name']}**")
            with st.form("edit_company"):
                description = st.text_area("Company overview", value=company.get("description", ""))
                categories = st.multiselect("Products / services", list(CATEGORIES.keys()),
                                            default=[c for c in company.get("categories", []) if c in CATEGORIES])
                industries = st.text_input("Industries", value=", ".join(clean_list(company.get("industries"))))
                states_needed = st.multiselect("States where reps are needed", US_STATES,
                                               default=[s for s in company.get("states_needed", []) if s in US_STATES])
                metros_needed = st.multiselect("Metros where reps are needed", list(METROS.keys()),
                                               default=[m for m in company.get("metros_needed", []) if m in METROS])
                customer_types = st.text_input("Customer types", value=", ".join(clean_list(company.get("customer_types"))))
                opportunities = st.text_area("Opportunities for reps", value=company.get("opportunities", "") or "")
                status = st.selectbox("Profile status", ["active", "hidden"], index=0 if company.get("profile_status") != "hidden" else 1)
                saved = st.form_submit_button("Save company profile")
            if saved:
                try:
                    update_company_db(company["id"], {
                        "description": description.strip(),
                        "categories": categories,
                        "industries": clean_list(industries),
                        "states_needed": states_needed,
                        "metros_needed": metros_needed,
                        "customer_types": clean_list(customer_types),
                        "opportunities": opportunities.strip(),
                        "profile_status": status,
                    })
                    st.session_state.pop("managing_company", None)
                    st.success("Company profile saved.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Save failed: {exc}")


def opportunity_company_name(opportunity: dict, companies_by_id: dict) -> str:
    embedded = opportunity.get("companies") or {}
    if embedded.get("name"):
        return embedded["name"]
    company = companies_by_id.get(str(opportunity.get("company_id")))
    return (company or {}).get("name", "Company")


def opportunity_ref(opportunity: dict) -> str:
    return str(opportunity.get("slug") or opportunity.get("id") or slugify(opportunity.get("title") or "opportunity"))


def opportunity_compensation_text(opportunity: dict) -> str:
    bits = []
    low = opportunity.get("commission_min")
    high = opportunity.get("commission_max")
    if low not in (None, "") and high not in (None, ""):
        bits.append(f"{float(low):g}-{float(high):g}% commission")
    elif low not in (None, ""):
        bits.append(f"from {float(low):g}% commission")
    elif high not in (None, ""):
        bits.append(f"up to {float(high):g}% commission")
    if clean_list(opportunity.get("compensation_types")):
        bits.append(", ".join(clean_list(opportunity.get("compensation_types"))))
    if opportunity.get("recurring_commission"):
        bits.append("recurring")
    return " · ".join(bits) or "Compensation not listed"


def opportunity_territory_text(opportunity: dict) -> str:
    parts = []
    if clean_list(opportunity.get("states")):
        parts.append("States: " + compact_text(opportunity.get("states"), 4, ""))
    if clean_list(opportunity.get("metros")):
        parts.append("Metros: " + compact_text(opportunity.get("metros"), 4, ""))
    if clean_list(opportunity.get("zip_codes")):
        parts.append("ZIPs: " + compact_text(opportunity.get("zip_codes"), 4, ""))
    return " · ".join(p for p in parts if p) or "Territory flexible"


def selected_compare_rep() -> dict | None:
    rep_id = st.session_state.get("opp_compare_rep_id", "")
    if not rep_id:
        return None
    for rep in all_reps():
        if str(rep.get("id")) == str(rep_id):
            return rep
    return None


def opportunity_matches_filters(opportunity: dict) -> bool:
    territory = clean_list(st.session_state.get("opp_territory", []))
    metros = [t for t in territory if t in METROS]
    states = [t for t in territory if t in US_STATES]
    if metros and not values_overlap(opportunity.get("metros"), metros):
        return False
    if states and not values_overlap(opportunity.get("states"), states):
        return False
    if not values_overlap(opportunity.get("categories"), st.session_state.get("opp_categories", [])):
        return False
    if not values_overlap(opportunity.get("industries"), st.session_state.get("opp_industries", [])):
        return False
    if not values_overlap(opportunity.get("compensation_types"), st.session_state.get("opp_compensation", [])):
        return False
    if st.session_state.get("opp_exclusive") and not opportunity.get("exclusive_territory"):
        return False
    if int(opportunity.get("experience_required") or 0) > int(st.session_state.get("opp_experience", 20)):
        return False
    return True


def render_opportunity_match(match: RepMatchResult | None):
    if not match or not match.enough_context:
        return
    st.markdown(
        f'<div class="matchbox"><div class="matchnum">{match.score}%</div>'
        f'<div class="matchlbl">{h(match.confidence_label)}</div></div>',
        unsafe_allow_html=True,
    )
    render_match_explanation(match, "opportunity")


def recommendation_detail_lines(match: RepMatchResult) -> str:
    conflicts = match.possible_conflicts[:2]
    conflict_text = "; ".join(conflicts) if conflicts else "No obvious conflicts"
    public_conflict_details = ", ".join(match.public_conflict_details[:3])
    product_line_text = match.product_line_conflict
    if public_conflict_details:
        product_line_text += f": {public_conflict_details}"
    return (
        f'<div class="rep-grid">'
        f'<div><div class="rep-field-label">Territory</div><div class="rep-field-value">{h(match.territory_overlap)}</div></div>'
        f'<div><div class="rep-field-label">Category</div><div class="rep-field-value">{h(match.category_overlap)}</div></div>'
        f'<div><div class="rep-field-label">Compensation</div><div class="rep-field-value">{h(match.compensation_compatibility)}</div></div>'
        f'<div><div class="rep-field-label">Availability</div><div class="rep-field-value">{h(match.availability)}</div></div>'
        f'<div><div class="rep-field-label">Product-Line Conflict</div><div class="rep-field-value">{h(product_line_text)}</div></div>'
        f'<div><div class="rep-field-label">Conflict Rationale</div><div class="rep-field-value">{h(match.product_line_conflict_explanation)}</div></div>'
        f'<div><div class="rep-field-label">Other Conflicts</div><div class="rep-field-value">{h(conflict_text)}</div></div>'
        f'<div><div class="rep-field-label">Why</div><div class="rep-field-value">{h((match.explanations or match.confidence_notes or ["Limited matching context"])[0])}</div></div>'
        f'</div>'
    )


def render_recommended_rep_card(rep: dict, match: RepMatchResult, rating: float, rcount: int, key_prefix: str):
    summary = rep_card_summary(rep, rating, rcount)
    st.markdown(
        f'<div class="rep-card">'
        f'<div class="rep-name">{h(summary["name"])}</div>'
        f'<div class="rep-company">{h(summary["company"])}</div>'
        f'<div class="rep-headline">{h(summary["headline"])}</div>'
        f'<div class="rep-badges">'
        f'<span class="badge rep-badge-territory">{h(match.confidence_label)}</span>'
        f'<span class="badge rep-badge-category">{match.score}% match</span>'
        + ('<span class="badge b-verified">Verified</span>' if rep.get("verified") else '<span class="badge">Unverified</span>')
        + '</div>'
        + recommendation_detail_lines(match)
        + f'</div>',
        unsafe_allow_html=True,
    )
    render_match_explanation(match, f"{key_prefix}_{rep.get('id')}")
    render_save_controls("rep", rep.get("id"), f"{key_prefix}_save_rep_{rep.get('id')}", "Strong Candidates")
    if st.button("View Profile", key=f"{key_prefix}_view_rep_{rep.get('id')}", use_container_width=True):
        st.query_params["rep"] = rep_profile_ref(rep)
        if "opportunity" in st.query_params:
            del st.query_params["opportunity"]
        st.rerun()


def render_recommended_representatives(opportunity: dict, limit: int = 5):
    if not can_use_full_matching(current_entitlements()):
        entitlement_notice("Full matching recommendations")
        return
    reps = [
        rep for rep in all_reps()
        if rep.get("active", True) is not False and (rep.get("profile_status") or "active") == "active"
    ]
    summary = reviews_summary(all_reviews())
    ranked = []
    for rep in reps:
        rating, rcount, _real = effective_rating(rep, summary)
        match = score_opportunity_rep_match(opportunity, rep, rating)
        if match.enough_context:
            ranked.append((rep, match, rating, rcount))
    ranked.sort(key=lambda row: row[1].score, reverse=True)
    st.subheader("Recommended Representatives")
    if not ranked:
        st.info("Add territory, category, industry, or customer-type details to this opportunity to generate representative recommendations.")
        return
    for rep, match, rating, rcount in ranked[:limit]:
        render_recommended_rep_card(rep, match, rating, rcount, f"opp_rec_{opportunity.get('id')}")


def render_recommended_opportunity_card(opportunity: dict, match: RepMatchResult, companies_by_id: dict, key_prefix: str):
    st.markdown(
        f'<div class="rep-card">'
        f'<div class="rep-name">{h(opportunity.get("title") or "Sales opportunity")}</div>'
        f'<div class="rep-company">{h(opportunity_company_name(opportunity, companies_by_id))}</div>'
        f'<div class="rep-badges">'
        f'<span class="badge rep-badge-territory">{h(match.confidence_label)}</span>'
        f'<span class="badge rep-badge-category">{match.score}% match</span>'
        + ('<span class="badge rep-badge-territory">Exclusive territory</span>' if opportunity.get("exclusive_territory") else '<span class="badge">Flexible territory</span>')
        + '</div>'
        + recommendation_detail_lines(match)
        + f'<div class="rep-bio">{h(opportunity.get("description") or "No description added yet.")}</div>'
        + f'</div>',
        unsafe_allow_html=True,
    )
    render_match_explanation(match, f"{key_prefix}_{opportunity.get('id')}")
    render_save_controls("opportunity", opportunity.get("id"), f"{key_prefix}_save_opp_{opportunity.get('id')}", "Saved")
    if st.button("View Opportunity", key=f"{key_prefix}_view_opp_{opportunity.get('id')}", use_container_width=True):
        st.query_params["opportunity"] = opportunity_ref(opportunity)
        if "rep" in st.query_params:
            del st.query_params["rep"]
        st.rerun()


def render_recommended_opportunities(rep: dict, rating: float | None = None, limit: int = 5):
    if not can_use_full_matching(current_entitlements()):
        entitlement_notice("Opportunity recommendations")
        return
    opportunities = [o for o in all_opportunities() if o.get("active", True)]
    companies_by_id = {str(c.get("id", "")).replace("co-", ""): c for c in all_companies()}
    ranked = []
    for opportunity in opportunities:
        match = score_opportunity_rep_match(opportunity, rep, rating)
        if match.enough_context:
            ranked.append((opportunity, match))
    ranked.sort(key=lambda row: row[1].score, reverse=True)
    st.subheader("Recommended Opportunities")
    if not ranked:
        st.info("No active opportunities have enough matching context yet. Opportunities with territory, category, industry, or customer-type details will appear here.")
        return
    for opportunity, match in ranked[:limit]:
        render_recommended_opportunity_card(opportunity, match, companies_by_id, f"rep_rec_{rep.get('id')}")


def fmt_indicator_number(value, fallback: str = "Not enough marketplace data") -> str:
    if value is None:
        return fallback
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def fmt_commission_indicator(result: TerritoryIntelligenceResult) -> str:
    if result.average_commission_min is None and result.average_commission_max is None:
        return "Not enough marketplace data"
    if result.average_commission_min is not None and result.average_commission_max is not None:
        return f"{result.average_commission_min:.1f}% - {result.average_commission_max:.1f}%"
    if result.average_commission_min is not None:
        return f"{result.average_commission_min:.1f}% min avg"
    return f"{result.average_commission_max:.1f}% max avg"


def territory_map_color(row: dict) -> list[int]:
    if row.get("not_enough_data"):
        return [138, 153, 147, 145]
    ratio = row.get("supply_to_demand_ratio")
    if ratio is None:
        return [138, 153, 147, 145]
    if ratio < 0.75:
        return [198, 67, 42, 205]
    if ratio <= 1.5:
        return [182, 122, 30, 205]
    return [14, 90, 84, 205]


def territory_map_radius(row: dict) -> int:
    activity = int(row.get("activity") or 0)
    return max(22000, min(95000, 18000 + activity * 7000))


def selected_pydeck_metro(selection) -> str:
    try:
        objects = selection.selection.get("objects", {})
        for layer_objects in objects.values():
            if layer_objects:
                return str(layer_objects[0].get("metro") or "")
    except Exception:
        return ""
    return ""


def render_territory_map(rows: list[dict]) -> str:
    if not rows:
        st.info("Map data is unavailable for the selected filters.")
        return ""
    for row in rows:
        row["color"] = territory_map_color(row)
        row["radius"] = territory_map_radius(row)
        ratio = row.get("supply_to_demand_ratio")
        row["ratio_label"] = "n/a" if ratio is None else f"{ratio:.2f}"
        row["score_label"] = "Not enough data" if row.get("opportunity_score") is None else f"{row['opportunity_score']}/100"

    if not HAVE_PYDECK:
        st.info("Interactive map rendering is unavailable. Use the metro table below to drill into a territory.")
        return ""

    layer = pdk.Layer(
        "ScatterplotLayer",
        rows,
        get_position="[lon, lat]",
        get_fill_color="color",
        get_radius="radius",
        pickable=True,
        stroked=True,
        get_line_color=[255, 255, 255, 190],
        line_width_min_pixels=1,
    )
    text_layer = pdk.Layer(
        "TextLayer",
        rows,
        get_position="[lon, lat]",
        get_text="metro",
        get_size=12,
        get_color=[30, 42, 38, 230],
        get_alignment_baseline="'bottom'",
        get_pixel_offset=[0, -12],
        pickable=False,
    )
    deck = pdk.Deck(
        map_style=None,
        initial_view_state=pdk.ViewState(latitude=39.5, longitude=-98.35, zoom=3, pitch=0),
        layers=[layer, text_layer],
        tooltip={
            "html": (
                "<b>{metro}</b><br/>"
                "Rep supply: {rep_supply}<br/>"
                "Opportunities: {opportunities}<br/>"
                "Company demand: {company_demand}<br/>"
                "Supply/demand: {ratio_label}<br/>"
                "Opportunity Score: {score_label}"
            ),
            "style": {"backgroundColor": "#1f2d2a", "color": "white"},
        },
    )
    selection = st.pydeck_chart(deck, on_select="rerun", selection_mode="single-object", key="territory_activity_map")
    return selected_pydeck_metro(selection)


def render_territory_drilldown(metro: str, reps: list[dict], opportunities: list[dict], companies: list[dict], category: str = "", industry: str = ""):
    if not metro:
        return
    st.subheader(f"{metro} Drilldown")
    result = calculate_territory_intelligence(reps, opportunities, companies, metro=metro, category=category, industry=industry)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Rep supply", result.total_active_reps)
    k2.metric("Active opportunities", result.active_opportunities)
    k3.metric("Company demand", result.companies_seeking_reps)
    k4.metric("Opportunity Score", "Not enough data" if result.opportunity_score is None else f"{result.opportunity_score}/100")
    area_reps = matching_reps(reps, metro=metro, category=category, industry=industry)
    area_opps = matching_opportunities(opportunities, metro=metro, category=category, industry=industry)
    d1, d2 = st.columns(2)
    with d1:
        st.markdown("**Reps in territory**")
        if not area_reps:
            st.caption("No matching reps listed for this metro.")
        for rep in area_reps[:8]:
            st.write(f"- {rep.get('name') or 'Rep'} · {rep.get('company') or 'Independent rep'}")
    with d2:
        st.markdown("**Opportunities in territory**")
        if not area_opps:
            st.caption("No matching opportunities listed for this metro.")
        for opportunity in area_opps[:8]:
            st.write(f"- {opportunity.get('title') or 'Sales opportunity'}")


def render_territory_intelligence_dashboard():
    if not can_view_territory_intelligence(current_entitlements()):
        entitlement_notice("Territory Intelligence")
        return
    metro = st.session_state.get("ti_metro", "")
    state = st.session_state.get("ti_state", "")
    category = st.session_state.get("ti_category", "")
    industry = st.session_state.get("ti_industry", "")
    area_label = metro or state or "All marketplace territories"
    category_label = category or industry or "All categories"

    st.caption("Marketplace indicators only. Calculations use listed marketplace data and are not guarantees of demand, coverage, or close probability.")
    if not SUPABASE_ON:
        st.info("Not enough marketplace data. Connect Supabase to calculate Territory Intelligence from the shared marketplace tables.")
        return

    try:
        reps, opportunities, companies = territory_intelligence_rows(metro, state, category, industry)
    except Exception as exc:
        st.warning(f"Not enough marketplace data. Territory Intelligence could not read the marketplace tables ({exc}).")
        return

    result = calculate_territory_intelligence(
        reps,
        opportunities,
        companies,
        metro=metro,
        state=state,
        category=category,
        industry=industry,
    )

    st.subheader(f"{area_label} · {category_label}")
    if result.not_enough_data:
        st.info("Not enough marketplace data.")

    m1, m2, m3 = st.columns(3)
    m1.metric("Total active reps", result.total_active_reps)
    m2.metric("Verified reps", result.verified_reps)
    m3.metric("Open to new lines", result.open_reps)

    m4, m5, m6 = st.columns(3)
    m4.metric("Active opportunities", result.active_opportunities)
    m5.metric("Companies seeking reps", result.companies_seeking_reps)
    m6.metric("Average rep rating", fmt_indicator_number(result.average_rep_rating))

    m7, m8, m9 = st.columns(3)
    m7.metric("Average stated commission range", fmt_commission_indicator(result))
    m8.metric("Supply / demand ratio", fmt_indicator_number(result.supply_to_demand_ratio))
    m9.metric("Opportunity Score", "Not enough marketplace data" if result.opportunity_score is None else f"{result.opportunity_score}/100")

    st.divider()
    st.subheader("Calculation Notes")
    for note in result.calculation_notes:
        st.caption(f"- {note}")
    st.caption("Supply / demand ratio = active matching reps divided by active matching opportunities plus companies seeking reps.")

    st.divider()
    st.subheader("Territory Map")
    st.caption("Markers use approximate metro centers and relative marketplace activity. No precise home addresses are stored or displayed.")
    map_rows = build_metro_activity_rows(reps, opportunities, companies, METROS, category=category, industry=industry)
    selected_from_map = render_territory_map(map_rows)
    metro_options = [row["metro"] for row in map_rows]
    default_metro = selected_from_map or metro or (metro_options[0] if metro_options else "")
    selected_metro = st.selectbox(
        "Drill into metro",
        metro_options,
        index=metro_options.index(default_metro) if default_metro in metro_options else 0,
        key="territory_map_drill_metro",
    ) if metro_options else ""
    render_territory_drilldown(selected_from_map or selected_metro, reps, opportunities, companies, category, industry)


def opportunity_card(opportunity: dict, companies_by_id: dict, compare_rep: dict | None):
    match = score_opportunity_rep_match(opportunity, compare_rep) if compare_rep else None
    c1, c2 = st.columns([5, 1.4])
    with c1:
        badges = ""
        if opportunity.get("featured"):
            badges += '<span class="badge b-verified">Featured</span>'
        if opportunity.get("exclusive_territory"):
            badges += '<span class="badge rep-badge-territory">Exclusive territory</span>'
        badges += f'<span class="badge rep-badge-category">{h(compact_text(opportunity.get("categories"), 3, "Category flexible"))}</span>'
        badges += f'<span class="badge rep-badge-territory">{h(opportunity_territory_text(opportunity))}</span>'
        st.markdown(
            f'<div class="rep-card">'
            f'<div class="rep-name">{h(opportunity.get("title") or "Sales opportunity")}</div>'
            f'<div class="rep-company">{h(opportunity_company_name(opportunity, companies_by_id))}</div>'
            f'<div class="rep-badges">{badges}</div>'
            f'<div class="rep-bio">{h(opportunity.get("description") or "No description added yet.")}</div>'
            f'<div class="rep-grid">'
            f'<div><div class="rep-field-label">Compensation</div><div class="rep-field-value">{h(opportunity_compensation_text(opportunity))}</div></div>'
            f'<div><div class="rep-field-label">Experience Required</div><div class="rep-field-value">{int(opportunity.get("experience_required") or 0)}+ years</div></div>'
            f'<div><div class="rep-field-label">Customer Types</div><div class="rep-field-value">{h(compact_text(opportunity.get("customer_types"), 3, "Not listed"))}</div></div>'
            f'<div><div class="rep-field-label">Applications</div><div class="rep-field-value">{int(opportunity.get("application_count") or 0)}</div></div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c2:
        render_opportunity_match(match)
        render_save_controls("opportunity", opportunity.get("id"), f"opp_card_{opportunity['id']}", "Saved")
        if st.button("View Opportunity", key=f"view_opp_{opportunity['id']}", use_container_width=True):
            st.query_params["opportunity"] = opportunity_ref(opportunity)
            st.rerun()


def find_opportunity(ref: str, opportunities: list[dict]) -> dict | None:
    for opportunity in opportunities:
        if ref in {
            str(opportunity.get("id")),
            str(opportunity.get("id", "")).replace("opp-", ""),
            str(opportunity.get("slug") or ""),
            slugify(opportunity.get("title") or ""),
        }:
            return opportunity
    return None


def render_opportunity_detail(ref: str):
    opportunities = all_opportunities()
    opportunity = find_opportunity(ref, opportunities)
    if not opportunity:
        st.warning("That opportunity is not available.")
        if st.button("Back to opportunities"):
            if "opportunity" in st.query_params:
                del st.query_params["opportunity"]
            st.rerun()
        return
    companies_by_id = {str(c.get("id", "")).replace("co-", ""): c for c in all_companies()}
    if st.button("Back to opportunities"):
        if "opportunity" in st.query_params:
            del st.query_params["opportunity"]
        st.rerun()
    compare_rep = selected_compare_rep()
    track_once(
        f"opportunity_view_{opportunity.get('id')}",
        "opportunity_view",
        target_type="opportunity",
        target_id=str(opportunity.get("id") or ""),
        category=clean_list(opportunity.get("categories"))[0] if clean_list(opportunity.get("categories")) else "",
        metro=clean_list(opportunity.get("metros"))[0] if clean_list(opportunity.get("metros")) else "",
    )
    title_description(
        opportunity.get("title") or "Sales opportunity",
        f"{opportunity_company_name(opportunity, companies_by_id)} is seeking sales representation. Territory: {opportunity_territory_text(opportunity)}.",
    )
    render_shareable_url("Share this opportunity", opportunity=opportunity_ref(opportunity))
    st.markdown(
        f'<div class="rep-card">'
        f'<div class="rep-name">{h(opportunity.get("title") or "Sales opportunity")}</div>'
        f'<div class="rep-company">{h(opportunity_company_name(opportunity, companies_by_id))}</div>'
        f'<div class="rep-badges">'
        + ('<span class="badge b-verified">Featured</span>' if opportunity.get("featured") else '')
        + ('<span class="badge rep-badge-territory">Exclusive territory</span>' if opportunity.get("exclusive_territory") else '<span class="badge">Shared/flexible territory</span>')
        + f'<span class="badge rep-badge-category">{h(compact_text(opportunity.get("categories"), 3, "Category flexible"))}</span>'
        + '</div>'
        f'<div class="rep-bio">{h(opportunity.get("description") or "No description added yet.")}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if compare_rep:
        render_opportunity_match(score_opportunity_rep_match(opportunity, compare_rep))
    render_save_controls("opportunity", opportunity.get("id"), f"opp_detail_{opportunity.get('id')}", "Saved")
    d1, d2 = st.columns(2)
    with d1:
        st.subheader("Territory")
        st.write(opportunity_territory_text(opportunity))
        st.subheader("Categories / industries")
        st.write(public_list_text(opportunity.get("categories")) or "Categories flexible")
        st.write(public_list_text(opportunity.get("industries")) or "Industries not listed")
        st.subheader("Customer types")
        st.write(public_list_text(opportunity.get("customer_types")) or "Not listed")
    with d2:
        st.subheader("Compensation")
        st.write(opportunity_compensation_text(opportunity))
        st.subheader("Requirements")
        st.write(f"{int(opportunity.get('experience_required') or 0)}+ years experience")
        st.write("Exclusive territory" if opportunity.get("exclusive_territory") else "Shared or flexible territory")
        st.subheader("Status")
        st.write("Active")
    st.divider()
    render_recommended_representatives(opportunity)


def find_territory_name(ref: str, reps: list[dict], companies: list[dict], opportunities: list[dict]) -> str:
    candidates = set()
    for rep in reps:
        candidates.update(clean_list(rep.get("metros")))
        candidates.update(clean_list(rep.get("states")))
    for company in companies:
        candidates.update(clean_list(company.get("metros_needed")))
        candidates.update(clean_list(company.get("states_needed")))
    for opportunity in opportunities:
        candidates.update(clean_list(opportunity.get("metros")))
        candidates.update(clean_list(opportunity.get("states")))
    for candidate in sorted(candidates):
        if slug_match(candidate, ref):
            return candidate
    return ""


def find_category_name(ref: str, reps: list[dict], companies: list[dict], opportunities: list[dict]) -> str:
    candidates = set(CATEGORIES.keys())
    for rep in reps:
        candidates.update(clean_list(rep.get("categories")))
        candidates.update(clean_list(rep.get("industries")))
    for company in companies:
        candidates.update(clean_list(company.get("categories")))
        candidates.update(clean_list(company.get("industries")))
    for opportunity in opportunities:
        candidates.update(clean_list(opportunity.get("categories")))
        candidates.update(clean_list(opportunity.get("industries")))
    for candidate in sorted(candidates):
        if slug_match(candidate, ref):
            return candidate
    return ""


def render_public_territory_page(ref: str):
    reps = active_public_reps()
    companies = active_public_companies()
    opportunities = active_public_opportunities()
    territory = find_territory_name(ref, reps, companies, opportunities)
    if not territory:
        st.warning("No public marketplace page exists for that territory yet.")
        st.caption("Territory pages are only shown when they are backed by active marketplace profiles or opportunities.")
        return
    reps = [r for r in reps if territory in clean_list(r.get("metros")) or territory in clean_list(r.get("states"))]
    companies = [c for c in companies if territory in clean_list(c.get("metros_needed")) or territory in clean_list(c.get("states_needed"))]
    opportunities = [o for o in opportunities if territory in clean_list(o.get("metros")) or territory in clean_list(o.get("states"))]
    if not any([reps, companies, opportunities]):
        st.warning("No active marketplace content is available for that territory.")
        return
    title_description(
        f"Sales reps and opportunities in {territory}",
        f"Browse active representatives, companies, and product-line opportunities connected to {territory}.",
    )
    render_shareable_url("Share this territory page", territory=slugify(territory))
    m1, m2, m3 = st.columns(3)
    m1.metric("Active reps", len(reps))
    m2.metric("Companies", len(companies))
    m3.metric("Opportunities", len(opportunities))
    summary = reviews_summary(all_reviews())
    if reps:
        st.subheader("Representatives")
        for rep in reps[:6]:
            rating, rcount, real = effective_rating(rep, summary)
            rep_card(rep, rep_score(rep, rating), rating, rcount, real, [], None, None)
    if opportunities:
        st.subheader("Opportunities")
        companies_by_id = {str(c.get("id", "")).replace("co-", ""): c for c in all_companies()}
        for opportunity in opportunities[:6]:
            opportunity_card(opportunity, companies_by_id, None)
    if companies:
        st.subheader("Companies")
        for company in companies[:6]:
            company_card(company)


def render_public_category_page(ref: str):
    reps = active_public_reps()
    companies = active_public_companies()
    opportunities = active_public_opportunities()
    category = find_category_name(ref, reps, companies, opportunities)
    if not category:
        st.warning("No public marketplace page exists for that category yet.")
        st.caption("Category pages are only shown when they are backed by active marketplace profiles or opportunities.")
        return
    reps = [r for r in reps if category in clean_list(r.get("categories")) or category in clean_list(r.get("industries"))]
    companies = [c for c in companies if category in clean_list(c.get("categories")) or category in clean_list(c.get("industries"))]
    opportunities = [o for o in opportunities if category in clean_list(o.get("categories")) or category in clean_list(o.get("industries"))]
    if not any([reps, companies, opportunities]):
        st.warning("No active marketplace content is available for that category.")
        return
    title_description(
        f"{category} sales representatives and opportunities",
        f"Browse active representative profiles, companies, and product-line opportunities connected to {category}.",
    )
    render_shareable_url("Share this category page", category=slugify(category))
    m1, m2, m3 = st.columns(3)
    m1.metric("Active reps", len(reps))
    m2.metric("Companies", len(companies))
    m3.metric("Opportunities", len(opportunities))
    summary = reviews_summary(all_reviews())
    if reps:
        st.subheader("Representatives")
        for rep in reps[:6]:
            rating, rcount, real = effective_rating(rep, summary)
            rep_card(rep, rep_score(rep, rating), rating, rcount, real, [], None, None)
    if opportunities:
        st.subheader("Opportunities")
        companies_by_id = {str(c.get("id", "")).replace("co-", ""): c for c in all_companies()}
        for opportunity in opportunities[:6]:
            opportunity_card(opportunity, companies_by_id, None)
    if companies:
        st.subheader("Companies")
        for company in companies[:6]:
            company_card(company)


def render_post_opportunity(companies: list[dict]):
    st.subheader("Post Opportunity")
    if SUPABASE_ON and not current_auth_session():
        st.info("Sign in with a company account before posting a live opportunity.")
        return
    if SUPABASE_ON and not can_create_company(current_account_role()):
        st.info("Company or admin accounts can post live opportunities.")
        return
    visible_companies = companies
    if SUPABASE_ON and not is_admin_role(current_account_role(), current_admin_verified()):
        visible_companies = [c for c in companies if str(c.get("owner_user_id") or "") == current_auth_user_id()]
        if not visible_companies:
            st.info("Create a company profile from this account before posting an opportunity.")
            return
    with st.form("post_opportunity"):
        company_options = {f"{c.get('name')} ({c.get('id')})": c for c in visible_companies}
        selected_company_label = st.selectbox("Company profile", [""] + list(company_options.keys()))
        title = st.text_input("Title", placeholder="Independent reps wanted for commercial security line")
        description = st.text_area("Description")
        categories = st.multiselect("Categories", list(CATEGORIES.keys()))
        industries = st.text_input("Industries", placeholder="Comma-separated")
        customer_types = st.text_input("Customer types", placeholder="Comma-separated")
        metros = st.multiselect("Metros", list(METROS.keys()))
        states = st.multiselect("States", US_STATES)
        zip_codes = st.text_input("ZIP codes", placeholder="Comma-separated")
        territory_type = st.selectbox("Territory type", TERRITORY_TYPE_OPTIONS)
        compensation_types = st.multiselect("Compensation", COMPENSATION_TYPE_OPTIONS)
        c1, c2 = st.columns(2)
        commission_min = c1.number_input("Min commission %", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
        commission_max = c2.number_input("Max commission %", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
        recurring_commission = st.checkbox("Recurring commission", value=False)
        exclusive_territory = st.checkbox("Exclusive territory", value=territory_type == "exclusive")
        experience_required = st.slider("Experience required", 0, 20, 0)
        competitor_categories = st.text_input("Direct competitor categories", placeholder="Comma-separated; used for private conflict checks")
        direct_competitors = st.text_input("Direct competitor names", placeholder="Comma-separated; hidden unless marked public")
        competitor_info_public = st.checkbox("Allow competitor names/categories to appear in match explanations", value=False)
        set_expires_at = st.checkbox("Set expiration date", value=False)
        expires_at = None
        if set_expires_at:
            expires_at = st.date_input("Expires at", value=date.today() + timedelta(days=30))
        submitted = st.form_submit_button("Post opportunity")
    if submitted:
        if not selected_company_label:
            st.error("Choose a company profile first.")
        elif not title or not description:
            st.error("Add a title and description.")
        elif commission_min and commission_max and commission_min > commission_max:
            st.error("Min commission cannot be greater than max commission.")
        else:
            company = company_options[selected_company_label]
            raw_company_id = str(company.get("id", ""))
            db_company_id = raw_company_id.replace("co-", "")
            opportunity = {
                "company_id": int(db_company_id) if SUPABASE_ON and db_company_id.isdigit() else raw_company_id,
                "slug": f"{slugify(title)}-{secrets.token_hex(2)}",
                "title": title.strip(),
                "description": description.strip(),
                "categories": categories,
                "industries": clean_list(industries),
                "customer_types": clean_list(customer_types),
                "metros": metros,
                "states": states,
                "zip_codes": clean_list(zip_codes),
                "territory_type": territory_type,
                "compensation_types": compensation_types,
                "commission_min": commission_min if commission_min else None,
                "commission_max": commission_max if commission_max else None,
                "recurring_commission": recurring_commission,
                "exclusive_territory": exclusive_territory,
                "experience_required": int(experience_required or 0),
                "direct_competitors": clean_list(direct_competitors),
                "competitor_categories": clean_list(competitor_categories),
                "competitor_info_public": competitor_info_public,
                "active": True,
                "featured": False,
                "application_count": 0,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "owner_user_id": current_auth_user_id() or None,
            }
            try:
                if SUPABASE_ON:
                    insert_opportunity_user_db(opportunity)
                else:
                    opportunity["id"] = f"local-opp-{len(st.session_state.setdefault('my_opportunities', [])) + 1}"
                    opportunity["companies"] = {"name": company.get("name"), "slug": company.get("slug"), "verified": company.get("verified")}
                    st.session_state.setdefault("my_opportunities", []).append(opportunity)
                st.success("Opportunity posted.")
                st.rerun()
            except Exception as exc:
                st.error(f"Couldn't post opportunity: {exc}")


def render_company_opportunities(companies: list[dict], opportunities: list[dict]):
    st.subheader("Company Opportunities")
    if not companies:
        st.info("Create a company profile before posting or reviewing opportunities.")
        return
    company_options = {f"{c.get('name')} ({c.get('id')})": c for c in companies}
    selected = st.selectbox("Company", list(company_options.keys()), key="company_opps_select")
    company = company_options[selected]
    cid = str(company.get("id", "")).replace("co-", "")
    rows = [o for o in opportunities if str(o.get("company_id", "")).replace("co-", "") == cid]
    if not rows:
        st.info("No opportunities posted for this company yet.")
    companies_by_id = {str(c.get("id", "")).replace("co-", ""): c for c in companies}
    compare_rep = selected_compare_rep()
    for opportunity in rows:
        opportunity_card(opportunity, companies_by_id, compare_rep)


def render_company_directory():
    companies = [c for c in all_companies() if c.get("profile_status") == "active"]
    companies = [c for c in companies if company_matches_directory_filters(c)]
    opportunities = [o for o in all_opportunities() if o.get("active", True) and opportunity_matches_filters(o)]
    companies_by_id = {str(c.get("id", "")).replace("co-", ""): c for c in all_companies()}
    compare_rep = selected_compare_rep()
    tab_browse, tab_detail_companies, tab_post, tab_company_opps, tab_connections = st.tabs([
        "Browse Opportunities",
        "Company Directory",
        "Post Opportunity",
        "Company Opportunities",
        "Connection Requests",
    ])
    with tab_browse:
        st.subheader(f"{len(opportunities)} sales opportunities")
        if not opportunities:
            st.info("No opportunities match those filters yet. Broaden territory, category, compensation, or experience filters.")
        sorted_opps = sorted(
            opportunities,
            key=lambda o: (
                score_opportunity_rep_match(o, compare_rep).score if compare_rep and st.session_state.get("opp_compare_rep_id") else 0,
                bool(o.get("featured")),
                str(o.get("created_at") or ""),
            ),
            reverse=True,
        )
        for opportunity in sorted_opps:
            opportunity_card(opportunity, companies_by_id, compare_rep)
    with tab_detail_companies:
        st.subheader(f"{len(companies)} companies looking for reps")
        if not companies:
            st.info("No company profiles match yet. Try clearing filters or check back after more companies join.")
        for company in companies:
            company_card(company)
        st.divider()
        render_company_create_form()
        render_company_edit_form()
    with tab_post:
        render_post_opportunity(all_companies())
    with tab_company_opps:
        render_company_opportunities(all_companies(), all_opportunities())
    with tab_connections:
        render_company_connection_outbox(all_companies())


# Customer mode renders here and halts before the rep-mode code below.
if not rep_mode:
    company_profile_ref_from_url = st.query_params.get("company", "")
    rep_profile_ref_from_url = st.query_params.get("rep", "")
    opportunity_ref_from_url = st.query_params.get("opportunity", "")
    territory_ref_from_url = st.query_params.get("territory", "")
    category_ref_from_url = st.query_params.get("category", "")
    if opportunity_ref_from_url:
        render_opportunity_detail(opportunity_ref_from_url)
    elif company_profile_ref_from_url:
        render_company_profile_page(company_profile_ref_from_url)
    elif rep_profile_ref_from_url:
        render_rep_profile_page(rep_profile_ref_from_url)
    elif territory_ref_from_url:
        render_public_territory_page(territory_ref_from_url)
    elif category_ref_from_url:
        render_public_category_page(category_ref_from_url)
    elif company_directory_mode:
        render_company_directory()
    elif territory_intelligence_mode:
        render_territory_intelligence_dashboard()
    elif saved_reps_mode:
        render_saved_reps_page()
    elif saved_opportunities_mode:
        render_saved_opportunities_page()
    elif admin_dashboard_mode:
        render_admin_dashboard()
    elif home_mode:
        render_homepage()
    else:
        render_marketplace()
    st.stop()


# ========================= REP MODE (find customers) ======================== #
# ---- Run the search ----
if go:
    if not cats:
        st.sidebar.error("Pick at least one category.")
    else:
        if custom.strip():
            bbox = geocode_area(custom.strip())
            area_label = custom.strip()
            if bbox is None:
                st.error(f"Couldn't locate “{custom}”. Try a more specific name, or pick a preset metro.")
                st.stop()
        else:
            bbox = METROS[metro]
            area_label = metro
        query = build_query(bbox, cats, cap)
        with st.spinner(f"Searching {area_label} via OpenStreetMap…"):
            try:
                elements = fetch_overpass(query)
            except Exception as exc:
                st.error(f"Live data source unavailable right now: {exc}")
                st.stop()
        df = parse_elements(elements, product_profile)
        st.session_state["results"] = df
        st.session_state["score_profile"] = product_profile
        st.session_state["area_label"] = area_label
        st.session_state["bbox"] = bbox

# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #
tab_discover, tab_pipeline = st.tabs(["🧭 Discover", f"📋 My Pipeline ({len(pipe())})"])


def prospect_card(r: pd.Series):
    bid = r["id"]
    entry = pipe().get(bid, {})
    with st.container():
        st.markdown('<div class="prospect">', unsafe_allow_html=True)
        c1, c2 = st.columns([5, 2])
        with c1:
            heat_cls = {"Hot": "b-hot", "Warm": "b-warm", "Cool": "b-cool"}[r["heat"]]
            st.markdown(
                f'<div class="pname">{h(r["name"])}</div>'
                f'<div class="pmeta">{h(r["category"])}'
                + (f' · {h(r["address"])}' if r["address"] else "")
                + "</div>"
                f'<span class="badge {heat_cls}">{h(r["heat"])} · {int(r["score"])}</span>'
                + (f'<span class="badge">📞 {h(r["phone"])}</span>' if r["phone"] else '<span class="badge">No phone</span>')
                + ('<span class="badge b-gap">No website</span>' if not r["website"] else '<span class="badge">🌐 Website</span>')
                + ('<span class="badge">Independent</span>' if r["independent"] else '<span class="badge">Chain</span>'),
                unsafe_allow_html=True,
            )
            with st.expander("Why this score / details"):
                st.write(r.get("insight", ""))
                st.write(r["why"])
                if r["website"]:
                    st.write(f"Website: {r['website']}")
                if r["hours"]:
                    st.write(f"Hours: {r['hours']}")
                st.caption(f"OSM id: {bid} · {r['lat']:.5f}, {r['lon']:.5f}")
        with c2:
            cur = entry.get("stage", "— none —")
            stage = st.selectbox(
                "Stage", STAGES, index=STAGES.index(cur) if cur in STAGES else 0,
                key=f"stage_{bid}", label_visibility="collapsed",
            )
            if stage != cur:
                set_stage(bid, stage, r["name"], r["category"])
                st.rerun()
            note = st.text_input(
                "Note", value=entry.get("note", ""), key=f"note_{bid}",
                placeholder="Call note…", label_visibility="collapsed",
            )
            if note != entry.get("note", ""):
                set_note(bid, note, r["name"], r["category"])
            follow_raw = entry.get("next_follow_up", "")
            try:
                follow_default = datetime.strptime(follow_raw, "%Y-%m-%d").date() if follow_raw else None
            except ValueError:
                follow_default = None
            follow = st.date_input(
                "Next follow-up",
                value=follow_default,
                key=f"follow_{bid}",
                label_visibility="collapsed",
            )
            follow_value = follow.isoformat() if follow else ""
            if follow_value != follow_raw:
                set_follow_up(bid, follow_value, r["name"], r["category"])
            q1, q2, q3 = st.columns(3)
            if q1.button("Contacted today", key=f"ct_{bid}", use_container_width=True):
                mark_contacted(bid, r["name"], r["category"])
                st.rerun()
            if q2.button("+1 day", key=f"fu1_{bid}", use_container_width=True):
                set_follow_up(bid, (date.today() + timedelta(days=1)).isoformat(), r["name"], r["category"])
                st.rerun()
            if q3.button("+1 week", key=f"fu7_{bid}", use_container_width=True):
                set_follow_up(bid, (date.today() + timedelta(days=7)).isoformat(), r["name"], r["category"])
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    out = df[df["score"] >= min_score]
    if only_no_web:
        out = out[out["website"] == ""]
    if indie_only:
        out = out[out["independent"]]
    if heat_filter:
        out = out[out["heat"].isin(heat_filter)]
    if sort_by == "Name (A–Z)":
        out = out.sort_values("name")
    elif sort_by == "Category":
        out = out.sort_values(["category", "score"], ascending=[True, False])
    else:
        out = out.sort_values("score", ascending=False)
    return out


with tab_discover:
    df = st.session_state.get("results")
    if df is not None and st.session_state.get("score_profile") != product_profile:
        df = score_prospects(df, product_profile)
        st.session_state["results"] = df
        st.session_state["score_profile"] = product_profile
    if df is None:
        st.info("👈 Pick a metro (or type any city), choose categories, and hit **Search territory** to pull live prospects.")
        st.markdown(
            "**How lead scores work** — choose what you sell, and the score adjusts around the signals that matter for that sale:\n"
            "- **Marketing/Web** heavily rewards no website / presence gaps.\n"
            "- **Security, POS, Merchant Services** care more about storefronts, phone access, and local operators.\n"
            "- **Payroll/HR and Insurance** add more weight for categories that usually need operational or risk support."
        )
    elif df.empty:
        st.warning("No named businesses found for those categories in this area. Try more categories or a larger metro.")
    else:
        view = apply_filters(df)
        area_label = st.session_state.get("area_label", "")
        st.subheader(f"{area_label} — {len(view)} prospects for {product_profile}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Prospects shown", len(view))
        m2.metric("🔥 Hot leads", int((view["heat"] == "Hot").sum()))
        m3.metric("Presence gaps", int((view["website"] == "").sum()))
        m4.metric("Avg lead score", int(view["score"].mean()) if len(view) else 0)

        if HAVE_PYDECK and len(view):
            colors = {k: v["color"] for k, v in CATEGORIES.items()}
            mp = view.copy()
            mp["color"] = mp["category"].map(colors)
            mp_bbox = st.session_state.get("bbox", METROS[metro])
            layer = pdk.Layer(
                "ScatterplotLayer", data=mp,
                get_position="[lon, lat]", get_fill_color="color",
                get_radius="40 + score * 3", radius_min_pixels=4, radius_max_pixels=22,
                pickable=True, opacity=0.8,
            )
            view_state = pdk.ViewState(
                latitude=(mp_bbox[0] + mp_bbox[2]) / 2,
                longitude=(mp_bbox[1] + mp_bbox[3]) / 2,
                zoom=10.5,
            )
            st.pydeck_chart(pdk.Deck(
                layers=[layer], initial_view_state=view_state,
                map_style=None, tooltip={"text": "{name}\n{category} · score {score}"},
            ), use_container_width=True)

        st.divider()
        for _, r in view.iterrows():
            prospect_card(r)


with tab_pipeline:
    p = pipe()
    owner_email = normalize_owner_email(st.session_state.get("pipeline_owner_email", ""))
    owner_code = st.session_state.get("pipeline_access_code", "")
    owner_key_hash = _hash_code(owner_code) if owner_code else ""
    auth_user = None
    auth_token = st.session_state.get("supabase_auth_token", "")
    if auth_token and LIVE_WRITES_ON:
        try:
            auth_user = get_auth_user(auth_token)
        except Exception as exc:
            st.caption(f"Auth token check failed: {exc}")
    owner_user_id = (auth_user or {}).get("id", "")
    if auth_user and not owner_email:
        owner_email = normalize_owner_email(auth_user.get("email", ""))
    sync_ready = bool(LIVE_WRITES_ON and ((owner_user_id and owner_email) or (owner_email and owner_key_hash)))
    if SUPABASE_ON and SUPABASE_SERVICE_KEY:
        s1, s2, s3 = st.columns([2, 1, 1])
        s1.caption("Pipeline sync uses Supabase Auth when a token is supplied; otherwise it uses email + private code.")
        if s2.button("Load pipeline", use_container_width=True, disabled=not sync_ready):
            try:
                rows = fetch_pipeline_db(owner_email, owner_key_hash, owner_user_id)
                st.session_state["pipe"] = {
                    str(r["prospect_id"]): {
                        "name": r.get("name", ""),
                        "category": r.get("category", ""),
                        "stage": r.get("stage", "New lead"),
                        "note": r.get("note", "") or "",
                        "next_follow_up": r.get("next_follow_up") or "",
                        "last_contacted": r.get("last_contacted") or "",
                        "call_attempts": r.get("call_attempts") or 0,
                        "outcome": r.get("outcome", "") or "",
                    }
                    for r in rows
                }
                st.success(f"Loaded {len(rows)} pipeline rows.")
                st.rerun()
            except Exception as exc:
                st.error(f"Couldn't load pipeline: {exc}")
        if s3.button("Save pipeline", use_container_width=True, disabled=not sync_ready or not p):
            try:
                save_pipeline_db(owner_email, owner_key_hash, p, owner_user_id)
                st.success("Pipeline saved.")
            except Exception as exc:
                st.error(f"Couldn't save pipeline: {exc}")
    elif SUPABASE_ON:
        st.caption("Pipeline sync needs `[supabase].service_key`; without it, this browser-session pipeline still works.")
    if not p:
        st.info("Move prospects into stages from the Discover tab and they'll collect here.")
    else:
        prows = [{"id": k, **v} for k, v in p.items()]
        pdf = pd.DataFrame(prows)
        # Entries may carry only a stage or only a note, so guarantee every column
        # exists (and has no NaN) before we select/sort on them.
        for _col in ["name", "category", "stage", "note", "next_follow_up", "last_contacted", "call_attempts", "outcome"]:
            if _col not in pdf.columns:
                pdf[_col] = ""
        pdf["stage"] = pdf["stage"].fillna("")
        pdf["note"] = pdf["note"].fillna("")
        pdf["next_follow_up"] = pdf["next_follow_up"].fillna("")
        pdf["last_contacted"] = pdf["last_contacted"].fillna("")
        pdf["call_attempts"] = pdf["call_attempts"].fillna(0).astype(int)
        today = date.today().isoformat()
        due = pdf[pdf["next_follow_up"].astype(str) == today]
        overdue = pdf[(pdf["next_follow_up"].astype(str) != "") & (pdf["next_follow_up"].astype(str) < today)]
        order = {s: i for i, s in enumerate(STAGES)}
        pdf["ord"] = pdf["stage"].map(lambda s: order.get(s, 99))
        pdf = pdf.sort_values(["next_follow_up", "ord"], na_position="last").drop(columns="ord")
        st.subheader(f"{len(pdf)} businesses in your pipeline")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Today's Calls", len(due))
        k2.metric("Overdue", len(overdue))
        k3.metric("New Leads", int((pdf["stage"] == "New lead").sum()))
        k4.metric("Qualified", int((pdf["stage"] == "Qualified").sum()))
        if len(due) or len(overdue):
            with st.expander("Today's call list", expanded=True):
                call_list = pd.concat([overdue, due]).drop_duplicates(subset="id")
                st.dataframe(
                    call_list[["name", "category", "stage", "next_follow_up", "last_contacted", "call_attempts", "note"]].rename(
                        columns={"name": "Business", "category": "Category", "stage": "Stage",
                                 "next_follow_up": "Next Follow-up", "last_contacted": "Last Contacted",
                                 "call_attempts": "Attempts", "note": "Note"}
                    ),
                    use_container_width=True, hide_index=True,
                )
        st.dataframe(
            pdf[["name", "category", "stage", "next_follow_up", "last_contacted", "call_attempts", "note"]].rename(
                columns={"name": "Business", "category": "Category", "stage": "Stage",
                         "next_follow_up": "Next Follow-up", "last_contacted": "Last Contacted",
                         "call_attempts": "Attempts", "note": "Note"}
            ),
            use_container_width=True, hide_index=True,
        )
        csv = pdf.to_csv(index=False).encode("utf-8")
        c1, c2 = st.columns(2)
        c1.download_button(
            "⬇️ Export pipeline (CSV)", csv,
            file_name=f"pipeline_{datetime.now():%Y%m%d}.csv", mime="text/csv",
            use_container_width=True,
        )
        with c2:
            up = st.file_uploader("⬆️ Import pipeline CSV", type="csv", label_visibility="collapsed")
            if up is not None:
                try:
                    imp = pd.read_csv(io.BytesIO(up.read()))
                    for _, r in imp.iterrows():
                        pipe()[str(r["id"])] = {
                            "name": r.get("name", ""), "category": r.get("category", ""),
                            "stage": r.get("stage", "New lead"),
                            "note": "" if pd.isna(r.get("note", "")) else str(r.get("note", "")),
                            "next_follow_up": "" if pd.isna(r.get("next_follow_up", "")) else str(r.get("next_follow_up", "")),
                            "last_contacted": "" if pd.isna(r.get("last_contacted", "")) else str(r.get("last_contacted", "")),
                            "call_attempts": 0 if pd.isna(r.get("call_attempts", 0)) else int(r.get("call_attempts", 0)),
                            "outcome": "" if pd.isna(r.get("outcome", "")) else str(r.get("outcome", "")),
                        }
                    st.success(f"Imported {len(imp)} rows.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Couldn't read that CSV: {exc}")

st.caption(
    "Live business data © OpenStreetMap contributors, via the Overpass API. "
    "Coverage and detail vary by area; ratings/reviews aren't part of OSM."
)
