# Territory Prospector

A Streamlit app for field sales reps to **discover new business customers** by area and
category, using **live OpenStreetMap data** via the public Overpass API — **no API key, no billing.**

## What it does
- Search any US metro (presets) or type any city (geocoded via OSM Nominatim).
- Pull real businesses in 8 categories (restaurants, fitness, auto, home services,
  medical, retail, professional services, beauty).
- **Lead score (0–100)** from real listing signals a rep can act on:
  - Has a phone → reachable (+35)
  - No website → a digital-presence gap you can help close (+35)
  - Street address → you can plan a visit (+15)
  - Independent (not a chain) → a real local decision-maker (+15)
- Map view (pydeck) + summary metrics (hot leads, presence gaps, avg score).
- **Pipeline**: move prospects through New → Contacted → Qualified → Won / Passed, add
  call notes. Export/import the pipeline as CSV to keep it (session state is per-browser).

## Run locally
```bash
cd sales_prospector
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/streamlit run app.py
```

## Deploy (Streamlit Cloud)
Point a new app at `sales_prospector/app.py` with this folder's `requirements.txt`.
No secrets are required — the data source is public.

## Notes & limits
- Data © OpenStreetMap contributors. Coverage and field completeness vary by area.
- OSM has **no ratings/reviews or employee counts** — that's why scoring uses reachability
  and digital-presence signals instead. If you later want ratings, phone verification, and
  richer profiles, the same UI can be repointed at Google Places (needs a key + billing).
- Overpass is a shared free service; keep "Max results" modest and avoid hammering it.
