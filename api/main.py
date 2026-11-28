"""FirstKnock API. Run: uvicorn api.main:app

Endpoints (all /v1 routes need a Supabase sign-in, sent as `Authorization: Bearer <token>`):
GET  /healthz
GET  /v1/me
GET  /v1/options                      territories, metros, categories, sales profiles
GET  /v1/leads/licenses               pending / new liquor licenses (CA ABC daily file)
POST /v1/leads/licenses/contacts      same list with phone / email / website looked up
GET  /v1/leads/news                   local news about openings, leases and moves
GET  /v1/leads/new-on-map             businesses newly added to OpenStreetMap
GET  /v1/leads/permits                commercial build-out / move-in permits (Palo Alto so far)
GET  /v1/discover                     businesses in an area by category, scored
GET  /v1/pipeline · PUT/DELETE /v1/pipeline/{prospect_id}
GET/PUT /v1/digest · POST /v1/digest/test   weekly email settings / send a test now
POST /internal/digest/run            Monday scheduler (X-Digest-Secret header)
OpenAPI docs at /docs.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import hmac

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

import discover_core
import permits
import trades
import trigger_leads
from api import digest
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


def palo_alto_csv() -> str:
    def compute():
        try:
            return permits.download_palo_alto(requests, UA)
        except Exception as exc:
            raise HTTPException(503, "Palo Alto's permit data isn't responding right now. Try again in a few minutes.") from exc

    return cached("palo_alto_permits", 12 * 3600, compute)


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


def _permit_rows(cities: tuple[str, ...], days: int, include_minor: bool = False) -> list[dict]:
    if "Palo Alto" not in cities:
        return []
    return cached(("permits", "Palo Alto", days, include_minor), 12 * 3600,
                  lambda: permits.parse_palo_alto(palo_alto_csv(), days=days, include_minor=include_minor))


def _new_on_map_rows(cities: tuple[str, ...], days: int) -> list[dict]:
    def compute():
        since = date.today() - timedelta(days=days)
        query = trigger_leads.osm_new_query(territory_bbox(cities), since)
        return trigger_leads.parse_new_on_map(overpass(query, trigger_leads.NEW_ON_MAP_MIRRORS), hide_chains=False)

    return cached(("newmap", cities, days), 12 * 3600, compute)


def _news_rows(cities: tuple[str, ...], days: int) -> list[dict]:
    return cached(("news", cities, days), 6 * 3600, lambda: news(cities, days))


# What the weekly email pulls for each source (chains hidden; windows comfortably longer than a week).
DIGEST_LOADERS = {
    "permits": lambda c: _chain_filter([dict(r) for r in _permit_rows(c, 30)], True, "Chain"),
    "licenses": lambda c: _chain_filter(_licenses(c, 90), True, "Chain"),
    "news": lambda c: _news_rows(c, 14),
    "map": lambda c: [r for r in _new_on_map_rows(c, 30) if r.get("Note") != "Chain"],
}


def _cities(territory: str) -> tuple[str, ...]:
    return parse_cities(territory)


_last_call: dict[str, float] = {}


def _throttle(key: str, seconds: float) -> None:
    now = time.time()
    if now - _last_call.get(key, 0) < seconds:
        raise HTTPException(429, "You just sent a test — wait a couple of minutes before sending another.")
    _last_call[key] = now


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
        "trades": trades.options(),
    }


@app.get("/v1/leads/licenses")
def licenses(cities: tuple[str, ...] = Depends(parse_cities), days: int = Query(180, ge=30, le=365),
             hide_chains: bool = True, trade: str = trades.DEFAULT_TRADE, user: User = Depends(current_user)) -> dict:
    rows = trades.rescore("licenses", _chain_filter(_licenses(cities, days), hide_chains, "Chain"), trade)
    return {"cities": cities, "count": len(rows), "leads": rows}


@app.post("/v1/leads/licenses/contacts")
def license_contacts(cities: tuple[str, ...] = Depends(parse_cities), days: int = Query(180, ge=30, le=365),
                     hide_chains: bool = True, trade: str = trades.DEFAULT_TRADE,
                     user: User = Depends(current_user)) -> dict:
    def compute():
        leads = _licenses(cities, days)
        listings = overpass(trigger_leads.osm_contact_query(territory_bbox(cities)))
        return trigger_leads.enrich_contacts(leads, listings, session=requests, user_agent=UA)

    rows = trades.rescore("licenses", _chain_filter([dict(r) for r in cached(("contacts", cities, days), 24 * 3600,
                                                                             compute)], hide_chains, "Chain"), trade)
    return {"cities": cities, "count": len(rows), "leads": rows}


@app.get("/v1/leads/news")
def news_leads(cities: tuple[str, ...] = Depends(parse_cities), days: int = Query(30, ge=7, le=60),
               trade: str = trades.DEFAULT_TRADE, user: User = Depends(current_user)) -> dict:
    rows = trades.rescore("news", _news_rows(cities, days), trade)
    return {"cities": cities, "count": len(rows), "leads": rows}


@app.get("/v1/leads/new-on-map")
def new_on_map(cities: tuple[str, ...] = Depends(parse_cities), days: int = Query(60, ge=7, le=180),
               hide_chains: bool = True, trade: str = trades.DEFAULT_TRADE,
               user: User = Depends(current_user)) -> dict:
    rows = trades.rescore("map", [r for r in _new_on_map_rows(cities, days)
                                  if not (hide_chains and r.get("Note") == "Chain")], trade)
    return {"cities": cities, "count": len(rows), "leads": rows}


@app.get("/v1/leads/permits")
def permit_leads(cities: tuple[str, ...] = Depends(parse_cities), days: int = Query(90, ge=14, le=365),
                 hide_chains: bool = True, include_minor: bool = False, trade: str = trades.DEFAULT_TRADE,
                 user: User = Depends(current_user)) -> dict:
    covered = [c for c in cities if c in permits.COVERED_CITIES]
    rows = trades.rescore("permits", _chain_filter([dict(r) for r in _permit_rows(cities, days, include_minor)],
                                                   hide_chains, "Chain"), trade)
    return {"cities": cities, "covered": covered, "not_covered": [c for c in cities if c not in covered],
            "count": len(rows), "leads": rows}


@app.get("/v1/discover")
def discover(metro: str = "", area: str = "", categories: str = Query(..., description="Comma-separated"),
             profile: str = "", trade: str = trades.DEFAULT_TRADE, cap: int = Query(200, ge=10, le=500),
             user: User = Depends(current_user)) -> dict:
    cats = [c.strip() for c in categories.split(",") if c.strip() in discover_core.CATEGORIES]
    if not cats:
        raise HTTPException(422, "Pick at least one category.")
    profile = profile or trades.get(trade)["profile"]
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


class DigestSettings(BaseModel):
    active: bool = True
    trade: str = trades.DEFAULT_TRADE
    territory: str = Field(digest.DEFAULT_TERRITORY, max_length=500)
    sources: list[str] = Field(default_factory=lambda: list(digest.SOURCES))


@app.get("/v1/digest")
def digest_get(user: User = Depends(current_user), settings: Settings = Depends(get_settings)) -> dict:
    sub = digest.get_subscription(settings, user.id) or {
        "active": False, "territory": digest.DEFAULT_TERRITORY, "sources": list(digest.SOURCES), "last_sent_at": None}
    return {**{k: sub.get(k) for k in digest.PUBLIC}, "email": user.email, "sources_available": digest.SOURCES}


@app.put("/v1/digest")
def digest_put(body: DigestSettings, user: User = Depends(current_user),
               settings: Settings = Depends(get_settings)) -> dict:
    _cities(body.territory)  # 422 for an unusable territory
    sources = [s for s in body.sources if s in digest.SOURCES]
    if body.active and not sources:
        raise HTTPException(422, "Pick at least one source.")
    sub = digest.save_subscription(settings, user.id, user.email, body.active, body.territory, sources)
    return {k: sub.get(k) for k in digest.PUBLIC}


@app.post("/v1/digest/test")
def digest_test(body: DigestSettings, user: User = Depends(current_user),
                settings: Settings = Depends(get_settings)) -> dict:
    _throttle(f"digest-test:{user.id}", 120)
    sources = [s for s in body.sources if s in digest.SOURCES] or list(digest.SOURCES)
    return digest.send_test(settings, user.email, body.territory, sources, DIGEST_LOADERS, _cities, body.trade)


@app.post("/internal/digest/run")
def digest_run(x_digest_secret: str = Header(default=""), dry_run: bool = False,
               settings: Settings = Depends(get_settings)) -> dict:
    if not settings.digest_secret or not hmac.compare_digest(x_digest_secret, settings.digest_secret):
        raise HTTPException(403, "Forbidden.")
    return digest.run_all(settings, DIGEST_LOADERS, _cities, dry_run=dry_run)
