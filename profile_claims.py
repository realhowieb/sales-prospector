"""Claiming an existing rep listing: claim payload building and claim-status normalization."""

from __future__ import annotations

from dataclasses import dataclass


CLAIM_STATUSES = {"pending", "approved", "rejected"}


@dataclass(frozen=True)
class ProfileClaimPayload:
    rep_id: int
    claimant_email: str
    claimant_name: str
    message: str
    status: str = "pending"


def normalize_rep_db_id(rep_id) -> int | None:
    value = str(rep_id or "").replace("db-", "").strip()
    return int(value) if value.isdigit() else None


def is_claimable_rep(rep: dict) -> bool:
    if rep.get("claimed") is True:
        return False
    return normalize_rep_db_id(rep.get("id")) is not None


def build_profile_claim_payload(rep: dict, claimant_email: str, claimant_name: str = "", message: str = "") -> ProfileClaimPayload:
    rep_id = normalize_rep_db_id(rep.get("id"))
    email = (claimant_email or "").strip().lower()
    if rep_id is None:
        raise ValueError("Only live database profiles can be claimed.")
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise ValueError("Enter a valid business email.")
    return ProfileClaimPayload(
        rep_id=rep_id,
        claimant_email=email,
        claimant_name=(claimant_name or "").strip(),
        message=(message or "").strip(),
    )


def normalize_claim_status(status: str) -> str:
    value = (status or "pending").strip().lower()
    if value not in CLAIM_STATUSES:
        return "pending"
    return value
