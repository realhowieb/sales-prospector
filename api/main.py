"""FirstKnock API. Run: uvicorn api.main:app

Endpoints (all /v1 routes need a Supabase sign-in, sent as `Authorization: Bearer <token>`):
GET  /healthz
GET  /v1/me
GET  /v1/options                      territories, metros, categories, sales profiles
GET  /v1/leads/licenses               pending / new liquor licenses (CA ABC daily file)
POST /v1/leads/licenses/contacts      same list with phone / email / website looked up
GET  /v1/leads/news                   local news about openings, leases and moves
GET  /v1/leads/new-on-map             businesses newly added to OpenStreetMap
GET  /v1/discover                     businesses in an area by category, scored
GET  /v1/pipeline · PUT/DELETE /v1/pipeline/{prospect_id}
OpenAPI docs at /docs.
"""
from __future__ import annotations

from datetime import date, timedelta

import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

import discover_core
import trigger_leads
from api import pipeline as pipe
from api.auth import User, current_user, get_settings
from api.cache import cached
from api.settings import Settings, load_settings
from prospecting_core import PRODUCT_PROFILES

VERSION = "1.0.0"
UA = discover_core.USER_AGENT

app = FastAPI(title="FirstKnock API", version=VERSION)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(load_settings().web_origins),
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# --------------------------------------------------------------------------- #
# Data sources (module-level so tests can replace them)
# --------------------------------------------------------------------------- #
def abc_csv() -> str:
    return cached("abc_csv", 6 * 3600, lambda: trigger_leads.download_abc_csv(requests, UA))


def territory_bbox(cities: tuple[str, ...]) -> tuple[float, float, float, float]:
    def compute():
        return trigger_leads.union_bbox([discover_core.geocode(requests, f"{c}, California") for c in cities])

    bbox = cached(("bbox", cities), 7 * 86400, compute)
    if bbox is None:
        raise HTTPException(422, "Couldn't find those cities on the map.")
    s, w, n, e = bbox
    if (n - s) * (e - w) > discover_core.MAX_BBOX_AREA:
        raise HTTPException(422, "That territory is too big for one search — pick fewer cities.")
    return bbox


def overpass(query: str, mirrors=None) -> list[dict]:
    try:
        return trigger_leads.fetch_listings(requests, query, UA, mirrors=mirrors)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def news(cities: tuple[str, ...], days: int) -> list[dict]:
    return trigger_leads.fetch_news(requests, list(cities), UA, days)


def discover_fetch(query: str) -> list[dict]:
    try:
        return discover_core.fetch_overpass(requests, query)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def geocode_area(area: str):
    return discover_core.geocode(requests, area)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def parse_cities(cities: str = Query(..., description="Comma-separated California cities, or a preset name")) -> tuple[str, ...]:
    if cities in trigger_leads.PRESET_TERRITORIES:
        return tuple(trigger_leads.PRESET_TERRITORIES[cities])
    out = tuple(dict.fromkeys(c.strip().title() for c in cities.split(",") if c.strip()))
    if not out:
        raise HTTPException(422, "Pick at least one city.")
    if len(out) > 25:
        raise HTTPException(422, "Pick 25 cities or fewer.")
    return out


def _licenses(cities: tuple[str, ...], days: int) -> list[dict]:
    rows = cached(("licenses", cities, days), 6 * 3600, lambda: trigger_leads.parse_abc(abc_csv(), list(cities),
                                                                                         issued_days=days))
    return [dict(r) for r in rows]


def _chain_filter(rows: list[dict], hide_chains: bool, key: str) -> list[dict]:
    return [r for r in rows if not (hide_chains and r.get(key))]


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "version": VERSION}


