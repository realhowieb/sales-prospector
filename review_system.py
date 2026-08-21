"""Review payloads, status normalization, rating clamping, duplicate detection, and rating summaries."""

from __future__ import annotations

from dataclasses import dataclass


REVIEW_STATUSES = {"pending", "approved", "rejected"}


@dataclass(frozen=True)
class ReviewPayload:
    rep_id: str
    rating: int
    reviewer: str
    title: str
    review: str
    verified_relationship: bool
    status: str = "pending"
    lead_id: int | None = None
    company_id: int | None = None
    opportunity_id: int | None = None


def normalize_review_status(status: str | None) -> str:
    value = (status or "").strip().lower()
    return value if value in REVIEW_STATUSES else "pending"


def approved_review(review: dict) -> bool:
    return normalize_review_status(review.get("status")) == "approved"


def clamp_rating(value) -> int:
    try:
        rating = int(value)
    except (TypeError, ValueError):
        rating = 0
    if rating < 1 or rating > 5:
        raise ValueError("Rating must be between 1 and 5.")
    return rating


def normalize_rep_review_id(rep_id) -> str:
    return str(rep_id or "").replace("db-", "").strip()


def has_duplicate_review(reviews: list[dict], *, rep_id, lead_id=None, reviewer: str = "") -> bool:
    rep_key = normalize_rep_review_id(rep_id)
    reviewer_key = (reviewer or "").strip().lower()
    for review in reviews:
        if normalize_review_status(review.get("status")) == "rejected":
            continue
        if lead_id and str(review.get("lead_id") or "") == str(lead_id):
            return True
        if reviewer_key and normalize_rep_review_id(review.get("rep_id")) == rep_key:
            if (review.get("reviewer") or review.get("customer_name") or "").strip().lower() == reviewer_key:
                return True
    return False


def build_review_payload(
    *,
    rep_id,
    rating,
    reviewer: str,
    title: str = "",
    review: str = "",
    verified_relationship: bool = False,
    lead_id=None,
    company_id=None,
    opportunity_id=None,
) -> ReviewPayload:
    reviewer_name = (reviewer or "").strip() or "Anonymous"
    return ReviewPayload(
        rep_id=normalize_rep_review_id(rep_id),
        rating=clamp_rating(rating),
        reviewer=reviewer_name,
        title=(title or "").strip(),
        review=(review or "").strip(),
        verified_relationship=bool(verified_relationship),
        lead_id=int(lead_id) if str(lead_id or "").isdigit() else None,
        company_id=int(company_id) if str(company_id or "").isdigit() else None,
        opportunity_id=int(opportunity_id) if str(opportunity_id or "").isdigit() else None,
    )


def reviews_summary(reviews: list[dict]) -> dict:
    agg: dict = {}
    for review in reviews:
        if not approved_review(review):
            continue
        rid = normalize_rep_review_id(review.get("rep_id"))
        if not rid:
            continue
        bucket = agg.setdefault(rid, {"sum": 0, "count": 0, "recent": []})
        try:
            bucket["sum"] += clamp_rating(review.get("rating"))
        except ValueError:
            continue
        bucket["count"] += 1
        if len(bucket["recent"]) < 3 and (review.get("review") or review.get("comment") or review.get("title")):
            bucket["recent"].append(review)
    for bucket in agg.values():
        bucket["avg"] = round(bucket["sum"] / bucket["count"], 1) if bucket["count"] else 0.0
    return agg
