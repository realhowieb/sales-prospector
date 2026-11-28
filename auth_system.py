"""Account roles, sessions, and permission checks (rep/company creation, admin access)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import quote


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


# --------------------------------------------------------------------------- #
# Password reset (Supabase PKCE recovery link, or a 6-digit code)
# --------------------------------------------------------------------------- #
MIN_PASSWORD_LENGTH = 8


def recovery_pkce_pair(email: str, server_secret: str) -> tuple[str, str]:
    """Deterministic PKCE verifier/challenge for a reset request.

    The verifier is derived from the email and a server-only secret, so the tab that opens
    from the email link can rebuild it without any stored state. The one-time `code` in the
    link is useless without it.
    """
    msg = (email or "").strip().lower().encode()
    digest = hmac.new((server_secret or "").encode(), b"pw-reset:" + msg, hashlib.sha256).digest()
    verifier = base64.urlsafe_b64encode(digest + hashlib.sha256(digest).digest()).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def recovery_request_path(base_url: str) -> str:
    """Supabase /recover path, sending the link back to this app's reset page."""
    if not base_url:
        return "recover"
    return "recover?redirect_to=" + quote(f"{base_url.rstrip('/')}/?reset_password=1", safe="")


def password_problem(password: str, confirm: str) -> str:
    """Return a user-facing problem with a new password, or '' if it is acceptable."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if password != confirm:
        return "The two passwords don't match."
    return ""
