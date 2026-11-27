"""Trigger leads: businesses that are opening, changing owners, or just opened.

Source: the California ABC (liquor board) daily license file — public, no key. A pending
application means a restaurant, bar, store or hotel is building out or changing hands, which is
when owners buy alarms, cameras, access control and other services.

Contact enrichment (optional): match leads to OpenStreetMap listings, then read each business's
own website for an email / phone. Pure functions here; the Streamlit UI lives in app.py.
"""
from __future__ import annotations

import csv
import io
import re
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from difflib import SequenceMatcher

ABC_EXPORT_URL = "https://www.abc.ca.gov/wp-content/uploads/DailyExport-CSV.zip"
ABC_RECORD_URL = "https://www.abc.ca.gov/licensing/license-lookup/single-license/?RPTTYPE=12&LICENSE={}"

# ABC license type -> (what it is, fit points 0-40, what to pitch)
LICENSE_TYPES = {
    "47": ("Full restaurant w/ bar", 40, "CCTV, burglar alarm, after-hours monitoring"),
    "41": ("Restaurant (beer & wine)", 35, "CCTV, burglar alarm"),
    "48": ("Bar / nightclub", 40, "CCTV inside + entrance, alarm, panic"),
    "21": ("Liquor store / market", 40, "CCTV, alarm, panic"),
    "20": ("Market / convenience (beer & wine)", 35, "CCTV, alarm"),
    "23": ("Brewery / brewpub", 35, "CCTV, alarm, access"),
    "70": ("Hotel", 40, "Card access, CCTV"),
    "02": ("Winery", 30, "Alarm, CCTV"),
    "42": ("Wine bar / tasting", 35, "CCTV, alarm"),
    "40": ("Beer bar", 35, "CCTV, alarm"),
    "57": ("Special on-sale (club/venue)", 30, "CCTV, access"),
    "51": ("Private club", 30, "Card access, CCTV"),
    "52": ("Veterans club", 25, "Alarm, CCTV"),
    "58": ("Caterer", 20, "Alarm"),
    "86": ("Tasting room", 30, "Alarm, CCTV"),
    "17": ("Beer & wine wholesaler", 30, "Alarm, CCTV, access"),
    "09": ("Beer & wine importer", 25, "Alarm"),
}
SKIP_TYPES = {"77", "66", "68", "03", "12", "14", "22", "28", "79", "80", "84", "85"}  # event permits, brokers…

CHAINS = re.compile(
    r"safeway|hilton|marriott|residence inn|applebee|p ?f chang|grocery outlet|cardenas|whole foods|"
    r"trader joe|\bcvs\b|walgreens|7-eleven|seven eleven|\btarget\b|costco|chevron|\bshell\b|\barco\b|valero|"
    r"rite aid|bevmo|total wine|sprouts|lucky|nob hill|starbucks|chipotle|olive garden|cheesecake|hyatt|"
    r"sheraton|westin|homewood|hampton|courtyard|holiday inn|cinemark|\bamc\b|in-n-out|shake shack|mcdonald",
    re.I,
)
ENTITY = re.compile(
    r"\b(llc|inc|incorporated|corp|corporation|co|company|lp|llp|l-pship|ltd|group|holdings|partners|"
    r"trust|enterprises?|ventures?|restaurants?|hospitality|foods?|market|hotel|management|the)\b|&",
    re.I,
)

PRESET_TERRITORIES = {
    "Peninsula (Belmont → Mountain View)": ["Belmont", "San Carlos", "Redwood City", "Menlo Park", "Atherton",
                                            "East Palo Alto", "Palo Alto", "Stanford", "Mountain View",
                                            "Los Altos", "Los Altos Hills", "Portola Valley", "Woodside"],
    "San Francisco": ["San Francisco"],
    "San Jose / South Bay": ["San Jose", "Santa Clara", "Sunnyvale", "Cupertino", "Campbell", "Milpitas",
                             "Los Gatos", "Saratoga"],
    "East Bay": ["Oakland", "Berkeley", "Emeryville", "Alameda", "San Leandro", "Hayward", "Fremont",
                 "Walnut Creek", "Concord", "Pleasanton", "Dublin", "Livermore", "San Ramon"],
    "Los Angeles": ["Los Angeles", "Santa Monica", "Beverly Hills", "West Hollywood", "Culver City",
                    "Pasadena", "Burbank", "Glendale", "Long Beach"],
    "San Diego": ["San Diego", "La Jolla", "Chula Vista", "Oceanside", "Carlsbad", "Escondido", "El Cajon"],
    "Sacramento": ["Sacramento", "Elk Grove", "Roseville", "Folsom", "Rancho Cordova", "Citrus Heights"],
}


