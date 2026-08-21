"""Account roles, sessions, and permission checks (rep/company creation, admin access)."""

from __future__ import annotations

from dataclasses import dataclass


ACCOUNT_ROLES = {"rep", "company", "admin"}
PUBLIC_ACCOUNT_ROLES = {"rep", "company"}


@dataclass(frozen=True)
class AuthSession:
    access_token: str
    refresh_token: str
    user_id: str
    email: str


def normalize_account_role(role: str | None) -> str:
    value = (role or "rep").strip().lower()
    return value if value in ACCOUNT_ROLES else "rep"


def public_signup_role(role: str | None) -> str:
    value = normalize_account_role(role)
    return value if value in PUBLIC_ACCOUNT_ROLES else "rep"


def auth_session_from_response(data: dict) -> AuthSession:
    if data.get("session") and isinstance(data["session"], dict):
        session = data["session"]
        user = data.get("user") or session.get("user") or {}
        data = {**session, "user": user}
    user = data.get("user") or {}
    access_token = data.get("access_token") or ""
    if not access_token:
        raise ValueError("Supabase did not return an access token.")
    return AuthSession(
        access_token=access_token,
        refresh_token=data.get("refresh_token") or "",
        user_id=user.get("id") or "",
        email=(user.get("email") or "").strip().lower(),
    )


def can_create_rep(role: str | None) -> bool:
    return normalize_account_role(role) in {"rep", "admin"}


def can_create_company(role: str | None) -> bool:
    return normalize_account_role(role) in {"company", "admin"}


def is_admin_role(role: str | None, admin_verified: bool = False) -> bool:
    return admin_verified and normalize_account_role(role) == "admin"