@app.get("/v1/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "email": user.email}


@app.get("/v1/options")
def options(user: User = Depends(current_user)) -> dict:
    return {
        "territories": trigger_leads.PRESET_TERRITORIES,
        "metros": list(discover_core.METROS),
        "categories": list(discover_core.CATEGORIES),
        "profiles": list(PRODUCT_PROFILES),
        "stages": pipe.STAGES,
    }


@app.get("/v1/leads/licenses")
def licenses(cities: tuple[str, ...] = Depends(parse_cities), days: int = Query(180, ge=30, le=365),
             hide_chains: bool = True, user: User = Depends(current_user)) -> dict:
    rows = _chain_filter(_licenses(cities, days), hide_chains, "Chain")
    return {"cities": cities, "count": len(rows), "leads": rows}


@app.post("/v1/leads/licenses/contacts")
def license_contacts(cities: tuple[str, ...] = Depends(parse_cities), days: int = Query(180, ge=30, le=365),
                     hide_chains: bool = True, user: User = Depends(current_user)) -> dict:
    def compute():
        leads = _licenses(cities, days)
        listings = overpass(trigger_leads.osm_contact_query(territory_bbox(cities)))
        return trigger_leads.enrich_contacts(leads, listings, session=requests, user_agent=UA)

    rows = _chain_filter([dict(r) for r in cached(("contacts", cities, days), 24 * 3600, compute)],
                         hide_chains, "Chain")
    return {"cities": cities, "count": len(rows), "leads": rows}


@app.get("/v1/leads/news")
def news_leads(cities: tuple[str, ...] = Depends(parse_cities), days: int = Query(30, ge=7, le=60),
               user: User = Depends(current_user)) -> dict:
    rows = cached(("news", cities, days), 6 * 3600, lambda: news(cities, days))
    return {"cities": cities, "count": len(rows), "leads": rows}


@app.get("/v1/leads/new-on-map")
def new_on_map(cities: tuple[str, ...] = Depends(parse_cities), days: int = Query(60, ge=7, le=180),
               hide_chains: bool = True, user: User = Depends(current_user)) -> dict:
    def compute():
        since = date.today() - timedelta(days=days)
        query = trigger_leads.osm_new_query(territory_bbox(cities), since)
        return trigger_leads.parse_new_on_map(overpass(query, trigger_leads.NEW_ON_MAP_MIRRORS), hide_chains=False)

    rows = [r for r in cached(("newmap", cities, days), 12 * 3600, compute)
            if not (hide_chains and r.get("Note") == "Chain")]
    return {"cities": cities, "count": len(rows), "leads": rows}


@app.get("/v1/discover")
def discover(metro: str = "", area: str = "", categories: str = Query(..., description="Comma-separated"),
             profile: str = "Security systems", cap: int = Query(200, ge=10, le=500),
             user: User = Depends(current_user)) -> dict:
    cats = [c.strip() for c in categories.split(",") if c.strip() in discover_core.CATEGORIES]
    if not cats:
        raise HTTPException(422, "Pick at least one category.")
    if profile not in PRODUCT_PROFILES:
        raise HTTPException(422, "Unknown sales profile.")
    if area.strip():
        bbox = cached(("area", area.strip().lower()), 7 * 86400, lambda: geocode_area(area.strip()))
        if bbox is None:
            raise HTTPException(422, f"Couldn't find “{area}”. Try a more specific name.")
        s, w, n, e = bbox
        if (n - s) * (e - w) > discover_core.MAX_BBOX_AREA:
            raise HTTPException(422, "That area is too big — try a city or neighborhood.")
        label = area.strip()
    elif metro in discover_core.METROS:
        bbox, label = discover_core.METROS[metro], metro
    else:
        raise HTTPException(422, "Pick a metro or type an area.")
    query = discover_core.build_query(bbox, cats, cap)
    elements = cached(("discover", query), 3600, lambda: discover_fetch(query))
    rows = discover_core.parse_listings(elements, profile)
    return {"area": label, "count": len(rows), "prospects": rows}


class PipelineEntry(BaseModel):
    name: str = Field("", max_length=200)
    category: str = Field("", max_length=120)
    stage: str = "New lead"
    note: str = Field("", max_length=4000)
    next_follow_up: date | None = None
    outcome: str = Field("", max_length=200)


@app.get("/v1/pipeline")
def pipeline_list(user: User = Depends(current_user), settings: Settings = Depends(get_settings)) -> dict:
    rows = pipe.list_entries(settings, user)
    return {"count": len(rows), "entries": rows}


@app.put("/v1/pipeline/{prospect_id}")
def pipeline_put(prospect_id: str, entry: PipelineEntry, user: User = Depends(current_user),
                 settings: Settings = Depends(get_settings)) -> dict:
    if not prospect_id or len(prospect_id) > 200:
        raise HTTPException(422, "Bad prospect id.")
    data = entry.model_dump()
    data["next_follow_up"] = entry.next_follow_up.isoformat() if entry.next_follow_up else None
    return pipe.upsert_entry(settings, user, prospect_id, data)


@app.delete("/v1/pipeline/{prospect_id}")
def pipeline_delete(prospect_id: str, user: User = Depends(current_user),
                    settings: Settings = Depends(get_settings)) -> dict:
    pipe.delete_entry(settings, user, prospect_id)
    return {"deleted": prospect_id}
