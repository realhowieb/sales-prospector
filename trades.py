"""Trades: re-rank the same trigger leads for whatever the rep sells.

Every source (permits, liquor licenses, news, new-on-the-map) finds businesses that are opening,
moving or building out. Who should knock first depends on the trade: a solar rep cares about new
buildings, a POS rep about restaurants and shops, an office-furniture rep about office build-outs.

Each lead is put in a coarse business bucket from its text, then rescored:
    score = base * (0.55 + 0.45 * bucket_fit) + signal_bonus
and given a trade-specific pitch.
"""
from __future__ import annotations

import re

DEFAULT_TRADE = "security"

BUCKETS = {  # first match wins; tested against the lead's combined text
    "medical": re.compile(r"dent(al|ist)|clinic|medical|health|doctor|physician|pharmac|therap|chiro|"
                          r"optom|veterin|urgent care|\bDDS\b|\bMD\b|surgery|hospital", re.I),
    "food": re.compile(r"restaurant|caf[eé]\b|coffee (shop|house|roaster)|cocktail|wine bar|\bpub\b|brew(ery|pub)|"
                       r"winery|tasting room|bakery|commercial kitchen|\bfood\b|pizza|taqueria|sushi|ramen|"
                       r"\bgrill\b|eatery|diner|catering|caterer|nightclub|fast food|ice cream|boba|"
                       r"juice bar|\bdeli\b|new bar|bar opens", re.I),
    "fitness": re.compile(r"gym|fitness|yoga|pilates|martial|crossfit|spa\b|salon|hair|beauty|nail|barber|"
                          r"osteostrong|wellness|massage", re.I),
    "lab": re.compile(r"\blab(s|oratory)?\b|r&d|research|manufactur|industrial|warehouse|testing|clean ?room|"
                      r"\bbio\b|biotech|therapeutics|pharma|semiconductor|data cent|\btools?\b|hydraulic", re.I),
    "hotel": re.compile(r"hotel|motel|\binn\b|lodging|hospitality suites", re.I),
    "retail": re.compile(r"retail|store|shop\b|boutique|market|grocery|liquor|showroom|mall|shopping cent|"
                         r"clothes|apparel|jewel|florist|furniture store|convenience", re.I),
    "office": re.compile(r"office|law|attorney|legal|account|cpa|insurance|financ|capital|venture|partners|"
                         r"consult|software|\bai\b|tech|startup|headquarters|\bhq\b|bank|real estate|"
                         r"architect|agency|\binc\b|corporat|conference|meeting room|workstation|"
                         r"general office|group b\b", re.I),
}

