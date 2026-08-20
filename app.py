"""
Territory Prospector — find new business customers by area & category.

Live data source: OpenStreetMap via the public Overpass API (no API key, no billing).
Lead scores are computed from real listing signals (reachability + digital-presence gap).
Pipeline stages & notes live in the browser session; export/import as CSV to keep them.
"""
from __future__ import annotations

import io
import time
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

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


def lead_score(row: dict) -> tuple[int, list[str]]:
    """
    Transparent score from *real* listing signals a rep can act on:
      + reachable (has phone)            35
      + opportunity (NO website)         35   <- the classic "needs a web/marketing presence" angle
      + locatable for a visit (address)  15
      + independent (not a chain)        15
    """
    score, why = 0, []
    if row["phone"]:
        score += 35; why.append("Has phone (reachable) +35")
    else:
        why.append("No phone listed")
    if not row["website"]:
        score += 35; why.append("No website — presence gap +35")
    else:
        why.append("Already has a website")
    if row["address"]:
        score += 15; why.append("Street address (visitable) +15")
    if row["independent"]:
        score += 15; why.append("Independent, not a chain +15")
    else:
        why.append("Looks like a chain/brand")
    return min(score, 100), why


def heat_of(score: int) -> str:
    return "Hot" if score >= 66 else "Warm" if score >= 40 else "Cool"


def parse_elements(elements: list[dict]) -> pd.DataFrame:
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
        score, why = lead_score(row)
        row["score"] = score
        row["heat"] = heat_of(score)
        row["why"] = " · ".join(why)
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


def all_reps() -> list[dict]:
    """Seed roster plus any reps added in this browser session."""
    return REPS_SEED + st.session_state.setdefault("my_reps", [])


def rep_score(rep: dict) -> int:
    """Best-match score (0–100): deal strength (40) + rating (35) + response speed (25)."""
    deal = rep.get("deal_strength", 0) * 40
    rating = ((rep.get("rating", 4.0) - 3.0) / 2.0) * 35
    hrs = RESPONSE_HOURS.get(rep.get("response", "Within 24 hrs"), 24)
    resp = (1 - min(hrs, 48) / 48) * 25
    return int(max(0, min(100, round(deal + rating + resp))))


def log_intro_request(rep: dict):
    reqs = st.session_state.setdefault("intro_requests", [])
    reqs.append({"rep": rep["name"], "company": rep["company"], "when": datetime.now().strftime("%Y-%m-%d %H:%M")})


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
st.set_page_config(page_title=APP_TITLE, page_icon="📍", layout="wide")

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
    else:
        st.header("What do you need?")
        cust_category = st.selectbox("I'm looking for", ["Any category"] + list(CATEGORIES.keys()))
        cust_metro = st.selectbox("My area", ["Anywhere"] + list(METROS.keys()))
        cust_min_rating = st.slider("Minimum rating", 0.0, 5.0, 0.0, step=0.5)
        cust_sort = st.selectbox("Rank by", ["Best match", "Best deal", "Top rated", "Fastest response"])

