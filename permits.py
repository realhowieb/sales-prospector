"""Build-outs & move-ins: commercial building permits as trigger leads.

A company moving into a space nearly always pulls a tenant-improvement (TI) permit or a
Use & Occupancy (U&O) certificate first — weeks before move-in, while it is still picking
low-voltage, alarm, camera and access-control vendors.

Sources (free, no key):
- Palo Alto: Development Center Permits on data.paloalto.gov (Junar). The city publishes the
  data in date-range slices; the newest slice's CSV id changes, so we read it off the dashboard
  page and fall back to the last known id.

Framework-free so the API and the Streamlit app can share it.
"""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import date, datetime, timedelta

from trigger_leads import CHAINS

PALO_ALTO_DASHBOARD = "https://data.paloalto.gov/dashboards/7712/development-center-permits/"
PALO_ALTO_CSV = "https://data.paloalto.gov/rest/datastreams/{}/data.csv"
PALO_ALTO_FALLBACK_ID = "298792"  # "Mar 2025-Nov 2026" slice

# Cities we have permit data for (the rest of the territory is skipped and reported back).
COVERED_CITIES = ("Palo Alto",)

RESIDENTIAL = re.compile(
    r"^\s*(res|mf|mfr|sfr|multi-?fam\w*|residential|adu|jadu|duplex|townhome|condo)\b|"
    r"single[- ]family|\bADU\b|\bJADU\b|residential|multi-?family|duplex|apartment|dwelling",
    re.I,
)
COMMERCIAL_PREFIX = re.compile(r"^\s*(com|comm|commercial|non-?res\w*|office|retail|restaurant)\b", re.I)
SIGNALS = [  # (signal, base score, pattern) — first match wins
    ("Move-in (U&O)", 92, re.compile(r"\bU\s*&\s*O\b|use (and|&) occupancy|certificate of (use|occupancy)", re.I)),
    ("Build-out (TI)", 88, re.compile(r"\bT\.?\s?I\b|tenant improvement|build-?out|demis(e|ing)", re.I)),
    ("New building", 80, re.compile(r"\bnew (\d+[- ]story )?(commercial |office |retail )?building\b|"
                                    r"new construction|ground[- ]up", re.I)),
    ("Remodel", 62, re.compile(r"remodel|renovat|alteration|interior improvement", re.I)),
]
MINOR = re.compile(r"\b(re-?roof|roofing|solar|photovoltaic|\bPV\b|ev charg|water heater|sign(age)?|"
                   r"fence|repair|replace|like-for-like|demo(lition)? only|grading|tree)\b", re.I)
MALL = re.compile(r"shopping cent(er|re)|space\s*#", re.I)
QUOTED = re.compile(r"[‘'“\"]\s*([^‘’'“”\"]{2,60}?)\s*[’'”\"]")
FOR_TENANT = re.compile(r"\b(?:for|occupied by|tenant[: ]+)\s+([A-Z][\w&.,' -]{2,50}?)(?=[.;,]| - |\s+(?:in|at|on|to)\b|$)")
NOT_TENANT = re.compile(r"^(a|an|the|new|existing|tenant|interior|exterior|space|suite|office|retail|spec|"
                        r"restaurant|landlord|owner|building|level|floor|shell|core|work|use|com|comm|"
                        r"commercial|minor|partial|phase|project|revision|\*)\b", re.I)
LEAD_NAME = re.compile(r"^\s*([A-Z][A-Za-z0-9&.' -]{1,40}?)\s+(?:office\s+|lab\s+)?(?:T\.?I\b|U\s*&\s*O\b|"
                       r"tenant improvement|use (?:and|&) occupancy)", re.I)
BLDG = re.compile(r"\s+(bldg|building)\s+\w+$", re.I)


def latest_csv_url(dashboard_html: str) -> str:
    """Pick the newest date-range slice's CSV off the dashboard page."""
    best: tuple[date, str] | None = None
    for m in re.finditer(r"datastreams/(\d+)/data\.csv", dashboard_html):
        t = re.search(r"title:\s*['\"]([^'\"]+)", dashboard_html[m.end():m.end() + 1200])
        end = re.search(r"-\s*([A-Z][a-z]{2})\w*\s+(\d{4})\s*$", t.group(1)) if t else None
        if not end:
            continue
        try:
            end_date = datetime.strptime(f"{end.group(1)} {end.group(2)}", "%b %Y").date()
        except ValueError:
            continue
        if best is None or end_date > best[0]:
            best = (end_date, m.group(1))
    return PALO_ALTO_CSV.format(best[1] if best else PALO_ALTO_FALLBACK_ID)


def download_palo_alto(session, user_agent: str, timeout: int = 120) -> str:
    headers = {"User-Agent": user_agent}
    try:
        page = session.get(PALO_ALTO_DASHBOARD, headers=headers, timeout=30)
        url = latest_csv_url(page.text if page.status_code == 200 else "")
    except Exception:
        url = PALO_ALTO_CSV.format(PALO_ALTO_FALLBACK_ID)
    r = session.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.content.decode("utf-8-sig", errors="replace")