TRADES: dict[str, dict] = {
    "security": {
        "label": "Security & low-voltage", "emoji": "🔐", "profile": "Security systems",
        "sells": "alarms, cameras, access control and monitoring",
        "fit": {"food": 1.0, "retail": 1.0, "hotel": 1.0, "medical": 0.9, "office": 0.85, "lab": 0.9,
                "fitness": 0.8, "other": 0.6},
        "signals": {"Move-in (U&O)": 6, "Build-out (TI)": 6, "New building": 4, "Pending license": 4},
        "pitch": {"food": "CCTV, burglar alarm, after-hours monitoring", "retail": "CCTV, alarm, panic button",
                  "hotel": "Card access, CCTV", "medical": "Access control for exam/records rooms, alarm",
                  "office": "Card access, cameras at entries, alarm", "lab": "Badge access, CCTV, intrusion",
                  "fitness": "Member access control, CCTV", "other": "Alarm and camera walkthrough"},
    },
    "solar": {
        "label": "Solar & energy", "emoji": "☀️", "profile": "Solar & energy",
        "sells": "solar, batteries, EV charging and efficiency upgrades",
        "fit": {"lab": 1.0, "office": 0.8, "retail": 0.7, "hotel": 0.8, "medical": 0.6, "food": 0.45,
                "fitness": 0.5, "other": 0.5},
        "signals": {"New building": 15, "Remodel": 6, "Build-out (TI)": 3, "Moving / HQ": 4},
        "pitch": {"lab": "Solar + storage to offset heavy equipment load", "office": "Rooftop solar, EV chargers for staff",
                  "other": "Energy audit and solar proposal"},
    },
    "hvac": {
        "label": "HVAC & mechanical", "emoji": "❄️", "profile": "HVAC & mechanical",
        "sells": "HVAC install, service contracts and controls",
        "fit": {"lab": 1.0, "medical": 0.9, "food": 0.9, "office": 0.8, "fitness": 0.8, "hotel": 0.8,
                "retail": 0.6, "other": 0.5},
        "signals": {"Build-out (TI)": 10, "New building": 12, "Remodel": 8, "Move-in (U&O)": 2},
        "pitch": {"food": "Kitchen ventilation and a maintenance contract", "lab": "Process cooling and air changes",
                  "medical": "Air quality and a preventive maintenance plan",
                  "other": "Preventive maintenance contract for the new space"},
    },
    "cleaning": {
        "label": "Commercial cleaning", "emoji": "🧽", "profile": "Commercial cleaning",
        "sells": "janitorial, post-construction and recurring cleaning",
        "fit": {"office": 1.0, "medical": 1.0, "fitness": 0.9, "lab": 0.8, "retail": 0.6, "food": 0.6,
                "hotel": 0.5, "other": 0.5},
        "signals": {"Build-out (TI)": 8, "Move-in (U&O)": 10, "New lease": 8, "Moving / HQ": 8},
        "pitch": {"office": "Post-construction clean, then nightly janitorial",
                  "medical": "Medical-grade disinfection schedule", "fitness": "Daily locker room and floor care",
                  "other": "Move-in clean and a recurring quote"},
    },
    "it": {
        "label": "IT, telecom & internet", "emoji": "🌐", "profile": "IT & telecom",
        "sells": "managed IT, internet, phones and structured cabling",
        "fit": {"office": 1.0, "medical": 0.9, "lab": 0.9, "retail": 0.6, "hotel": 0.7, "food": 0.5,
                "fitness": 0.5, "other": 0.5},
        "signals": {"Move-in (U&O)": 10, "Build-out (TI)": 10, "New lease": 10, "Moving / HQ": 10},
        "pitch": {"office": "Cabling, internet circuit and Wi-Fi before move-in",
                  "medical": "HIPAA-ready network and phones", "lab": "Network build-out for the lab",
                  "other": "Internet, Wi-Fi and phones for the new location"},
    },
    "pos": {
        "label": "POS & payments", "emoji": "💳", "profile": "Merchant Services",
        "sells": "point of sale, card processing and online ordering",
        "fit": {"food": 1.0, "retail": 1.0, "fitness": 0.9, "hotel": 0.7, "medical": 0.5, "office": 0.15,
                "lab": 0.05, "other": 0.4},
        "signals": {"Pending license": 10, "Opening": 8, "Move-in (U&O)": 4},
        "pitch": {"food": "POS with online ordering and tableside pay", "retail": "POS, inventory and card rates",
                  "fitness": "Memberships, booking and payments", "other": "Card processing rate review"},
    },
    "insurance": {
        "label": "Business insurance", "emoji": "🛡️", "profile": "Insurance",
        "sells": "commercial property, liability and workers' comp",
        "fit": {"food": 1.0, "retail": 0.9, "medical": 0.9, "fitness": 0.9, "office": 0.8, "lab": 0.8,
                "hotel": 0.9, "other": 0.7},
        "signals": {"Pending license": 8, "Move-in (U&O)": 8, "Opening": 8, "New lease": 6},
        "pitch": {"food": "Liquor liability, property and workers' comp",
                  "office": "Property, GL and cyber for the new office", "other": "Coverage review for the new location"},
    },
    "furniture": {
        "label": "Office furniture & interiors", "emoji": "🪑", "profile": "Office furniture",
        "sells": "furniture, workspace design and installation",
        "fit": {"office": 1.0, "medical": 0.7, "lab": 0.6, "hotel": 0.7, "food": 0.5, "retail": 0.4,
                "fitness": 0.4, "other": 0.4},
        "signals": {"Build-out (TI)": 12, "Move-in (U&O)": 6, "New lease": 10, "Moving / HQ": 10},
        "pitch": {"office": "Workstations and conference rooms for the build-out",
                  "medical": "Waiting room and exam-room furniture", "other": "Furniture plan for the new space"},
    },
}


def get(trade: str | None) -> dict:
    return TRADES.get(trade or DEFAULT_TRADE, TRADES[DEFAULT_TRADE])


def options() -> list[dict]:
    return [{"id": k, "label": v["label"], "emoji": v["emoji"], "sells": v["sells"]} for k, v in TRADES.items()]


def _text(source: str, row: dict) -> str:
    keys = {
        "permits": ("Business", "Description"),
        "licenses": ("Type", "Business"),
        "news": ("Headline",),
        "map": ("Kind", "Business"),
    }.get(source, ())
    return " ".join(str(row.get(k) or "") for k in keys)


def bucket_of(source: str, row: dict) -> str:
    if source == "licenses":  # ABC types are food, retail or hotel by definition
        t = str(row.get("Type") or "")
        return "hotel" if "Hotel" in t else "retail" if re.search(r"store|market|wholesal|import", t, re.I) else "food"
    text = _text(source, row)
    hits = {name: len(pat.findall(text)) for name, pat in BUCKETS.items()}
    best = max(hits, key=lambda n: hits[n])  # ties keep BUCKETS order
    if hits[best]:
        return best
    # A commercial build-out or move-in with no other clue is almost always office space.
    if source == "permits" and str(row.get("Signal", "")).startswith(("Move", "Build")):
        return "office"
    return "other"


def _signal(source: str, row: dict) -> str:
    if source == "licenses":
        return "Pending license" if str(row.get("Trigger", "")).startswith("Pending") else "New license"
    return str(row.get("Signal") or "")


def rescore(source: str, rows: list[dict], trade: str | None) -> list[dict]:
    """Copies of rows re-ranked for the trade, each with Fit and Pitch; best first."""
    t = get(trade)
    out = []
    for row in rows:
        bucket = bucket_of(source, row)
        fit = t["fit"].get(bucket, t["fit"]["other"])
        base = float(row.get("Score") or 0)
        score = round(base * (0.55 + 0.45 * fit) + t["signals"].get(_signal(source, row), 0))
        out.append({**row, "Score": max(1, min(100, score)), "Fit": bucket,
                    "Pitch": t["pitch"].get(bucket, t["pitch"].get("other", ""))})
    date_key = {"permits": "Opened", "news": "Date", "map": "Added"}.get(source, "")
    return sorted(out, key=lambda r: (r["Score"], str(r.get(date_key, ""))), reverse=True)
