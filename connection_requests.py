from __future__ import annotations

from dataclasses import dataclass


CONNECTION_STATUSES = {"pending", "accepted", "declined", "withdrawn"}
CONNECTION_INITIATORS = {"company", "rep", "admin"}


@dataclass(frozen=True)
class ConnectionPayload:
    company_id: int | str
    rep_id: int | str
    opportunity_id: int | str | None
    status: str
    message: str
    initiated_by: str


def normalize_prefixed_id(value, prefix: str) -> int | str | None:
    raw = str(value or "").replace(prefix, "").strip()
    if not raw:
        return None
    return int(raw) if raw.isdigit() else raw


def normalize_connection_status(status: str | None) -> str:
    value = (status or "pending").strip().lower()
    return value if value in CONNECTION_STATUSES else "pending"


def normalize_initiator(value: str | None) -> str:
    initiator = (value or "company").strip().lower()
    return initiator if initiator in CONNECTION_INITIATORS else "company"


def build_connection_payload(
    *,
    company_id,
    rep_id,
    opportunity_id=None,
    message: str = "",
    initiated_by: str = "company",
) -> ConnectionPayload:
    company_key = normalize_prefixed_id(company_id, "co-")
    rep_key = normalize_prefixed_id(rep_id, "db-")
    opportunity_key = normalize_prefixed_id(opportunity_id, "opp-") if opportunity_id else None
    if company_key is None:
        raise ValueError("Choose a company profile first.")
    if rep_key is None:
        raise ValueError("Choose a live rep profile first.")
    return ConnectionPayload(
        company_id=company_key,
        rep_id=rep_key,
        opportunity_id=opportunity_key,
        status="pending",
        message=(message or "").strip()[:1000],
        initiated_by=normalize_initiator(initiated_by),
    )


def duplicate_open_connection(connections: list[dict], *, company_id, rep_id, opportunity_id=None) -> dict | None:
    company_key = str(normalize_prefixed_id(company_id, "co-"))
    rep_key = str(normalize_prefixed_id(rep_id, "db-"))
    opportunity_key = normalize_prefixed_id(opportunity_id, "opp-") if opportunity_id else None
    for connection in connections:
        status = normalize_connection_status(connection.get("status"))
        if status not in {"pending", "accepted"}:
            continue
        same_company = str(connection.get("company_id") or "") == company_key
        same_rep = str(connection.get("rep_id") or "") == rep_key
        existing_opp = connection.get("opportunity_id")
        same_opp = str(existing_opp or "") == str(opportunity_key or "")
        if same_company and same_rep and same_opp:
            return connection
    return None


def contact_visible(connection: dict) -> bool:
    return normalize_connection_status(connection.get("status")) == "accepted"
