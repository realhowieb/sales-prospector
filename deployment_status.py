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

EXPECTED_TABLE_COLUMNS = {
    "reps": [
        "id", "created_at", "name", "company", "categories", "metros", "deal",
        "rating", "reviews", "verified", "blurb", "email", "phone", "active",
        "profile_slug", "headline", "years_experience", "industries",
        "customer_types", "states", "zip_codes", "territory_radius",
        "availability_status", "open_to_new_lines", "compensation_types",
        "existing_lines", "competing_lines", "website", "linkedin_url",
        "profile_status", "claimed", "featured", "source", "updated_at",
    ],
    "leads": [
        "id", "created_at", "rep_id", "customer_name", "customer_email",
        "customer_phone", "need", "metro", "category", "status",
    ],
    "reviews": [
        "id", "created_at", "rep_id", "lead_id", "reviewer", "rating", "title",
        "review", "verified_relationship", "status", "reviewed_at", "approved_at",
    ],
    "pipeline_entries": [
        "id", "created_at", "updated_at", "prospect_id", "rep_id", "stage",
        "notes", "next_follow_up", "outcome", "call_attempts",
    ],
    "companies": [
        "id", "created_at", "updated_at", "name", "slug", "website",
        "description", "industries", "categories", "company_size",
        "headquarters", "states_needed", "metros_needed", "customer_types",
        "verified", "profile_status", "featured", "source",
    ],
    "opportunities": [
        "id", "company_id", "created_at", "updated_at", "title", "description",
        "categories", "industries", "customer_types", "metros", "states",
        "zip_codes", "territory_type", "compensation_types", "commission_min",
        "commission_max", "exclusive_territory", "experience_required",
        "active", "featured", "application_count", "expires_at",
    ],
    "profile_claims": [
        "id", "rep_id", "created_at", "updated_at", "claimant_email",
        "claimant_name", "status", "reviewed_at", "reviewed_by",
    ],
    "connections": [
        "id", "company_id", "rep_id", "opportunity_id", "created_at",
        "updated_at", "status", "message", "initiated_by", "owner_user_id",
    ],
    "shortlist_items": [
        "id", "created_at", "updated_at", "owner_user_id", "session_id",
        "collection", "target_type", "target_id",
    ],
    "account_profiles": [
        "user_id", "created_at", "updated_at", "email", "display_name", "role",
    ],
    "admin_account_roles": [
        "user_id", "created_at", "updated_at", "role", "approved",
    ],
    "content_reports": [
        "id", "created_at", "updated_at", "target_type", "target_id", "reason",
        "details", "reporter_email", "status", "reviewed_at", "reviewed_by",
    ],
    "marketplace_events": [
        "id", "created_at", "event_name", "actor_role", "target_type",
        "target_id", "category", "metro",
    ],
    "schema_migrations": ["version", "name", "applied_at"],
    "admin_audit_log": [
        "id", "created_at", "actor_user_id", "actor_email", "action",
        "target_type", "target_id", "metadata",
    ],
}

TABLE_MIGRATION_HINTS = {
    "reps": "Run supabase_setup.sql or migrations/005_rep_profiles.sql and migrations/006_rep_availability_preferences.sql.",
    "leads": "Run supabase_setup.sql or migrations/001_initial_marketplace.sql.",
    "reviews": "Run supabase_setup.sql or migrations/011_moderated_reviews.sql.",
    "pipeline_entries": "Run supabase_setup.sql or migrations/003_pipeline_followups.sql.",
    "companies": "Run supabase_setup.sql or migrations/007_company_profiles.sql.",
    "opportunities": "Run supabase_setup.sql or migrations/008_sales_opportunities.sql.",
    "profile_claims": "Run supabase_setup.sql or migrations/010_profile_claims.sql.",
    "connections": "Run supabase_setup.sql or migrations/012_connections.sql.",
    "shortlist_items": "Run supabase_setup.sql or migrations/013_shortlists.sql.",
    "account_profiles": "Run supabase_setup.sql or migrations/014_auth_accounts_roles.sql.",
    "admin_account_roles": "Run supabase_setup.sql or migrations/014_auth_accounts_roles.sql.",
    "content_reports": "Run supabase_setup.sql or migrations/015_admin_dashboard_reports.sql.",
    "marketplace_events": "Run supabase_setup.sql or migrations/018_marketplace_analytics.sql.",
    "schema_migrations": "Run supabase_setup.sql or migrations/020_schema_tracking_and_admin_audit.sql.",
    "admin_audit_log": "Run supabase_setup.sql or migrations/020_schema_tracking_and_admin_audit.sql.",
}

TABLE_ORDER_COLUMNS = {
    "schema_migrations": "version.desc",
    "admin_audit_log": "created_at.desc",
}


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


def table_order_for(table: str) -> str:
    return TABLE_ORDER_COLUMNS.get(table, "created_at.desc")


def summarize_table_checks(rows: list[dict]) -> dict:
    counts = {"ok": 0, "missing": 0, "restricted": 0, "error": 0, "demo mode": 0}
    for row in rows:
        status = str(row.get("status") or "error")
        counts[status] = counts.get(status, 0) + 1
    return counts


def expected_columns_for(table: str) -> list[str]:
    return EXPECTED_TABLE_COLUMNS.get(table, [])


def migration_hint_for(table: str) -> str:
    return TABLE_MIGRATION_HINTS.get(table, "Run supabase_setup.sql.")


def add_migration_hints(rows: list[dict]) -> list[dict]:
    hinted = []
    for row in rows:
        next_row = dict(row)
        next_row["fix"] = migration_hint_for(str(row.get("table") or ""))
        hinted.append(next_row)
    return hinted


def summarize_column_checks(rows: list[dict]) -> dict:
    counts = {"ok": 0, "missing": 0, "restricted": 0, "error": 0, "demo mode": 0}
    for row in rows:
        status = str(row.get("status") or "error")
        counts[status] = counts.get(status, 0) + 1
    return counts