def money(value: str) -> float:
    try:
        return float(re.sub(r"[^\d.]", "", value or "") or 0)
    except ValueError:
        return 0.0


def tenant_of(description: str) -> str:
    """Best guess at the business moving in, e.g. "COM: TI and U&O for 'Rivian'" -> Rivian."""
    for m in QUOTED.finditer(description):
        name = m.group(1).strip(" .,-")
        if len(name) > 1 and not re.fullmatch(r"[\d,.\s]+", name) and not NOT_TENANT.match(name):
            return name
    m = re.match(r"^\s*([A-Z][A-Za-z0-9&.' -]{1,40}?)\s*:", description)  # "GOOGLE: ..." / "XAI: ..."
    if m and not COMMERCIAL_PREFIX.match(m.group(1)) and not RESIDENTIAL.match(m.group(1)) \
            and m.group(1).upper() not in {"MEP", "COM MEP", "COMM MEP", "CITY OF PALO ALTO"}:
        return m.group(1).strip().title() if m.group(1).isupper() else m.group(1).strip()
    m = LEAD_NAME.match(description)  # "Sweetgreen TI ..." / "GIGASCALE CAPITAL U&O ..."
    if m and not NOT_TENANT.match(m.group(1)) and not RESIDENTIAL.match(m.group(1)):
        name = m.group(1).strip()
        return name.title() if name.isupper() else name
    m = FOR_TENANT.search(description)
    if m and not NOT_TENANT.match(m.group(1)):
        return m.group(1).strip(" .,-")
    return ""


def _clean_tenant(name: str) -> str:
    return BLDG.sub("", name).strip(" .,-")


def classify(description: str) -> tuple[str, int] | None:
    """Commercial signal for a permit description, or None if residential / not a lead."""
    if RESIDENTIAL.search(description) and not COMMERCIAL_PREFIX.match(description):
        return None
    commercial = bool(COMMERCIAL_PREFIX.match(description)) or bool(tenant_of(description))
    for signal, base, pat in SIGNALS:
        if pat.search(description):
            if signal == "Remodel" and not commercial:
                return None
            return signal, base
    if commercial and not MINOR.search(description):
        return "Other commercial", 50
    return None


def parse_palo_alto(csv_text: str, today: date | None = None, days: int = 90,
                    include_minor: bool = False) -> list[dict]:
    """Palo Alto permit CSV -> scored lead rows, best first, one per permit."""
    today = today or date.today()
    since = today - timedelta(days=days)
    rows: dict[str, dict] = {}
    for rec in csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))):
        desc = " ".join((rec.get("DESCRIPTION") or "").split())
        status = (rec.get("RECORD STATUS") or "").strip()
        if not desc or status in {"Void", "Abandoned", "Withdrawn"}:
            continue
        try:
            opened = datetime.strptime(rec.get("DATE OPENED", ""), "%m/%d/%Y").date()
        except ValueError:
            continue
        if opened < since:
            continue
        hit = classify(desc)
        if not hit:
            continue
        signal, score = hit
        if signal == "Other commercial" and not include_minor:
            continue
        value = money(rec.get("JOB VALUE", ""))
        tenant = _clean_tenant(tenant_of(desc))
        notes = []
        if value >= 100:  # $1 placeholders on standalone U&Os
            score += min(10, round(2 * math.log10(value / 10_000))) if value > 10_000 else 0
        score -= min(10, (today - opened).days // 10)
        if status in {"Finaled", "Closed"}:
            score -= 8
            notes.append("Work finished")
        if MALL.search(desc):
            score -= 10
            notes.append("Mall tenant — may use a national vendor")
        chain = bool(tenant and CHAINS.search(tenant))
        if chain:
            notes.append("Chain")
        addr = (rec.get("ADDR FULL LINE") or "").strip()
        permit = (rec.get("RECORD ID") or "").strip()
        rows[permit] = {
            "Score": max(1, min(100, score)),
            "Opened": opened.isoformat(),
            "Signal": signal,
            "Business": tenant,
            "Address": re.sub(r",?\s*PALO ALTO,?\s*CA\s*\d*\s*$", "", addr, flags=re.I).title(),
            "City": "Palo Alto",
            "Value": round(value) if value >= 100 else None,
            "Status": status,
            "Description": desc,
            "Contractor": (rec.get("BUSINESS NAME") or "").strip().title(),
            "Permit #": permit,
            "Note": " · ".join(notes),
            "Chain": chain,
            "Map": "https://www.google.com/maps/search/?api=1&query=" + re.sub(r"\s+", "+", addr),
        }
    return sorted(rows.values(), key=lambda r: (r["Score"], r["Opened"]), reverse=True)
