"""Saved pipeline rows in Supabase `pipeline_entries`, scoped to the signed-in user."""
from __future__ import annotations

import requests
from fastapi import HTTPException

from api.auth import User
from api.settings import Settings

STAGES = ["New lead", "Contacted", "Qualified", "Won", "Passed"]
FIELDS = ["prospect_id", "name", "category", "stage", "note", "next_follow_up", "last_contacted",
          "call_attempts", "outcome", "updated_at"]


def _headers(settings: Settings, extra: dict | None = None) -> dict:
    if not settings.supabase_service_key:
        raise HTTPException(503, "Saving the pipeline is not configured on this server.")
    h = {"apikey": settings.supabase_service_key, "Authorization": f"Bearer {settings.supabase_service_key}",
         "Content-Type": "application/json"}
    return {**h, **(extra or {})}


def owner_key(user: User) -> str:
    # Unique per account so the (owner_email, owner_key_hash, prospect_id) index de-duplicates upserts.
    return f"user:{user.id}"


def _check(r) -> None:
    if r.status_code >= 400:
        raise HTTPException(502, f"Database error ({r.status_code}). Try again.")


def list_entries(settings: Settings, user: User) -> list[dict]:
    r = requests.get(f"{settings.supabase_url}/rest/v1/pipeline_entries", headers=_headers(settings),
                     params={"select": ",".join(FIELDS), "owner_user_id": f"eq.{user.id}",
                             "order": "updated_at.desc"}, timeout=20)
    _check(r)
    return r.json()


def upsert_entry(settings: Settings, user: User, prospect_id: str, entry: dict) -> dict:
    row = {
        "owner_email": user.email,
        "owner_key_hash": owner_key(user),
        "owner_user_id": user.id,
        "prospect_id": prospect_id,
        "name": entry.get("name", "")[:200],
        "category": entry.get("category", "")[:120],
        "stage": entry.get("stage") if entry.get("stage") in STAGES else "New lead",
        "note": entry.get("note", "")[:4000],
        "next_follow_up": entry.get("next_follow_up") or None,
        "outcome": entry.get("outcome", "")[:200],
    }
    r = requests.post(f"{settings.supabase_url}/rest/v1/pipeline_entries",
                      headers=_headers(settings, {"Prefer": "resolution=merge-duplicates,return=representation"}),
                      params={"on_conflict": "owner_email,owner_key_hash,prospect_id"}, json=[row], timeout=20)
    _check(r)
    rows = r.json()
    return {k: rows[0].get(k) for k in FIELDS} if rows else row


def delete_entry(settings: Settings, user: User, prospect_id: str) -> None:
    r = requests.delete(f"{settings.supabase_url}/rest/v1/pipeline_entries", headers=_headers(settings),
                        params={"owner_user_id": f"eq.{user.id}", "prospect_id": f"eq.{prospect_id}"}, timeout=20)
    _check(r)
