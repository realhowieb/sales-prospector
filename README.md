# Territory Prospector

A multi-sided sales marketplace and prospecting platform, built in Streamlit.

Three audiences share one workspace, chosen from the sidebar:

- **🧭 Sales rep — find customers:** discover new business customers by area + category from
  **live OpenStreetMap data** (Overpass API, no key/billing), scored for how winnable they are,
  with a private pipeline.
- **🏢 Company — find reps / 🙋 Rep — find companies:** an open marketplace where reps list
  their coverage & deals and companies post product lines/opportunities, matched to each other.
- **🛍️ Customer — find a rep & deals:** browse and compare reps by a best-match score, request
  intros, and leave reviews.

Plus **🗺 Territory Intelligence** (per-metro activity and matches) and **⭐ Saved** reps/opportunities.

## Architecture
`app.py` is the Streamlit UI; core logic lives in focused modules:

| Module | Responsibility |
|---|---|
| `prospecting_core` | HTML/text utils, lead scoring & heat, geo distance/area matching, pipeline payloads |
| `rep_match_score` | Rep↔opportunity match scoring, product-line conflict detection, confidence labels |
| `review_system` | Review payloads, status, rating clamp/dedup, summaries |
| `monetization` | Plans & entitlements gating (contact, full profile, advanced search, territory intel, featured) |
| `auth_system` | Account roles, sessions, permission checks |
| `connection_requests` | Company↔rep connection requests + contact-visibility rules |
| `profile_claims` | Reps claiming an existing listing |
| `shortlists` | Saved reps/companies/opportunities |
| `territory_intelligence` | Matching reps/opportunities/companies + metro activity |

## Data & modes
- **Prospecting (rep side)** always uses live OpenStreetMap/Overpass + Nominatim geocoding — no key.
- **Marketplace (everything else)** runs in two modes automatically:
  - **🟡 Demo mode** (no secret): a seeded roster + session state. Good for local dev / demos.
  - **🟢 Live mode** (Supabase secrets set): reps/companies/leads/reviews/pipeline persist in a
    shared Postgres DB, reached over the Supabase REST API using `requests` (no extra dependency).

**Security model:** public visitors read only what's meant to be public (approved listings,
verified reviews). All writes — sign-ups, leads, reviews, pipeline, listing management — go
**server-side with the Supabase `service_role` key** after validation and rate-limits. Customer
lead details are private (service-role only) and reps are notified by email.

## Enable live mode (config only)
1. Create a free project at [supabase.com](https://supabase.com).
2. **SQL Editor → run [`supabase_setup.sql`](supabase_setup.sql)** — creates/updates the `reps`,
   `leads`, `reviews`, `pipeline_entries` (and related) tables and RLS policies. It's idempotent.
3. **Project Settings → API** → copy the **Project URL**, the **anon** key, and the **service_role**
   key.
4. Add secrets (Streamlit Cloud → Settings → Secrets, or local `.streamlit/secrets.toml` — see
   [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example)):
   ```toml
   [supabase]
   url = "https://YOUR-PROJECT-REF.supabase.co"
   key = "YOUR-ANON-PUBLIC-KEY"
   service_key = "YOUR-SERVICE-ROLE-KEY"   # required for all live writes
   [app]
   base_url = "https://YOUR-STREAMLIT-APP-URL"   # builds one-time verified-review links
   [smtp]                                         # optional: lead/edit-code emails via Resend
   password = "re_YOUR_RESEND_API_KEY"
   from = "leads@hsfinest.ai"
   ```
5. Reload. Empty at first — use **Load sample reps** once, or let real reps/companies register.

Each layer degrades gracefully: no `[smtp]` → data still saves, no email; no `service_key` →
live writes are read-only; no `[supabase]` → session/demo mode. The app never white-screens on
missing config — it does as much as it's configured to do.

## Notable features
- **Lead scoring** (rep side) adapts to **what you sell** ("What do you sell?" → the weighting of
  no-website / storefront / operational signals changes per product line).
- **Best-match** ranking on the marketplace = deal strength + rating + response speed, with
  ZIP/city + **search-radius** matching and a match-confidence explanation.
- **Verified reviews** (one-time token issued on intro request) drive the real rating.
- **Rep listing management** via an emailed **edit code** (SHA-256 hashed): edit / pause / delete.
- **Trust & safety:** email/blocked-word/link/length/dedup checks + per-session rate limits.
- **Monetization:** plans & entitlements gate contact, full profiles, advanced search, and
  territory intelligence.
- **Mobile:** long result lists are paginated ("Show more") and the map is collapsed by default,
  so scrolling stays smooth on phones.

## Run locally
```bash
cd sales_prospector
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/streamlit run app.py
```

## Deploy (Streamlit Cloud)
Point a new app at `sales_prospector/app.py` with this folder's `requirements.txt`. Runs in demo
mode with no secrets; add the `[supabase]` block above to go live.

## Notes & limits
- Business data © OpenStreetMap contributors; coverage/detail vary by area, and OSM has no
  ratings/reviews. Overpass is a shared free service — keep "Max results" modest.
- Marketplace reps/companies are proprietary data (no public directory exists), so demo mode
  ships with fabricated sample listings clearly badged as samples.
