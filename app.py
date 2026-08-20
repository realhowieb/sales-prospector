"""
Territory Prospector — find new business customers by area & category.

Live data source: OpenStreetMap via the public Overpass API (no API key, no billing).
Lead scores are computed from real listing signals (reachability + digital-presence gap).
Pipeline stages & notes live in the browser session; export/import as CSV to keep them.
"""
from __future__ import annotations

import io
import re
import secrets
import smtplib
import time
from datetime import date, datetime
from email.message import EmailMessage
from urllib.parse import urlencode

import pandas as pd
import requests
import streamlit as st

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
              "service_lon", "service_radius_miles"]
LEAD_FIELDS = ["rep_id", "rep_company", "rep_email", "customer_name",
               "customer_email", "customer_phone", "message", "category", "metro",
               "review_token_hash"]
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


def _sb_headers(extra: dict | None = None) -> dict:
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def _sb_check(r):
    """Raise a human-readable error (with Supabase's own message + a hint) on failure."""
    if r.status_code >= 400:
        hint = ""
        if r.status_code == 404:
            hint = (" — that table wasn't found in your Supabase project. Re-run the latest "
                    "supabase_setup.sql (it creates BOTH the `reps` and `leads` tables), then "
                    "retry. Also confirm the `url` secret is your Project URL.")
        elif r.status_code in (401, 403):
            hint = (" — check the configured Supabase key and the RLS policies from "
                    "supabase_setup.sql. Public writes now require the service_role key.")
        raise RuntimeError(f"Supabase {r.status_code}: {r.text[:300]}{hint}")


@st.cache_data(ttl=45, show_spinner=False)
def fetch_reps_db() -> list[dict]:
    """Read every listing from the shared Supabase table."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/reps", headers=_sb_headers(),
        params={"select": "*", "order": "created_at.desc"}, timeout=20,
    )
    _sb_check(r)
    rows = r.json()
    for row in rows:
        row["id"] = f"db-{row.get('id')}"
        row["categories"] = row.get("categories") or []
        row["metros"] = row.get("metros") or []
        row["rating"] = row.get("rating") or 0.0
        row["reviews"] = row.get("reviews") or 0
        row["is_sample"] = bool(row.get("is_sample", False))
        row["service_radius_miles"] = row.get("service_radius_miles") or 25
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


def all_reps() -> list[dict]:
    """Live shared marketplace from Supabase when configured; else demo (seed + session)."""
    if SUPABASE_ON:
        try:
            return fetch_reps_db()
        except Exception as exc:
            st.warning(f"Marketplace database unreachable ({exc}). Showing sample reps for now.")
            return REPS_SEED
    return REPS_SEED + st.session_state.setdefault("my_reps", [])


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


def fetch_leads_db(rep_email: str) -> list[dict]:
    """Read a rep's leads. Requires the service_role key (leads aren't publicly readable)."""
    r = requests.get(f"{SUPABASE_URL}/rest/v1/leads", headers=_sb_service_headers(),
                     params={"select": "*", "rep_email": f"eq.{rep_email}",
                             "order": "created_at.desc"}, timeout=20)
    _sb_check(r)
    return r.json()


def fetch_pipeline_db(owner_email: str, owner_key_hash: str) -> list[dict]:
    """Read a rep's private pipeline. Requires the service_role key."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/pipeline_entries",
        headers=_sb_service_headers(),
        params={"select": "*", "owner_email": f"eq.{owner_email}",
                "owner_key_hash": f"eq.{owner_key_hash}", "order": "updated_at.desc"},
        timeout=20,
    )
    _sb_check(r)
    return r.json()


