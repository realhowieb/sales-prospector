"""Environment settings for the FirstKnock API."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_anon_key: str
    supabase_service_key: str
    web_origins: tuple[str, ...]


def load_settings() -> Settings:
    origins = os.environ.get("WEB_ORIGINS", "https://firstknock.hsfinest.com,http://localhost:3000")
    return Settings(
        supabase_url=os.environ.get("SUPABASE_URL", "").rstrip("/"),
        supabase_anon_key=os.environ.get("SUPABASE_ANON_KEY", ""),
        supabase_service_key=os.environ.get("SUPABASE_SERVICE_KEY", ""),
        web_origins=tuple(o.strip().rstrip("/") for o in origins.split(",") if o.strip()),
    )
