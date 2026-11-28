"""Sign-in check: the web app sends the user's Supabase access token as a Bearer token."""
from __future__ import annotations

from dataclasses import dataclass

import requests
from fastapi import Depends, Header, HTTPException

from api.cache import cached
from api.settings import Settings, load_settings


@dataclass(frozen=True)
class User:
    id: str
    email: str


def get_settings() -> Settings:
    return load_settings()


def fetch_supabase_user(settings: Settings, token: str) -> dict | None:
    r = requests.get(f"{settings.supabase_url}/auth/v1/user",
                     headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"},
                     timeout=10)
    return r.json() if r.status_code == 200 else None


def current_user(authorization: str = Header(default=""), settings: Settings = Depends(get_settings)) -> User:
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise HTTPException(503, "Accounts are not configured on this server.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Sign in required.")
    data = cached(("user", token), 120, lambda: fetch_supabase_user(settings, token))
    if not data or not data.get("id"):
        raise HTTPException(401, "Your session expired. Sign in again.")
    return User(id=data["id"], email=(data.get("email") or "").strip().lower())