def save_pipeline_db(owner_email: str, owner_key_hash: str, entries: dict):
    """Upsert pipeline rows for one rep. Requires a unique(owner_email, prospect_id) index."""
    payload = build_pipeline_payload(owner_email, owner_key_hash, entries)
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
    lead = {
        "rep_id": str(rep.get("id", "")), "rep_company": rep.get("company", ""),
        "rep_email": rep.get("email", ""), "customer_name": name,
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
        emailed = send_lead_email(rep, lead)
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
    """rep_id -> {avg, count, recent[]}."""
    agg: dict = {}
    for rv in reviews:
        if rv.get("verified") is False:
            continue
        rid = str(rv.get("rep_id", ""))
        a = agg.setdefault(rid, {"sum": 0, "count": 0, "recent": []})
        try:
            a["sum"] += int(rv.get("rating", 0))
            a["count"] += 1
        except (TypeError, ValueError):
            continue
        if len(a["recent"]) < 3 and rv.get("comment"):
            a["recent"].append(rv)
    for a in agg.values():
        a["avg"] = round(a["sum"] / a["count"], 1) if a["count"] else 0.0
    return agg


def effective_rating(rep: dict, summary: dict):
    """Real review average when reviews exist, else the listing's own (seed) value."""
    a = summary.get(str(rep.get("id", "")))
    if a and a["count"] > 0:
        return a["avg"], a["count"], True
    return float(rep.get("rating", 0) or 0), int(rep.get("reviews", 0) or 0), False


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


def insert_review(rep: dict, rating: int, name: str, comment: str, token: str = ""):
    rec = {"rep_id": str(rep.get("id", "")), "rating": int(rating),
           "customer_name": name or "Anonymous", "comment": comment or "",
           "verified": False}
    if LIVE_WRITES_ON:
        lead = fetch_review_lead(token)
        if not lead:
            raise RuntimeError("That review link is invalid.")
        if lead.get("review_token_used_at"):
            raise RuntimeError("That review link has already been used.")
        if str(lead.get("rep_id")) != str(rep.get("id", "")):
            raise RuntimeError("That review link is for a different rep.")
        rec["lead_id"] = lead.get("id")
        rec["verified"] = True
        r = requests.post(f"{SUPABASE_URL}/rest/v1/reviews",
                          headers=_sb_service_headers({"Prefer": "return=minimal"}),
                          json=rec, timeout=20)
        _sb_check(r)
        mark_review_token_used(lead.get("id"))
        fetch_reviews_db.clear()
    else:
        rec["verified"] = True
        st.session_state.setdefault("session_reviews", []).append(rec)


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


def rate_limited() -> bool:
    return st.session_state.get("signups_this_session", 0) >= 3


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
    r = requests.get(f"{SUPABASE_URL}/rest/v1/reps", headers=_sb_headers(),
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
st.set_page_config(page_title=APP_TITLE, page_icon="📍", layout="wide")

review_token_from_url = st.query_params.get("review_token", "")

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
      .repname {font-size:1.1rem; font-weight:700; line-height:1.15;}
      .repco {color:#7d8a86; font-size:.82rem; margin-bottom:2px;}
      .deal {margin:8px 0 4px; padding:9px 12px; border-radius:9px; font-weight:600; font-size:.9rem;
             background:linear-gradient(90deg,#c9781f22,#0e5a5411); border:1px solid #c9781f44; color:inherit;}
      .deal b {color:#c9781f;}
      .matchbox {text-align:center; border:1px solid rgba(128,128,128,.25); border-radius:12px; padding:8px 4px;}
      .matchnum {font-size:1.7rem; font-weight:800; line-height:1; color:#0e5a54;}
      .matchlbl {font-size:.62rem; letter-spacing:.1em; text-transform:uppercase; color:#7d8a86;}
      .stars {color:#b67a1e; letter-spacing:1px;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---- Audience switch (who's using the app right now) ----
with st.sidebar:
    audience = st.radio(
        "I am a…",
        ["🧭 Sales rep — find customers", "🛍️ Customer — find a rep & deals"],
        label_visibility="collapsed",
    )
rep_mode = audience.startswith("🧭")

if rep_mode:
    st.title("📍 Territory Prospector")
    st.caption("Find new business customers by area & category — live data from OpenStreetMap, no API key required.")
else:
    st.title("🛍️ Find a Rep · Best Deals")
    st.caption("Tell us what you need and where — matched reps compete on their offer, rating, and response time.")

# ---- Sidebar controls (per audience) ----
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
    else:
        st.header("What do you need?")
        cust_category = st.selectbox("I'm looking for", ["Any category"] + list(CATEGORIES.keys()))
        cust_metro = st.selectbox("My area", ["Anywhere"] + list(METROS.keys()))
        cust_area = st.text_input("…or city / ZIP + radius", placeholder="e.g. 95117 or Palo Alto, CA")
        cust_radius = st.slider("Search radius", 5, 100, 25, step=5)
        cust_min_rating = st.slider("Minimum rating", 0.0, 5.0, 0.0, step=0.5)
        cust_sort = st.selectbox("Rank by", ["Best match", "Best deal", "Top rated", "Fastest response"])

# --------------------------------------------------------------------------- #
# Customer mode: find a rep + best deals
# --------------------------------------------------------------------------- #
def rep_card(rep: dict, score: int, rating: float, rcount: int, real: bool, recent: list):
    n = round(rating)
    stars = "★" * n + "☆" * (5 - n)
    cats_html = " · ".join(h(c) for c in rep["categories"])
    metros_html = ", ".join(h(m) for m in rep["metros"])
    is_new = rcount == 0
    rating_txt = "Unrated" if is_new else f'{rating:.1f} ({rcount})'
    with st.container():
        st.markdown('<div class="prospect">', unsafe_allow_html=True)
        c1, c2 = st.columns([5, 2])
        with c1:
            badges = ""
            if rep.get("verified"):
                badges += '<span class="badge b-verified">✓ Verified</span>'
            if rep.get("is_sample"):
                badges += '<span class="badge">Sample listing</span>'
            if is_new:
                badges += '<span class="badge b-new">New listing</span>'
            st.markdown(
                f'<div class="repname">{h(rep["company"])}</div>'
                f'<div class="repco">{h(rep["name"])} · {cats_html}</div>'
                f'{badges}'
                f'<span class="badge">📍 {metros_html}</span>'
                f'<span class="badge"><span class="stars">{stars}</span> {rating_txt}</span>'
                f'<span class="badge">⏱ {h(rep["response"])}</span>'
                f'<div class="deal">🏷️ <b>Deal:</b> {h(rep["deal"])}</div>'
                f'<div class="pmeta">{h(rep.get("blurb", ""))}</div>',
                unsafe_allow_html=True,
            )
            with st.expander("📨 Request an intro / claim this deal"):
                st.caption(f"Send your details to {rep['name']} at {rep['company']} — they'll reach out directly.")
                with st.form(f"lead_{rep['id']}", clear_on_submit=True):
                    ln = st.text_input("Your name", key=f"ln_{rep['id']}")
                    lc1, lc2 = st.columns(2)
                    le = lc1.text_input("Your email", key=f"le_{rep['id']}")
                    lp = lc2.text_input("Phone (optional)", key=f"lp_{rep['id']}")
                    lm = st.text_area("What do you need?", key=f"lm_{rep['id']}",
                                      placeholder="One line on what you're looking for…")
                    sent = st.form_submit_button("Send my request")
                if sent:
                    if not ln or not (le or lp):
                        st.error("Add your name and an email or phone so the rep can reply.")
                    else:
                        submit_lead(rep, ln.strip(), le.strip(), lp.strip(), lm.strip())
            review_label = f"⭐ Verified reviews ({rcount}) · leave a review"
            with st.expander(review_label):
                if recent:
                    for rv in recent:
                        rn_ = int(rv.get("rating", 0) or 0)
                        st.markdown(
                            f'<span class="stars">{"★" * rn_}{"☆" * (5 - rn_)}</span> '
                            f'**{h(rv.get("customer_name") or "Anonymous")}** — '
                            f'{h(rv.get("comment") or "")}',
                            unsafe_allow_html=True,
                        )
                elif not is_new:
                    st.caption("No written reviews yet.")
                with st.form(f"rev_{rep['id']}", clear_on_submit=True):
                    rr = st.slider("Your rating", 1, 5, 5, key=f"rr_{rep['id']}")
                    rn = st.text_input("Your name", key=f"rn_{rep['id']}")
                    rc = st.text_area("Comment (optional)", key=f"rc_{rep['id']}")
                    rt = ""
                    if LIVE_WRITES_ON:
                        rt = st.text_input(
                            "Verified review token",
                            value=review_token_from_url,
                            key=f"rt_{rep['id']}",
                            help="Customers receive a one-time token after requesting an intro.",
                        )
                    rsent = st.form_submit_button("Submit review")
                if rsent:
                    try:
                        if LIVE_WRITES_ON and not rt.strip():
                            st.error("Use the verified review link from your intro request email.")
                            st.stop()
                        insert_review(rep, rr, rn.strip(), rc.strip(), rt.strip())
                        st.success("Thanks — your review is in.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Couldn't save review: {exc}")
        with c2:
            st.markdown(
                f'<div class="matchbox"><div class="matchnum">{score}</div>'
                f'<div class="matchlbl">Match score</div></div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)


def rep_matches_customer_area(rep: dict, customer_center: tuple[float, float] | None) -> bool:
    if customer_center:
        try:
            rep_lat = float(rep.get("service_lat"))
            rep_lon = float(rep.get("service_lon"))
            rep_radius = float(rep.get("service_radius_miles") or 25)
            distance = miles_between(customer_center[0], customer_center[1], rep_lat, rep_lon)
            return distance <= rep_radius + cust_radius
        except (TypeError, ValueError):
            return False
    return cust_metro == "Anywhere" or cust_metro in rep["metros"]


def render_marketplace():
    roster = all_reps()
    summary = reviews_summary(all_reviews())
    customer_center = None
    customer_area_label = cust_metro
    if cust_area.strip():
        bbox = geocode_area(cust_area.strip())
        customer_area_label = f"{cust_area.strip()} + {cust_radius} miles"
        if bbox:
            customer_center = bbox_center(bbox)
        else:
            st.warning(f"Couldn't locate {cust_area.strip()}. Showing metro/anywhere matches instead.")
    matched = []
    for rep in roster:
        if rep.get("active", True) is False:   # paused listings hidden from customers
            continue
        if cust_category != "Any category" and cust_category not in rep["categories"]:
            continue
        if not rep_matches_customer_area(rep, customer_center):
            continue
        rating, rcount, real = effective_rating(rep, summary)
        if rating < cust_min_rating:
            continue
        recent = summary.get(str(rep.get("id", "")), {}).get("recent", [])
        matched.append((rep, rep_score(rep, rating), rating, rcount, real, recent))

    if cust_sort == "Best deal":
        matched.sort(key=lambda x: x[0].get("deal_strength", 0), reverse=True)
    elif cust_sort == "Top rated":
        matched.sort(key=lambda x: (x[2], x[3]), reverse=True)
    elif cust_sort == "Fastest response":
        matched.sort(key=lambda x: RESPONSE_HOURS.get(x[0].get("response"), 24))
    else:  # Best match
        matched.sort(key=lambda x: x[1], reverse=True)

    want = "any service" if cust_category == "Any category" else cust_category
    where = "any area" if customer_area_label == "Anywhere" else customer_area_label
    st.caption("🟢 Live marketplace — reps below are shared database listings. Sample listings are labeled."
               if SUPABASE_ON else
               "🟡 Demo mode — sample reps + this-browser listings. Connect Supabase to go live (see README).")
    if SUPABASE_ON and not SUPABASE_SERVICE_KEY:
        st.warning("Live marketplace is read-only until `[supabase].service_key` is configured for server-side submissions.")
    st.subheader(f"{len(matched)} reps competing for your business · {want} · {where}")

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
        st.info("No reps match yet. Try **Any category** / **Anywhere**, or lower the minimum rating.")
    else:
        fast = sum(1 for m in matched if RESPONSE_HOURS.get(m[0]["response"], 24) <= 2)
        m1, m2, m3 = st.columns(3)
        m1.metric("Reps matched", len(matched))
        m2.metric("Top match score", matched[0][1])
        m3.metric("⏱ Reply within ~2 hrs", fast)
        st.divider()
        for rep, sc, rating, rcount, real, recent in matched:
            rep_card(rep, sc, rating, rcount, real, recent)

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
            if SUPABASE_ON and not LIVE_WRITES_ON:
                st.error("Live listing submissions need `[supabase].service_key` configured.")
            elif not (f_name and f_company and f_cats and (f_metros or f_service_area) and f_deal and f_email):
                st.error("Fill name, company, email, at least one category, a territory or service-area center, and your deal.")
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
                    }
                    if SUPABASE_ON:
                        new_rep["edit_code_hash"] = _hash_code(code)
                        try:
                            insert_reps_db([new_rep])
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
                e_blurb = st.text_input("Description", value=mrep.get("blurb", ""), key="ed_blurb")
                b1, b2, b3 = st.columns(3)
                if b1.button("💾 Save changes"):
                    try:
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
                                                   "service_radius_miles": e_service_radius})
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

    reqs = st.session_state.get("intro_requests", [])
    if reqs:
        st.divider()
        st.caption("Requests you've sent this session: " + ", ".join(r["company"] for r in reqs[-6:]))


# Customer mode renders here and halts before the rep-mode code below.
if not rep_mode:
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
    sync_ready = bool(LIVE_WRITES_ON and owner_email and owner_key_hash)
    if SUPABASE_ON and SUPABASE_SERVICE_KEY:
        s1, s2, s3 = st.columns([2, 1, 1])
        s1.caption("Supabase pipeline sync uses the rep email + private pipeline code in the sidebar.")
        if s2.button("Load pipeline", use_container_width=True, disabled=not sync_ready):
            try:
                rows = fetch_pipeline_db(owner_email, owner_key_hash)
                st.session_state["pipe"] = {
                    str(r["prospect_id"]): {
                        "name": r.get("name", ""),
                        "category": r.get("category", ""),
                        "stage": r.get("stage", "New lead"),
                        "note": r.get("note", "") or "",
                        "next_follow_up": r.get("next_follow_up") or "",
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
                save_pipeline_db(owner_email, owner_key_hash, p)
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
        for _col in ["name", "category", "stage", "note", "next_follow_up", "outcome"]:
            if _col not in pdf.columns:
                pdf[_col] = ""
        pdf["stage"] = pdf["stage"].fillna("")
        pdf["note"] = pdf["note"].fillna("")
        pdf["next_follow_up"] = pdf["next_follow_up"].fillna("")
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
                    call_list[["name", "category", "stage", "next_follow_up", "note"]].rename(
                        columns={"name": "Business", "category": "Category", "stage": "Stage",
                                 "next_follow_up": "Next Follow-up", "note": "Note"}
                    ),
                    use_container_width=True, hide_index=True,
                )
        st.dataframe(
            pdf[["name", "category", "stage", "next_follow_up", "note"]].rename(
                columns={"name": "Business", "category": "Category", "stage": "Stage",
                         "next_follow_up": "Next Follow-up", "note": "Note"}
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
