"""Discover: businesses in an area by category, from OpenStreetMap (no key, no billing).

Framework-free so both the API and (later) the Streamlit app can share it. Scoring comes
from prospecting_core.lead_score.
"""
from __future__ import annotations

import time

from prospecting_core import heat_of, lead_score, sales_insight

USER_AGENT = "FirstKnock/1.0 (sales prospecting; https://firstknock.hsfinest.com)"
OVERPASS_ENDPOINTS = [
    ("https://maps.mail.ru/osm/tools/overpass/api/interpreter", 60),
    ("https://overpass-api.de/api/interpreter", 60),
    ("https://overpass.private.coffee/api/interpreter", 60),
]

# Preset metro bounding boxes: (south, west, north, east)
METROS: dict[str, tuple[float, float, float, float]] = {
    "Peninsula, CA": (37.35, -122.32, 37.53, -122.05),
    "Bay Area, CA": (37.20, -122.55, 37.95, -121.75),
    "Austin, TX": (30.10, -97.94, 30.52, -97.56),
    "Denver, CO": (39.61, -105.11, 39.91, -104.60),
    "Nashville, TN": (36.03, -86.92, 36.28, -86.63),
    "Portland, OR": (45.43, -122.84, 45.65, -122.47),
    "Tampa, FL": (27.87, -82.53, 28.17, -82.34),
    "Columbus, OH": (39.86, -83.20, 40.14, -82.77),
    "Atlanta, GA": (33.65, -84.55, 33.89, -84.29),
    "Seattle, WA": (47.50, -122.44, 47.73, -122.24),
    "Phoenix, AZ": (33.30, -112.20, 33.70, -111.90),
    "Chicago, IL": (41.80, -87.75, 41.99, -87.55),
}

CATEGORIES: dict[str, list[str]] = {
    "Restaurant & Café": ['["amenity"~"^(restaurant|cafe|fast_food|bar|pub|ice_cream)$"]'],
    "Fitness & Gym": ['["leisure"~"^(fitness_centre|sports_centre)$"]', '["sport"="fitness"]'],
    "Auto Services": ['["shop"~"^(car_repair|tyres|car_parts)$"]', '["amenity"="car_wash"]'],
    "Home Services": ['["craft"~"^(plumber|electrician|carpenter|hvac|painter|roofer)$"]'],
    "Medical & Dental": ['["amenity"~"^(dentist|doctors|clinic|pharmacy)$"]'],
    "Retail Boutique": ['["shop"~"^(clothes|boutique|gift|books|shoes|jewelry|florist|furniture)$"]'],
    "Professional Svcs": ['["office"~"^(accountant|lawyer|insurance|estate_agent|financial|it|company|consulting)$"]'],
    "Beauty & Spa": ['["shop"~"^(hairdresser|beauty)$"]', '["leisure"="spa"]'],
}

MAX_BBOX_AREA = 0.6  # square degrees (~50 x 50 miles); bigger boxes time out on Overpass


def build_query(bbox: tuple[float, float, float, float], categories: list[str], cap: int) -> str:
    s, w, n, e = bbox
    box = f"({s},{w},{n},{e})"
    stmts = [f"  nwr{filt}{box};" for cat in categories for filt in CATEGORIES[cat]]
    return "[out:json][timeout:45];\n(\n" + "\n".join(stmts) + f"\n);\nout center tags {int(cap)};"


def which_category(tags: dict) -> str | None:
    amenity, shop, leisure = tags.get("amenity", ""), tags.get("shop", ""), tags.get("leisure", "")
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
    if tags.get("office") in {"accountant", "lawyer", "insurance", "estate_agent", "financial", "it", "company",
                              "consulting"}:
        return "Professional Svcs"
    if shop in {"hairdresser", "beauty"} or leisure == "spa":
        return "Beauty & Spa"
    return None


def parse_listings(elements: list[dict], profile_name: str) -> list[dict]:
    """Overpass elements -> scored prospect rows, best first, one per OSM id."""
    rows: dict[str, dict] = {}
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        cat = which_category(tags) if name else None
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not cat or lat is None or lon is None:
            continue
        row = {
            "id": f'{el.get("type", "node")}/{el.get("id")}',
            "name": name,
            "category": cat,
            "lat": float(lat),
            "lon": float(lon),
            "address": " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x),
            "city": tags.get("addr:city", ""),
            "phone": tags.get("phone") or tags.get("contact:phone") or "",
            "website": tags.get("website") or tags.get("contact:website") or "",
            "hours": tags.get("opening_hours", ""),
            "independent": not (tags.get("brand") or tags.get("operator")),
        }
        score, why = lead_score(row, profile_name)
        row.update(score=score, heat=heat_of(score), why=" · ".join(why), insight=sales_insight(row, profile_name))
        rows[row["id"]] = row
    return sorted(rows.values(), key=lambda r: r["score"], reverse=True)


def geocode(session, query: str) -> tuple[float, float, float, float] | None:
    """Free-text area -> (south, west, north, east) via Nominatim."""
    try:
        r = session.get("https://nominatim.openstreetmap.org/search",
                        params={"q": query, "format": "json", "limit": 1},
                        headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        s, n, w, e = (float(x) for x in data[0]["boundingbox"])
        return (s, w, n, e)
    except Exception:
        return None


def fetch_overpass(session, query: str) -> list[dict]:
    errors = []
    for url, wait in OVERPASS_ENDPOINTS:
        try:
            r = session.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=wait)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json().get("elements", [])
            errors.append(f"{url.split('/')[2]}: HTTP {r.status_code}")
        except Exception as exc:
            errors.append(f"{url.split('/')[2]}: {type(exc).__name__}")
            time.sleep(0.3)
    raise RuntimeError("the free map-data servers are busy (" + "; ".join(errors) + "). Try again in a few minutes.")