# --------------------------------------------------------------------------- #
# ABC file
# --------------------------------------------------------------------------- #
def download_abc_csv(session, user_agent: str, timeout: int = 180) -> str:
    """Download the ABC daily export (zip) and return the CSV text."""
    r = session.get(ABC_EXPORT_URL, headers={"User-Agent": user_agent}, timeout=timeout)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        return zf.read(name).decode("utf-8-sig", "ignore")


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    for fmt in ("%d-%b-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def person_from_owner(owner: str) -> str:
    """ABC lists individuals as 'Last  First Middle'. Return 'First Middle Last' or ''."""
    owner = (owner or "").strip()
    parts = [p.strip() for p in owner.split("  ") if p.strip()]
    if not owner or ENTITY.search(owner) or len(parts) != 2:
        return ""
    return f"{parts[1]} {parts[0]}".title()


def abc_record_url(file_number: str) -> str:
    return ABC_RECORD_URL.format((file_number or "").strip().lstrip("0"))


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def parse_abc(csv_text: str, cities: list[str], today: date | None = None, issued_days: int = 180) -> list[dict]:
    """Leads in the given cities: pending applications + licenses first issued in the last `issued_days`."""
    today = today or date.today()
    want = {c.strip().upper() for c in cities if c.strip()}
    lines = csv_text.splitlines()
    if lines and lines[0].lstrip('"').startswith("Updated"):
        lines = lines[1:]
    reader = csv.DictReader(lines)
    reader.fieldnames = [f.strip() for f in (reader.fieldnames or [])]
    leads: dict[str, dict] = {}
    for r in reader:
        r = {k: (v or "") for k, v in r.items() if k}
        city = r.get("Prem City", "").strip().upper()
        if city not in want:
            continue
        lt = r.get("License Type", "").strip()
        if lt in SKIP_TYPES:
            continue
        status, kind = r.get("Type Status", "").strip(), r.get("Lic or App", "").strip()
        issued = parse_date(r.get("Type Orig Iss Date", ""))
        if kind == "APP":
            trigger = "Pending license — opening soon or new owner"
        elif kind == "LIC" and status == "ACTIVE" and issued and 0 <= (today - issued).days <= issued_days:
            trigger = f"Licensed {issued:%b %d, %Y} — just opened / new owner"
        else:
            continue
        what, pts, pitch = LICENSE_TYPES.get(lt, (f"ABC type {lt}", 20, "Alarm, CCTV"))
        key = r.get("File Number", "").strip()
        if key in leads:  # one premises can hold several license types; keep the best fit
            if pts > leads[key]["_fit"]:
                leads[key].update({"Type": what, "Pitch": pitch, "_fit": pts})
            continue
        owner = _clean(r.get("Primary Name", ""))
        dba = _clean(r.get("DBA Name", "")) or owner
        entity = bool(re.search(r"\b(llc|inc|corp|lp|company)\b", owner, re.I))
        addr = " ".join(_clean(r.get(k, "")) for k in ("Prem Addr 1", "Prem Addr 2") if _clean(r.get(k, "")))
        leads[key] = {
            "Business": dba.title(),
            "Owner": owner.title(),
            "Person to ask for": person_from_owner(r.get("Primary Name", "")),  # needs the raw double spaces
            "City": city.title(),
            "Address": addr.title(),
            "ZIP": r.get("Prem Zip", "").strip()[:5],
            "Type": what,
            "Trigger": trigger,
            "Pitch": pitch,
            "Licensed": issued,
            "ABC file #": key,
            "ABC record": abc_record_url(key),
            "Owner mailing address": ", ".join(
                _clean(r.get(k, "")) for k in ("Mail Addr 1", "Mail City") if _clean(r.get(k, ""))).title(),
            "_fit": pts,
            "_pending": kind == "APP",
            "_in_review": status == "PEND",
            "_entity": entity,
        }
    out = []
    for lead in leads.values():
        score = lead["_fit"] + (35 if lead["_pending"] else 25) + (15 if lead["_entity"] else 5) \
            + (10 if lead["_in_review"] else 0)
        chain = bool(CHAINS.search(f"{lead['Business']} {lead['Owner']}"))
        if chain:
            score -= 50
            lead["Type"] += " (chain — national account)"
        lead["Score"] = max(0, min(100, score))
        lead["Chain"] = chain
        for k in ("_fit", "_pending", "_in_review", "_entity"):
            lead.pop(k)
        out.append(lead)
    return sorted(out, key=lambda x: x["Score"], reverse=True)


# --------------------------------------------------------------------------- #
# Contact enrichment
# --------------------------------------------------------------------------- #
STOP = {"llc", "inc", "corp", "co", "the", "and", "restaurant", "cafe", "bar", "kitchen", "dba", "lp", "ltd", "of"}
STREET_ABBR = {"street": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd", "road": "rd", "drive": "dr",
               "lane": "ln", "court": "ct", "place": "pl", "west": "w", "east": "e", "north": "n", "south": "s",
               "first": "1st", "second": "2nd", "third": "3rd"}
STREET_TYPES = {"st", "ave", "blvd", "rd", "dr", "ln", "ct", "pl", "way"}


def norm_name(s: str) -> str:
    return " ".join(w for w in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split() if w not in STOP)


def addr_key(s: str) -> tuple[str, str] | None:
    words = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()
    if not words or not re.match(r"^\d+", words[0]):
        return None
    num = re.match(r"^\d+", words[0]).group()
    rest = [STREET_ABBR.get(w, w) for w in words[1:] if w not in ("ste", "suite", "unit", "apt")]
    rest = [w for w in rest if w not in STREET_TYPES] or rest
    return num, (rest[0] if rest else "")


def osm_contact_query(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    box = f"[bbox:{s},{w},{n},{e}]"
    return (f"[out:json][timeout:90]{box};\n"
            'nwr["name"]["shop"]; out center tags;\n'
            'nwr["name"]["amenity"~"^(restaurant|cafe|bar|pub|fast_food|bank|pharmacy|cinema|ice_cream|nightclub)$"];'
            " out center tags;\n"
            'nwr["name"]["tourism"="hotel"]; out center tags;')


CONTACT_MIRRORS = [
    ("https://overpass-api.de/api/interpreter", 45),
    ("https://overpass.private.coffee/api/interpreter", 40),
    ("https://maps.mail.ru/osm/tools/overpass/api/interpreter", 30),
]


def fetch_listings(session, query: str, user_agent: str) -> list[dict]:
    """Run the contact query on public Overpass mirrors with short per-mirror waits."""
    errors = []
    for url, wait in CONTACT_MIRRORS:
        try:
            r = session.post(url, data={"data": query}, headers={"User-Agent": user_agent}, timeout=wait)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json().get("elements", [])
            errors.append(f"{url.split('/')[2]}: HTTP {r.status_code}")
        except Exception as exc:
            errors.append(f"{url.split('/')[2]}: {type(exc).__name__}")
    raise RuntimeError("the free map-data servers are busy (" + "; ".join(errors) + "). Try again in a few minutes.")


def index_osm(elements: list[dict]) -> tuple[dict, dict]:
    by_addr: dict = {}
    by_city: dict = {}
    for el in elements:
        t = el.get("tags", {})
        email = t.get("email") or t.get("contact:email") or ""
        rec = {
            "name": t.get("name", ""),
            "phone": t.get("phone") or t.get("contact:phone") or "",
            "email": "" if "notify" in email else email,
            "website": t.get("website") or t.get("contact:website") or t.get("url") or "",
        }
        hn, st = t.get("addr:housenumber"), t.get("addr:street")
        k = addr_key(f"{hn} {st}") if hn and st else None
        if k:
            by_addr.setdefault(k, []).append(rec)
        by_city.setdefault((t.get("addr:city") or "").lower(), []).append(rec)
    return by_addr, by_city


def match_listing(name: str, address: str, city: str, by_addr: dict, by_city: dict) -> dict | None:
    """Same street address + similar name, or a near-identical name in the same city."""
    n = norm_name(name)
    best, score = None, 0.0
    k = addr_key(address)
    for rec in by_addr.get(k, []) if k else []:
        s = SequenceMatcher(None, n, norm_name(rec["name"])).ratio() + 0.4
        if s > score:
            best, score = rec, s
    if score < 0.95:
        for rec in by_city.get(city.lower(), []):
            s = SequenceMatcher(None, n, norm_name(rec["name"])).ratio()
            if s > score and s >= 0.95:
                best, score = rec, s
    return best if best and score >= 0.95 else None


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\(?\b(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})\b")
BAD_EMAIL = ("example.", "sentry", "wixpress", "@2x", ".png", ".jpg", ".webp", "domain.com", "email.com",
             "godaddy", "squarespace", "yourname", "fonts", "notify", "noreply", "no-reply", "wordpress", "shopify")
MAILBOX_PROVIDERS = ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com")


def extract_contacts(site: str, pages: list[str]) -> tuple[str, str]:
    """Pick the business's email and phone out of its web pages."""
    text = " ".join(pages)
    found = re.findall(r"mailto:([^\"'?>\s]+)", text) + EMAIL_RE.findall(text)
    dom = urllib.parse.urlparse(site if site.startswith("http") else "https://" + site).netloc.lower()
    dom = dom.removeprefix("www.").split(":")[0]
    emails = []
    for e in found:
        e = e.strip().strip("\\/.,;")
        if not EMAIL_RE.fullmatch(e) or any(b in e.lower() for b in BAD_EMAIL):
            continue
        edom = e.lower().split("@")[1]
        if edom in MAILBOX_PROVIDERS or (dom and edom.endswith(dom)):
            emails.append(e)
    phones = re.findall(r"tel:([+\d\-().\s]{10,})", text)
    if not phones:
        plain = re.sub(r"<[^>]+>", " ", text)
        phones = ["(%s) %s-%s" % p for p in PHONE_RE.findall(plain)]
    return (emails[0] if emails else ""), (phones[0].strip() if phones else "")


def scrape_site(session, site: str, user_agent: str) -> tuple[str, str]:
    if not site:
        return "", ""
    if not site.startswith("http"):
        site = "https://" + site

    def get(url):
        try:
            r = session.get(url, headers={"User-Agent": user_agent}, timeout=10)
            return r.text[:400_000] if r.ok else ""
        except Exception:
            return ""

    home = get(site)
    pages = [home]
    for m in re.finditer(r'href="([^"]*(contact|about|location|visit)[^"]*)"', home, re.I):
        pages.append(get(urllib.parse.urljoin(site, m.group(1))))
        if len(pages) >= 3:
            break
    return extract_contacts(site, pages)


def enrich_contacts(leads: list[dict], osm_elements: list[dict], session=None, user_agent: str = "",
                    scrape_limit: int = 60) -> list[dict]:
    """Add Phone / Email / Website. Website scraping is capped to keep the page responsive."""
    by_addr, by_city = index_osm(osm_elements)
    for lead in leads:
        m = match_listing(lead["Business"], lead["Address"], lead["City"], by_addr, by_city)
        lead["Phone"] = m["phone"] if m else ""
        lead["Email"] = m["email"] if m else ""
        lead["Website"] = m["website"] if m else ""
    if session is None:
        return leads
    todo = [x for x in leads if x["Website"] and (not x["Email"] or not x["Phone"])][:scrape_limit]

    def work(lead):
        email, phone = scrape_site(session, lead["Website"], user_agent)
        lead["Email"] = lead["Email"] or email
        lead["Phone"] = lead["Phone"] or phone

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(work, todo))
    return leads


def union_bbox(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float] | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
