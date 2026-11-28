"""Weekly email of leads a rep hasn't been sent yet.

Each user keeps one row in Supabase `lead_digests` (territory, sources, on/off, and the keys of
leads already emailed). Every Monday a scheduler calls POST /internal/digest/run; for each active
row we gather the current leads, drop the ones already sent, email the rest, and remember them.
"""
from __future__ import annotations

import hashlib
import html
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Callable

import requests
from fastapi import HTTPException

import trades
from api.settings import Settings

SOURCES = {
    "permits": "🏗️ Build-outs & move-ins",
    "licenses": "🍷 Liquor licenses",
    "news": "📰 Openings, leases & moves",
    "map": "🗺️ New on the map",
}
DEFAULT_TERRITORY = "Peninsula (Belmont → Mountain View)"
PER_SECTION = 10       # leads shown per source in the email; the rest are on the site
SEEN_CAP = 4000        # keys remembered per user
RESEND_AFTER = timedelta(days=6)

Loader = Callable[[tuple[str, ...]], list[dict]]


# --------------------------------------------------------------------------- #
# Lead identity and formatting
# --------------------------------------------------------------------------- #
def lead_key(source: str, row: dict) -> str:
    if source == "permits":
        return f"permit:{row.get('Permit #')}"
    if source == "licenses":
        return f"abc:{row.get('ABC file #')}"
    if source == "map":
        return f"osm:{row.get('OSM id')}"
    raw = str(row.get("Link") or row.get("Headline") or "")
    return "news:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def _money(v) -> str:
    if not isinstance(v, (int, float)) or v < 100:
        return ""
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${round(v / 1e3)}K"


def describe(source: str, row: dict) -> tuple[str, str, str]:
    """(title, detail, link) for one lead."""
    title, detail, link = _describe(source, row)
    if row.get("Pitch"):
        detail = f"{detail} · Pitch: {row['Pitch']}"
    return title, detail, link


def _describe(source: str, row: dict) -> tuple[str, str, str]:
    """(title, detail, link) without the pitch."""
    if source == "permits":
        title = row.get("Business") or row.get("Address") or "Commercial permit"
        detail = " · ".join(x for x in (row.get("Signal"), row.get("Address"), _money(row.get("Value")),
                                         f"filed {row.get('Opened')}", row.get("Contractor") and
                                         f"contractor {row.get('Contractor')}") if x)
        return title, detail, row.get("Map") or ""
    if source == "licenses":
        detail = " · ".join(x for x in (row.get("Trigger"), f"{row.get('Address')}, {row.get('City')}",
                                         row.get("Type"), row.get("Person to ask for") and
                                         f"ask for {row.get('Person to ask for')}") if x)
        return row.get("Business") or "New license", detail, row.get("ABC record") or ""
    if source == "news":
        detail = " · ".join(x for x in (row.get("Signal"), row.get("City"), row.get("Date"), row.get("Source")) if x)
        return row.get("Headline") or "News", detail, row.get("Link") or ""
    detail = " · ".join(x for x in (row.get("Kind"), row.get("Address"), row.get("Phone"),
                                     f"added {row.get('Added')}") if x)
    return row.get("Business") or "New listing", detail, row.get("Map") or ""


