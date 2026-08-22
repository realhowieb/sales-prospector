from __future__ import annotations

APP_VERSION = "2026.08.21"
REQUIRED_SCHEMA_VERSION = "020"

CORE_TABLES = [
    "reps",
    "leads",
    "reviews",
    "pipeline_entries",
    "companies",
    "opportunities",
    "profile_claims",
    "connections",
    "shortlist_items",
    "account_profiles",
    "admin_account_roles",
    "content_reports",
    "marketplace_events",
    "schema_migrations",
    "admin_audit_log",
]


def normalize_migration_version(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(3) if digits else ""


def latest_schema_version(rows: list[dict]) -> str:
    versions = [normalize_migration_version(row.get("version")) for row in rows]
    versions = [version for version in versions if version]
    return max(versions, default="")


def schema_status(rows: list[dict], required: str = REQUIRED_SCHEMA_VERSION) -> dict:
    current = latest_schema_version(rows)
    required_norm = normalize_migration_version(required)
    if not current:
        return {
            "current": "",
            "required": required_norm,
            "ok": False,
            "label": "Schema tracking not installed",
        }
    ok = current >= required_norm
    return {
        "current": current,
        "required": required_norm,
        "ok": ok,
        "label": "Schema current" if ok else f"Schema behind: {current} of {required_norm}",
    }