# --------------------------------------------------------------------------- #
# Customer mode: find a rep + best deals
# --------------------------------------------------------------------------- #
def rep_card(rep: dict, score: int):
    n = round(rep.get("rating", 0))
    stars = "★" * n + "☆" * (5 - n)
    cats_html = " · ".join(rep["categories"])
    metros_html = ", ".join(rep["metros"])
    is_new = rep.get("reviews", 0) == 0
    with st.container():
        st.markdown('<div class="prospect">', unsafe_allow_html=True)
        c1, c2 = st.columns([5, 2])
        with c1:
            badges = ""
            if rep.get("verified"):
                badges += '<span class="badge b-verified">✓ Verified</span>'
            if is_new:
                badges += '<span class="badge b-new">New listing</span>'
            rating_txt = "Unrated" if is_new else f'{rep["rating"]:.1f} ({rep["reviews"]})'
            st.markdown(
                f'<div class="repname">{rep["company"]}</div>'
                f'<div class="repco">{rep["name"]} · {cats_html}</div>'
                f'{badges}'
                f'<span class="badge">📍 {metros_html}</span>'
                f'<span class="badge"><span class="stars">{stars}</span> {rating_txt}</span>'
                f'<span class="badge">⏱ {rep["response"]}</span>'
                f'<div class="deal">🏷️ <b>Deal:</b> {rep["deal"]}</div>'
                f'<div class="pmeta">{rep.get("blurb", "")}</div>',
                unsafe_allow_html=True,
            )
            with st.expander("📇 Contact & request an intro"):
                st.write(f"**{rep['name']}** — {rep['company']}")
                st.write(f"✉️ {rep['email']}")
                st.write(f"📞 {rep['phone']}")
                if st.button("Request an intro", key=f"req_{rep['id']}"):
                    log_intro_request(rep)
                    st.success("Intro requested — saved in-app. (No email is actually sent in this demo.)")
        with c2:
            st.markdown(
                f'<div class="matchbox"><div class="matchnum">{score}</div>'
                f'<div class="matchlbl">Match score</div></div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)


def render_marketplace():
    matched = []
    for rep in all_reps():
        if cust_category != "Any category" and cust_category not in rep["categories"]:
            continue
        if cust_metro != "Anywhere" and cust_metro not in rep["metros"]:
            continue
        if rep.get("rating", 0) < cust_min_rating:
            continue
        matched.append((rep, rep_score(rep)))

    if cust_sort == "Best deal":
        matched.sort(key=lambda x: x[0].get("deal_strength", 0), reverse=True)
    elif cust_sort == "Top rated":
        matched.sort(key=lambda x: x[0].get("rating", 0), reverse=True)
    elif cust_sort == "Fastest response":
        matched.sort(key=lambda x: RESPONSE_HOURS.get(x[0].get("response"), 24))
    else:  # Best match
        matched.sort(key=lambda x: x[1], reverse=True)

    want = "any service" if cust_category == "Any category" else cust_category
    where = "any area" if cust_metro == "Anywhere" else cust_metro
    st.subheader(f"{len(matched)} reps competing for your business · {want} · {where}")

    if not matched:
        st.info("No reps match yet. Try **Any category** / **Anywhere**, or lower the minimum rating.")
    else:
        fast = sum(1 for rep, _ in matched if RESPONSE_HOURS.get(rep["response"], 24) <= 2)
        m1, m2, m3 = st.columns(3)
        m1.metric("Reps matched", len(matched))
        m2.metric("Top match score", matched[0][1])
        m3.metric("⏱ Reply within ~2 hrs", fast)
        st.divider()
        for rep, sc in matched:
            rep_card(rep, sc)

    st.divider()
    with st.expander("🙋 List yourself as a rep — get found by customers"):
        with st.form("list_rep", clear_on_submit=True):
            colA, colB = st.columns(2)
            f_name = colA.text_input("Your name")
            f_company = colB.text_input("Company")
            f_cats = st.multiselect("Categories you serve", list(CATEGORIES.keys()))
            f_metros = st.multiselect("Territories you cover", list(METROS.keys()))
            f_deal = st.text_input("Your headline deal", placeholder="e.g. 20% off first order")
            f_strength = st.slider("How strong is this offer?", 0.0, 1.0, 0.5, step=0.05,
                                   help="Ranks you on 'best deal'. 1.0 = a standout offer.")
            f_resp = st.selectbox("Typical response time", RESPONSE_OPTS, index=2)
            colC, colD = st.columns(2)
            f_email = colC.text_input("Contact email")
            f_phone = colD.text_input("Contact phone")
            submitted = st.form_submit_button("➕ Add my listing")
        if submitted:
            if not (f_name and f_company and f_cats and f_metros and f_deal):
                st.error("Please fill name, company, at least one category & territory, and your deal.")
            else:
                mine = st.session_state.setdefault("my_reps", [])
                mine.append({
                    "id": f"me-{len(mine) + 1}", "name": f_name, "company": f_company,
                    "categories": f_cats, "metros": f_metros, "deal": f_deal,
                    "deal_strength": f_strength, "rating": 0.0, "reviews": 0,
                    "response": f_resp, "verified": False, "blurb": "New rep listing.",
                    "email": f_email or "—", "phone": f_phone or "—",
                })
                st.success(f"You're listed! Customers searching {', '.join(f_cats)} can now find {f_company}.")
                st.rerun()
        st.caption(
            "Listings added here are visible in **this browser session** only. To make the "
            "marketplace shared across all visitors, connect a datastore (Google Sheets or "
            "Supabase) — see the README."
        )

    reqs = st.session_state.get("intro_requests", [])
    if reqs:
        st.divider()
        st.caption("Intros requested this session: " + ", ".join(r["company"] for r in reqs[-6:]))


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
        df = parse_elements(elements)
        st.session_state["results"] = df
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
                f'<div class="pname">{r["name"]}</div>'
                f'<div class="pmeta">{r["category"]}'
                + (f' · {r["address"]}' if r["address"] else "")
                + "</div>"
                f'<span class="badge {heat_cls}">{r["heat"]} · {r["score"]}</span>'
                + (f'<span class="badge">📞 {r["phone"]}</span>' if r["phone"] else '<span class="badge">No phone</span>')
                + ('<span class="badge b-gap">No website</span>' if not r["website"] else '<span class="badge">🌐 Website</span>')
                + ('<span class="badge">Independent</span>' if r["independent"] else '<span class="badge">Chain</span>'),
                unsafe_allow_html=True,
            )
            with st.expander("Why this score / details"):
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
    if df is None:
        st.info("👈 Pick a metro (or type any city), choose categories, and hit **Search territory** to pull live prospects.")
        st.markdown(
            "**How lead scores work** — they reward the businesses easiest to *win*, from real listing signals:\n"
            "- **Has a phone** → reachable (+35)\n"
            "- **No website** → a presence gap you can help close (+35)\n"
            "- **Street address** → you can plan a visit (+15)\n"
            "- **Independent, not a chain** → a real local decision-maker (+15)"
        )
    elif df.empty:
        st.warning("No named businesses found for those categories in this area. Try more categories or a larger metro.")
    else:
        view = apply_filters(df)
        area_label = st.session_state.get("area_label", "")
        st.subheader(f"{area_label} — {len(view)} prospects")
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
    if not p:
        st.info("Move prospects into stages from the Discover tab and they'll collect here.")
    else:
        prows = [{"id": k, **v} for k, v in p.items()]
        pdf = pd.DataFrame(prows)
        order = {s: i for i, s in enumerate(STAGES)}
        pdf["ord"] = pdf["stage"].map(lambda s: order.get(s, 99))
        pdf = pdf.sort_values("ord").drop(columns="ord")
        st.subheader(f"{len(pdf)} businesses in your pipeline")
        cols = st.columns(len(STAGE_COLORS))
        for col, stage in zip(cols, STAGE_COLORS):
            col.metric(stage, int((pdf["stage"] == stage).sum()))
        st.dataframe(
            pdf[["name", "category", "stage", "note"]].rename(
                columns={"name": "Business", "category": "Category", "stage": "Stage", "note": "Note"}
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
                        }
                    st.success(f"Imported {len(imp)} rows.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Couldn't read that CSV: {exc}")

st.caption(
    "Live business data © OpenStreetMap contributors, via the Overpass API. "
    "Coverage and detail vary by area; ratings/reviews aren't part of OSM."
)