def build_email(territory: str, sections: dict[str, list[dict]], web_url: str,
                totals: dict[str, int] | None = None) -> tuple[str, str, str]:
    """-> (subject, plain text, html)."""
    totals = totals or {s: len(rows) for s, rows in sections.items()}
    n = sum(totals.values())
    subject = f"FirstKnock: {n} new lead{'s' if n != 1 else ''} this week" if n else "FirstKnock: no new leads this week"
    text = [f"New leads for {territory}", ""]
    parts = [f'<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px;color:#1d1d1f">'
             f'<h2 style="margin:0 0 4px">🚪 FirstKnock — new this week</h2>'
             f'<p style="margin:0 0 16px;color:#6e6e73">{html.escape(territory)}</p>']
    if not n:
        text.append("Nothing new since last week. We'll keep watching.")
        parts.append("<p>Nothing new since last week. We'll keep watching.</p>")
    for source, rows in sections.items():
        if not rows:
            continue
        label = SOURCES[source]
        more = totals.get(source, len(rows)) - len(rows)
        text += [f"{label} ({totals.get(source, len(rows))} new)", ""]
        parts.append(f'<h3 style="margin:20px 0 8px">{html.escape(label)} '
                     f'<span style="color:#6e6e73;font-weight:400">({totals.get(source, len(rows))} new)</span></h3>')
        for row in rows:
            title, detail, link = describe(source, row)
            text.append(f"- {title} — {detail}" + (f"\n  {link}" if link else ""))
            t = html.escape(str(title))
            t = f'<a href="{html.escape(link)}" style="color:#d4572a;text-decoration:none">{t}</a>' if link else t
            parts.append(f'<div style="margin:0 0 10px"><b>{t}</b><br>'
                         f'<span style="color:#6e6e73;font-size:14px">{html.escape(detail)}</span></div>')
        if more > 0:
            text.append(f"  …and {more} more on the site")
            parts.append(f'<p style="color:#6e6e73;font-size:14px">…and {more} more on the site.</p>')
        text.append("")
    text += [f"Open FirstKnock: {web_url}", f"Change or stop these emails: {web_url}/alerts/"]
    parts.append(f'<p style="margin-top:24px"><a href="{web_url}" style="background:#d4572a;color:#fff;'
                 f'padding:10px 16px;border-radius:8px;text-decoration:none">Open FirstKnock</a></p>'
                 f'<p style="color:#6e6e73;font-size:12px">You get this because you turned on the weekly email. '
                 f'<a href="{web_url}/alerts/">Change or stop it</a>.</p></div>')
    return subject, "\n".join(text), "".join(parts)


# --------------------------------------------------------------------------- #
# Gathering
# --------------------------------------------------------------------------- #
def gather(cities: tuple[str, ...], sources: list[str], loaders: dict[str, Loader]) -> tuple[dict, dict]:
    """-> ({source: rows}, {source: error message}). One failing source doesn't sink the email."""
    found, errors = {}, {}
    for s in sources:
        if s not in loaders:
            continue
        try:
            found[s] = loaders[s](cities)
        except HTTPException as exc:
            errors[s] = str(exc.detail)
        except Exception as exc:  # noqa: BLE001 — a flaky public feed shouldn't stop the run
            errors[s] = type(exc).__name__
    return found, errors


def for_trade(found: dict[str, list[dict]], trade: str | None) -> dict[str, list[dict]]:
    return {source: trades.rescore(source, rows, trade) for source, rows in found.items()}


def fresh(found: dict[str, list[dict]], seen: set[str]) -> tuple[dict, dict, list[str]]:
    """-> (sections capped for the email, totals per source, every new key)."""
    sections, totals, keys = {}, {}, []
    for source, rows in found.items():
        new = [r for r in rows if lead_key(source, r) not in seen]
        keys += [lead_key(source, r) for r in new]
        totals[source] = len(new)
        sections[source] = new[:PER_SECTION]
    return sections, totals, keys


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #
def send_email(settings: Settings, to: str, subject: str, text: str, body_html: str) -> None:
    if not (settings.smtp_host and settings.smtp_pass and settings.smtp_from):
        raise HTTPException(503, "Email isn't configured on this server yet.")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, settings.smtp_from, to
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, 465, context=ssl.create_default_context(), timeout=30) as s:
            s.login(settings.smtp_user, settings.smtp_pass)
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(settings.smtp_user, settings.smtp_pass)
            s.send_message(msg)


# --------------------------------------------------------------------------- #
# Subscriptions (Supabase `lead_digests`, service key)
# --------------------------------------------------------------------------- #
def _headers(settings: Settings, extra: dict | None = None) -> dict:
    if not settings.supabase_service_key:
        raise HTTPException(503, "The weekly email is not configured on this server.")
    key = settings.supabase_service_key
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", **(extra or {})}


def _check(r) -> None:
    if r.status_code == 404 or (r.status_code >= 400 and "lead_digests" in r.text):
        raise HTTPException(503, "The weekly email table isn't set up yet (run migrations/021_lead_digests.sql).")
    if r.status_code >= 400:
        raise HTTPException(502, f"Database error ({r.status_code}). Try again.")


PUBLIC = ("active", "territory", "sources", "last_sent_at")


def get_subscription(settings: Settings, user_id: str) -> dict | None:
    r = requests.get(f"{settings.supabase_url}/rest/v1/lead_digests", headers=_headers(settings),
                     params={"user_id": f"eq.{user_id}", "select": "*"}, timeout=20)
    _check(r)
    rows = r.json()
    return rows[0] if rows else None


