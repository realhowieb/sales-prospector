from __future__ import annotations

from dataclasses import dataclass


COLLECTIONS = ["Saved", "Contact Later", "Strong Candidates"]
TARGET_TYPES = {"rep", "company", "opportunity"}
OWNER_TYPES = {"anonymous", "company", "rep", "user"}


@dataclass(frozen=True)
class ShortlistItem:
    owner_type: str
    owner_id: str
    session_key: str
    target_type: str
    target_id: str
    collection: str
    notes: str = ""


def normalize_collection(value: str | None) -> str:
    return value if value in COLLECTIONS else "Saved"


def normalize_target_type(value: str) -> str:
    target_type = (value or "").strip().lower()
    if target_type not in TARGET_TYPES:
        raise ValueError("Unsupported shortlist target type.")
    return target_type


def normalize_owner_type(value: str | None) -> str:
    owner_type = (value or "anonymous").strip().lower()
    return owner_type if owner_type in OWNER_TYPES else "anonymous"


def target_key(target_type: str, target_id) -> str:
    return f"{normalize_target_type(target_type)}:{str(target_id or '').strip()}"


def build_shortlist_item(
    *,
    target_type: str,
    target_id,
    collection: str = "Saved",
    owner_type: str = "anonymous",
    owner_id: str = "",
    session_key: str = "",
    notes: str = "",
) -> ShortlistItem:
    tid = str(target_id or "").strip()
    if not tid:
        raise ValueError("Cannot save an item without an ID.")
    return ShortlistItem(
        owner_type=normalize_owner_type(owner_type),
        owner_id=str(owner_id or "").strip(),
        session_key=str(session_key or "").strip(),
        target_type=normalize_target_type(target_type),
        target_id=tid,
        collection=normalize_collection(collection),
        notes=(notes or "").strip()[:1000],
    )


def upsert_session_shortlist(items: list[dict], item: ShortlistItem) -> tuple[list[dict], bool]:
    key = target_key(item.target_type, item.target_id)
    for existing in items:
        if target_key(existing.get("target_type"), existing.get("target_id")) == key:
            existing["collection"] = item.collection
            existing["notes"] = item.notes
            return items, False
    items.append(item.__dict__.copy())
    return items, True


def remove_session_shortlist(items: list[dict], target_type: str, target_id) -> list[dict]:
    key = target_key(target_type, target_id)
    return [item for item in items if target_key(item.get("target_type"), item.get("target_id")) != key]


def is_saved(items: list[dict], target_type: str, target_id) -> bool:
    key = target_key(target_type, target_id)
    return any(target_key(item.get("target_type"), item.get("target_id")) == key for item in items)
