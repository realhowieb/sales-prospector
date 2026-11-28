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
    # Weekly email (Resend over SMTP) — optional; the email routes answer 503 until set.
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""
    digest_secret: str = ""  # shared with the Monday scheduler
    web_url: str = "https://firstknock.hsfinest.com"


def load_settings() -> Settings:
    origins = os.environ.get("WEB_ORIGINS", "https://firstknock.hsfinest.com,http://localhost:3000")
    return Settings(
        supabase_url=os.environ.get("SUPABASE_URL", "").rstrip("/"),
        supabase_anon_key=os.environ.get("SUPABASE_ANON_KEY", ""),
        supabase_service_key=os.environ.get("SUPABASE_SERVICE_KEY", ""),
        web_origins=tuple(o.strip().rstrip("/") for o in origins.split(",") if o.strip()),
        smtp_host=os.environ.get("SMTP_HOST", "smtp.resend.com"),
        smtp_port=int(os.environ.get("SMTP_PORT", "465") or 465),
        smtp_user=os.environ.get("SMTP_USER", "resend"),
        smtp_pass=os.environ.get("SMTP_PASS", ""),
        smtp_from=os.environ.get("SMTP_FROM", "FirstKnock <noreply@hsfinest.com>"),
        digest_secret=os.environ.get("DIGEST_SECRET", ""),
        web_url=os.environ.get("WEB_URL", "https://firstknock.hsfinest.com").rstrip("/"),
    )