def save_subscription(settings: Settings, user_id: str, email: str, active: bool, territory: str,
                      sources: list[str]) -> dict:
    row = {"user_id": user_id, "email": email, "active": active, "territory": territory[:500],
           "sources": [s for s in sources if s in SOURCES],
           "updated_at": datetime.now(timezone.utc).isoformat()}
    r = requests.post(f"{settings.supabase_url}/rest/v1/lead_digests",
                      headers=_headers(settings, {"Prefer": "resolution=merge-duplicates,return=representation"}),
                      params={"on_conflict": "user_id"}, json=[row], timeout=20)
    _check(r)
    out = r.json()
    return out[0] if out else row


def user_profile(settings: Settings, user_id: str) -> tuple[str, bool]:
    """(trade, is_pro) from the rep's Supabase account; (security, False) if unreadable."""
    from api import billing  # local import: billing imports settings only, but keep digest importable alone

    try:
        r = requests.get(f"{settings.supabase_url}/auth/v1/admin/users/{user_id}", headers=_headers(settings),
                         timeout=15)
        data = r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        data = {}
    trade = (data.get("user_metadata") or {}).get("trade")
    return (trade if trade in trades.TRADES else trades.DEFAULT_TRADE), billing.is_pro(data.get("app_metadata"))


def due_subscriptions(settings: Settings, now: datetime) -> list[dict]:
    cutoff = (now - RESEND_AFTER).isoformat()
    r = requests.get(f"{settings.supabase_url}/rest/v1/lead_digests", headers=_headers(settings),
                     params={"active": "is.true", "select": "*",
                             "or": f"(last_sent_at.is.null,last_sent_at.lt.{cutoff})"}, timeout=30)
    _check(r)
    return r.json()


def mark_sent(settings: Settings, user_id: str, seen: list[str], now: datetime) -> None:
    r = requests.patch(f"{settings.supabase_url}/rest/v1/lead_digests", headers=_headers(settings),
                       params={"user_id": f"eq.{user_id}"},
                       json={"seen": seen[-SEEN_CAP:], "last_sent_at": now.isoformat()}, timeout=20)
    _check(r)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
def run_all(settings: Settings, loaders: dict[str, Loader], parse_cities: Callable[[str], tuple[str, ...]],
            now: datetime | None = None, dry_run: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    report = {"sent": 0, "skipped": 0, "failed": 0, "errors": []}
    gathered: dict[tuple, tuple[dict, dict]] = {}  # territories shared by several reps are fetched once
    for sub in due_subscriptions(settings, now):
        try:
            cities = parse_cities(sub.get("territory") or DEFAULT_TERRITORY)
            sources = [s for s in (sub.get("sources") or list(SOURCES)) if s in SOURCES]
            key = (cities, tuple(sorted(sources)))
            if key not in gathered:
                gathered[key] = gather(cities, sources, loaders)
            found, errors = gathered[key]
            trade, pro = user_profile(settings, sub["user_id"])
            if not pro:  # the weekly email is a Pro feature; a lapsed plan just stops it
                report["skipped"] += 1
                continue
            seen = list(sub.get("seen") or [])
            sections, totals, new_keys = fresh(for_trade(found, trade), set(seen))
            subject, text, body = build_email(sub.get("territory") or DEFAULT_TERRITORY, sections,
                                              settings.web_url, totals)
            if errors:
                text += "\n\n(Couldn't reach: " + ", ".join(SOURCES[s] for s in errors) + " — they'll be back next week.)"
            if not dry_run:
                send_email(settings, sub["email"], subject, text, body)
                mark_sent(settings, sub["user_id"], seen + new_keys, now)
            report["sent"] += 1
        except Exception as exc:  # noqa: BLE001 — keep going for the other reps
            report["failed"] += 1
            report["errors"].append(f"{sub.get('user_id', '?')[:8]}: {getattr(exc, 'detail', type(exc).__name__)}")
    return report


def send_test(settings: Settings, email: str, territory: str, sources: list[str], loaders: dict[str, Loader],
              parse_cities: Callable[[str], tuple[str, ...]], trade: str | None = None) -> dict:
    """Send what the email looks like right now (ignores and doesn't change 'already sent')."""
    found, errors = gather(parse_cities(territory), sources, loaders)
    sections, totals, _ = fresh(for_trade(found, trade), set())
    subject, text, body = build_email(territory, sections, settings.web_url, totals)
    send_email(settings, email, "[Test] " + subject, text, body)
    return {"sent_to": email, "leads": sum(totals.values()), "unavailable": sorted(errors)}
