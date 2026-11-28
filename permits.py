"""Build-outs & move-ins: commercial building permits as trigger leads.

A company moving into a space nearly always pulls a tenant-improvement (TI) permit or a
Use & Occupancy (U&O) certificate first — weeks before move-in, while it is still picking
low-voltage, alarm, camera and access-control vendors.

Sources (free, no key):
- Palo Alto: Development Center Permits on data.paloalto.gov (Junar). The city publishes the
  data in date-range slices; the newest slice's CSV id changes, so we read it off the dashboard
  page and fall back to the last known id.
- San Jose: daily CSVs on data.sanjoseca.gov (last 30 days + all active permits).
- San Francisco: DataSF building permits (Socrata API), filtered to commercial uses.

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
COVERED_CITIES = ("Palo Alto", "San Jose", "San Francisco")

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


# --------------------------------------------------------------------------- #
# Shared scoring: every city adapter yields the same record shape
# --------------------------------------------------------------------------- #
FINISHED = {"Finaled", "Closed", "complete", "Complete", "Final", "Finaled/Closed"}
CLOSED_OUT = {"Void", "Abandoned", "Withdrawn", "cancelled", "withdrawn", "expired", "Expired", "disapproved"}


def tidy_address(addr: str) -> str:
    """'101 02Nd St' -> '101 2nd St'."""
    addr = re.sub(r"\b0+(\d)", r"\1", addr)
    return re.sub(r"(\d)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), addr)


def score_record(rec: dict, today: date) -> dict:
    """rec: permit, opened (date), signal, base, business, address, city, value, status, description,
    contractor, map. -> lead row."""
    score, notes = rec["base"], []
    value = rec.get("value") or 0.0
    if value > 10_000:
        score += min(10, round(2 * math.log10(value / 10_000)))
    score -= min(10, (today - rec["opened"]).days // 10)
    if rec.get("status") in FINISHED:
        score -= 8
        notes.append("Work finished")
    if MALL.search(rec["description"]):
        score -= 10
        notes.append("Mall tenant — may use a national vendor")
    tenant = rec.get("business") or ""
    chain = bool(tenant and CHAINS.search(tenant))
    if chain:
        notes.append("Chain")
    return {
        "Score": max(1, min(100, score)),
        "Opened": rec["opened"].isoformat(),
        "Signal": rec["signal"],
        "Business": tenant,
        "Address": tidy_address(rec["address"]),
        "City": rec["city"],
        "Value": round(value) if value >= 100 else None,
        "Status": rec.get("status") or "",
        "Description": rec["description"],
        "Contractor": rec.get("contractor") or "",
        "Permit #": rec["permit"],
        "Note": " · ".join(notes),
        "Chain": chain,
        "Map": rec.get("map") or "https://www.google.com/maps/search/?api=1&query="
        + re.sub(r"\s+", "+", f"{rec['address']}, {rec['city']}, CA"),
    }


def _finish(records, today: date) -> list[dict]:
    rows = {r["permit"]: score_record(r, today) for r in records}
    return sorted(rows.values(), key=lambda r: (r["Score"], r["Opened"]), reverse=True)


# --------------------------------------------------------------------------- #
# Palo Alto — data.paloalto.gov CSV
# --------------------------------------------------------------------------- #
def palo_alto_records(csv_text: str, since: date, include_minor: bool):
    for rec in csv.DictReader(io.StringIO(csv_text.lstrip("﻿"))):
        desc = " ".join((rec.get("DESCRIPTION") or "").split())
        status = (rec.get("RECORD STATUS") or "").strip()
        if not desc or status in CLOSED_OUT:
            continue
        try:
            opened = datetime.strptime(rec.get("DATE OPENED", ""), "%m/%d/%Y").date()
        except ValueError:
            continue
        if opened < since:
            continue
        hit = classify(desc)
        if not hit or (hit[0] == "Other commercial" and not include_minor):
            continue
        addr = (rec.get("ADDR FULL LINE") or "").strip()
        value = money(rec.get("JOB VALUE", ""))
        yield {
            "permit": (rec.get("RECORD ID") or "").strip(), "opened": opened, "signal": hit[0], "base": hit[1],
            "business": _clean_tenant(tenant_of(desc)),
            "address": re.sub(r",?\s*PALO ALTO,?\s*CA\s*\d*\s*$", "", addr, flags=re.I).title(),
            "city": "Palo Alto", "value": value if value >= 100 else 0.0,  # $1 placeholders on standalone U&Os
            "status": status, "description": desc, "contractor": (rec.get("BUSINESS NAME") or "").strip().title(),
            "map": "https://www.google.com/maps/search/?api=1&query=" + re.sub(r"\s+", "+", addr),
        }


def parse_palo_alto(csv_text: str, today: date | None = None, days: int = 90,
                    include_minor: bool = False) -> list[dict]:
    """Palo Alto permit CSV -> scored lead rows, best first, one per permit."""
    today = today or date.today()
    return _finish(palo_alto_records(csv_text, today - timedelta(days=days), include_minor), today)


# --------------------------------------------------------------------------- #
# San Jose — data.sanjoseca.gov daily CSVs (active permits + last 30 days)
# --------------------------------------------------------------------------- #
SAN_JOSE_CSVS = [
    "https://data.sanjoseca.gov/dataset/2723cdec-a639-4b63-bded-175338c45473/resource/"
    "045b3678-e923-4002-b696-300955bc6d06/download/buildingpermits30.csv",
    "https://data.sanjoseca.gov/dataset/fd9ceb0c-75e0-402e-9fe3-3f6e04f2c23f/resource/"
    "761b7ae8-3be1-4ad6-923d-c7af6404a904/download/buildingpermitsactive.csv",
]
SJ_MINOR = re.compile(r"\b(sign|signs|signage|billboard|cell site|antenna|hvac|chiller|condenser|heat pump|"
                      r"water heater|photovoltaic|\bpv\b|solar|reroof|re-roof|switch ?gear|feeder|parking lot|"
                      r"pipe|tank|exhaust fan|ev charg|fence|demo\b|demolition|code violation|rack)\b", re.I)
SJ_WORK = re.compile(r"\b(t\.?i|tenant|improvement|install\w*|replace\w*|upgrade|sign|demo|ada|restroom|"
                     r"suite|ste|floor|landlord|shell|interior|electrical|mechanical|plumbing|service)\b", re.I)
SJ_SKIP_USES = {"Antenna/Cell Site", "Church", "Temporary Use", "Survey", "Service Station"}
SJ_PREFIX = re.compile(r"^\s*(\((star|e|b|m|p)\)\s*|srp\s+|epr\s+)+", re.I)


def download_san_jose(session, user_agent: str, timeout: int = 120) -> str:
    parts = []
    for url in SAN_JOSE_CSVS:
        r = session.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
        r.raise_for_status()
        parts.append(r.content.decode("utf-8-sig", errors="replace"))
    header = parts[0].splitlines()[0] if parts else ""
    return "\n".join([parts[0]] + ["\n".join(p.splitlines()[1:]) for p in parts[1:] if p.splitlines()[:1] == [header]])


def san_jose_records(csv_text: str, since: date, include_minor: bool):
    for rec in csv.DictReader(io.StringIO(csv_text.lstrip("﻿"))):
        if rec.get("FOLDERDESC") != "Commercial/Industrial":
            continue
        use, work = (rec.get("SUBTYPEDESCRIPTION") or "").strip(), (rec.get("WORKDESCRIPTION") or "").strip()
        name = " ".join((rec.get("FOLDERNAME") or "").split())
        if use in SJ_SKIP_USES:
            continue
        try:
            opened = datetime.strptime((rec.get("ISSUEDATE") or "").split(" ")[0], "%m/%d/%Y").date()
        except ValueError:
            continue
        if opened < since:
            continue
        if work == "New Construction":
            signal, base = "New building", 80
        elif work in ("Tenant Improvement", "Additions/Alterations", "Change of Occupancy"):
            minor = SJ_MINOR.search(name)
            if minor and not include_minor:
                continue
            signal, base = ("Other commercial", 50) if minor else ("Build-out (TI)", 88)
        else:
            continue
        bare = SJ_PREFIX.sub("", name)
        m = re.match(r"^(.*?)\s*\([^)]*\)\s*(.*)$", bare)  # "ALTERA (BEM 100%) TENANT IMPROVEMENT"
        business = m.group(1).strip(" -") if m else ""
        if m and not business and not SJ_WORK.search(m.group(2)):  # "SRP (BEMP100%) TERIYAKI CITY GRILL #3"
            business = m.group(2).strip(" -")
        business = re.sub(r"'S\b", "'s", business.title()) if business.isupper() else business
        if business.lower() in {"market-ready", "master file", ""}:
            business = ""
        addr = re.sub(r"\s*,?\s*SAN JOSE\s*CA.*$", "", " ".join((rec.get("gx_location") or "").split()), flags=re.I)
        yield {
            "permit": (rec.get("FOLDERNUMBER") or "").strip(), "opened": opened, "signal": signal, "base": base,
            "business": business, "address": addr.title(), "city": "San Jose",
            "value": money(rec.get("PERMITVALUATION", "")), "status": (rec.get("Status") or "").strip(),
            "description": f"{name} — {use}: {work}".strip(" —:"),
            "contractor": " ".join((rec.get("CONTRACTOR") or "").split()).title(),
        }


def parse_san_jose(csv_text: str, today: date | None = None, days: int = 90,
                   include_minor: bool = False) -> list[dict]:
    today = today or date.today()
    return _finish(san_jose_records(csv_text, today - timedelta(days=days), include_minor), today)


# --------------------------------------------------------------------------- #
# San Francisco — DataSF building permits (Socrata), commercial uses only
# --------------------------------------------------------------------------- #
SF_PERMITS = "https://data.sf.gov/resource/i98e-djp9.json"
SF_SKIP_USES = re.compile(r"family|apartment|dwelling|residential|church|school|vacant lot|parking|storage shed", re.I)


def download_san_francisco(session, user_agent: str, since: date, timeout: int = 120) -> list[dict]:
    where = (f"permit_creation_date >= '{since.isoformat()}T00:00:00' AND existing_use IS NOT NULL AND "
             "upper(existing_use) not like '%FAMILY%' AND upper(existing_use) not like '%APARTMENT%' AND "
             "upper(coalesce(proposed_use,'')) not like '%FAMILY%' AND "
             "upper(coalesce(proposed_use,'')) not like '%APARTMENT%'")
    params = {"$where": where, "$limit": "20000",
              "$select": "permit_number,permit_creation_date,status,description,estimated_cost,revised_cost,"
                         "existing_use,proposed_use,street_number,street_name,street_suffix"}
    r = session.get(SF_PERMITS, params=params, headers={"User-Agent": user_agent}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def san_francisco_records(rows: list[dict], since: date, include_minor: bool):
    for rec in rows:
        status = (rec.get("status") or "").strip()
        before, after = (rec.get("existing_use") or "").strip(), (rec.get("proposed_use") or "").strip()
        if status in CLOSED_OUT or SF_SKIP_USES.search(f"{before} {after}"):
            continue
        try:
            opened = date.fromisoformat((rec.get("permit_creation_date") or "")[:10])
        except ValueError:
            continue
        if opened < since:
            continue
        desc = " ".join((rec.get("description") or "").split())
        if after and before and after != before:
            signal, base = "Move-in (change of use)", 90
        else:
            hit = classify(f"COM: {desc}")  # uses are already commercial
            if not hit or (hit[0] == "Other commercial" and not include_minor):
                continue
            if MINOR.search(desc) and hit[0] in ("Remodel", "Other commercial") and not include_minor:
                continue
            signal, base = hit
        use = after or before
        addr = " ".join(x for x in (rec.get("street_number"), rec.get("street_name"), rec.get("street_suffix")) if x)
        yield {
            "permit": (rec.get("permit_number") or "").strip(), "opened": opened, "signal": signal, "base": base,
            "business": _clean_tenant(tenant_of(desc)) if QUOTED.search(desc) else "",
            "address": addr.title(), "city": "San Francisco",
            "value": money(rec.get("revised_cost") or rec.get("estimated_cost") or ""), "status": status.title(),
            "description": f"{desc[:1].upper()}{desc[1:]} (use: {before}{' → ' + after if after and after != before else ''})",
            "contractor": "",
        }


def parse_san_francisco(rows: list[dict], today: date | None = None, days: int = 90,
                        include_minor: bool = False) -> list[dict]:
    today = today or date.today()
    return _finish(san_francisco_records(rows, today - timedelta(days=days), include_minor), today)
