"""FirstKnock plans: Free and Pro ($29/mo or $290/yr) through Stripe Checkout.

The plan lives in the user's Supabase `app_metadata` (users can't edit it; only the service key can),
so there's no billing table. Stripe tells us about changes through a signed webhook.

Free: top FREE_LEADS leads per search, FREE_PIPELINE saved leads, no weekly email, CSV or contact lookup.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import requests
from fastapi import HTTPException

from api.settings import Settings

FREE_LEADS = 10
FREE_PIPELINE = 25
PRO_STATUSES = {"active", "trialing", "past_due"}  # past_due keeps access while Stripe retries the card
STRIPE = "https://api.stripe.com/v1"


def is_pro(app_metadata: dict | None) -> bool:
    meta = app_metadata or {}
    return meta.get("plan") in ("pro", "admin") and meta.get("plan_status", "active") in PRO_STATUSES


def require_pro(pro: bool, feature: str) -> None:
    if not pro:
        raise HTTPException(402, f"{feature} is a Pro feature. Upgrade on the Account page.")


def limit_rows(rows: list[dict], pro: bool) -> tuple[list[dict], bool]:
    """-> (rows to show, limited?)."""
    if pro or len(rows) <= FREE_LEADS:
        return rows, False
    return rows[:FREE_LEADS], True


# --------------------------------------------------------------------------- #
# Stripe
# --------------------------------------------------------------------------- #
def _stripe(settings: Settings, path: str, data: dict) -> dict:
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Payments aren't set up on this server yet.")
    r = requests.post(f"{STRIPE}/{path}", data=data, auth=(settings.stripe_secret_key, ""), timeout=30)
    if r.status_code >= 400:
        msg = (r.json().get("error") or {}).get("message", "") if r.headers.get("content-type", "").startswith(
            "application/json") else ""
        raise HTTPException(502, f"Stripe error: {msg or r.status_code}")
    return r.json()


def checkout_url(settings: Settings, user_id: str, email: str, interval: str, customer_id: str = "") -> str:
    price = settings.stripe_price_yearly if interval == "year" else settings.stripe_price_monthly
    if not price:
        raise HTTPException(503, "Payments aren't set up on this server yet.")
    data = {
        "mode": "subscription",
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        "client_reference_id": user_id,
        "subscription_data[metadata][user_id]": user_id,
        "allow_promotion_codes": "true",
        "success_url": f"{settings.web_url}/account/?upgraded=1",
        "cancel_url": f"{settings.web_url}/account/",
    }
    if customer_id:
        data["customer"] = customer_id
    else:
        data["customer_email"] = email
    return _stripe(settings, "checkout/sessions", data)["url"]


def portal_url(settings: Settings, customer_id: str) -> str:
    if not customer_id:
        raise HTTPException(404, "No billing account yet — upgrade first.")
    return _stripe(settings, "billing_portal/sessions",
                   {"customer": customer_id, "return_url": f"{settings.web_url}/account/"})["url"]


def verify_signature(payload: bytes, header: str, secret: str, now: float | None = None,
                     tolerance: int = 300) -> dict:
    """Check Stripe's `Stripe-Signature` header (HMAC-SHA256 of "t.payload") and return the event."""
    if not secret:
        raise HTTPException(503, "Webhook secret not configured.")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    sigs = [p.split("=", 1)[1] for p in header.split(",") if p.startswith("v1=")]
    try:
        ts = int(parts.get("t", ""))
    except ValueError:
        raise HTTPException(400, "Bad signature header.") from None
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise HTTPException(400, "Bad signature.")
    if abs((now or time.time()) - ts) > tolerance:
        raise HTTPException(400, "Signature too old.")
    return json.loads(payload)


# --------------------------------------------------------------------------- #
# Supabase user app_metadata
# --------------------------------------------------------------------------- #
def set_plan(settings: Settings, user_id: str, fields: dict) -> None:
    key = settings.supabase_service_key
    if not key:
        raise HTTPException(503, "Accounts are not configured on this server.")
    r = requests.put(f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                     headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                     json={"app_metadata": fields}, timeout=20)
    if r.status_code >= 400:
        raise HTTPException(502, f"Couldn't update the account ({r.status_code}).")


def handle_event(settings: Settings, event: dict) -> str:
    """Apply a Stripe event to the user's plan. Returns what happened (for logs/tests)."""
    kind, obj = event.get("type", ""), (event.get("data") or {}).get("object") or {}
    if kind == "checkout.session.completed" and obj.get("mode") == "subscription":
        user_id = obj.get("client_reference_id")
        if not user_id:
            return "ignored: no user"
        set_plan(settings, user_id, {"plan": "pro", "plan_status": "active",
                                     "stripe_customer_id": obj.get("customer"),
                                     "stripe_subscription_id": obj.get("subscription")})
        return f"pro: {user_id}"
    if kind in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        user_id = (obj.get("metadata") or {}).get("user_id")
        if not user_id:
            return "ignored: no user"
        status = "canceled" if kind.endswith("deleted") else obj.get("status", "")
        set_plan(settings, user_id, {
            "plan": "pro" if status in PRO_STATUSES else "free", "plan_status": status,
            "stripe_customer_id": obj.get("customer"), "stripe_subscription_id": obj.get("id"),
            "plan_renews_at": obj.get("current_period_end") or (obj.get("items", {}).get("data") or [{}])[0].get(
                "current_period_end"),
        })
        return f"{status}: {user_id}"
    return f"ignored: {kind}"
