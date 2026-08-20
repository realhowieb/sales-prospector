# Territory Prospector

A two-sided Streamlit app:

- **Sales reps** → **discover new business customers** by area and category, using **live
  OpenStreetMap data** via the public Overpass API (no API key, no billing).
- **Customers/businesses** → **find a rep and the best deals**. An open marketplace where
  reps list their offer, and customers are matched to reps ranked by a **best-match score**
  (deal strength + rating + response time).

Switch between the two with the **"I am a…"** toggle at the top of the sidebar.

## Customer marketplace — best-match score
`score = deal strength (40) + rating (35) + response speed (25)`, 0–100. Customers filter by
what they need + where they are, and sort by Best match / Best deal / Top rated / Fastest
response. Reps can **list themselves** via the in-app form.

### Making it a real, shared open marketplace (Supabase)
The marketplace works in two modes automatically:

- **Demo mode** (no secret): a seeded roster + this-browser listings. Good for local dev / a demo.
- **🟢 Live mode** (Supabase secret present): reps self-register into a shared Postgres table and
  **every visitor sees the same listings** — a true open marketplace.

The app reads/writes Supabase over its REST API using `requests` (no extra dependency). To turn
it on:

1. Create a free project at [supabase.com](https://supabase.com).
2. **SQL Editor → run [`supabase_setup.sql`](supabase_setup.sql)** — creates the marketplace,
   leads, reviews, and pipeline tables with public read policies only where needed.
3. **Project Settings → API** → copy the **Project URL**, the **anon public key**, and the
   **service_role** key. The service key stays server-side in Streamlit secrets and is required
   for listing submissions, lead capture, verified reviews, listing management, and pipeline sync.
4. Add them as secrets:
   - **Streamlit Cloud:** app → Settings → Secrets → paste the `[supabase]` block.
   - **Local:** copy [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example) to
     `.streamlit/secrets.toml` and fill in the values (this file is gitignored).
   ```toml
   [supabase]
   url = "https://YOUR-PROJECT-REF.supabase.co"
   key = "YOUR-ANON-PUBLIC-KEY"
   service_key = "YOUR-SERVICE-ROLE-KEY"

   [app]
   base_url = "https://YOUR-STREAMLIT-APP-URL"
   ```
5. Reload. The Customer tab now shows **🟢 Live marketplace**. Empty at first — use **Load 16
   sample reps** once to populate, or let real reps register via **List yourself as a rep**.

**Moderation/security:** public visitors can read public marketplace data, but direct anon-key
inserts are blocked. The app performs public submissions server-side with the `service_role` key
after validation and session rate limits.

### Delivering the leads (customer → rep)
When a customer hits **Request an intro** on a rep card, they submit their name + email/phone +
a short message. That lead is:

1. **Saved to Supabase** (`leads` table — created by the same `supabase_setup.sql`) by the
   server-side app. The table has **no public read or write policy**, so customer contact details
   are never exposed through the public API.
2. **Emailed to the rep** via **Resend over SMTP** (reusing the `hsfinest.ai` verified domain),
   with `Reply-To` set to the customer so the rep can reply directly. Configure under `[smtp]` in
   secrets — only the Resend API key (`password`) and a `from` address are required.

**Rep leads inbox:** the marketplace has a **"Check my leads"** box where a rep enters their email
to see their leads. Because leads aren't publicly readable, this requires the Supabase
**service_role** key (`[supabase].service_key`) — a server-side secret that never reaches the
browser. Without it, leads are still saved and emailed; only the in-app inbox is hidden.

Each piece degrades gracefully: no email secret → leads are still saved; no service key → live
submissions are read-only; no Supabase → the request is logged for the session (demo).

### Ratings, reviews, and rep listing management
- **Verified reviews:** customers receive a one-time review token after requesting an intro.
  Live reviews require that token, are marked `verified`, and only verified reviews affect the
  displayed rating and best-match score.
- **Trust & safety at sign-up:** listings are checked for a valid email, blocked words, links in
  the name/company, length limits, and duplicates (same company + email); sign-ups are rate-limited
  per session.
- **Rep listing management:** each new listing gets an **edit code** (shown once + emailed; only a
  SHA-256 hash is stored). Under **Manage your listing**, a rep enters their email + code to
  **edit, pause, or delete** their listing. Pausing hides it from customers. These writes use the
  Supabase **service_role** key (`[supabase].service_key`), so the management panel only appears
  when that key is set.

Re-run `supabase_setup.sql` after updating — it adds the `reviews` table and the `edit_code_hash` /
`active`, sample, service-area, verified-review, and pipeline columns (idempotent where possible).

## Rep side — what it does
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
  call notes and next follow-up dates. With Supabase `service_key`, reps can sync a private
  pipeline using their email + pipeline code; CSV export/import still works.

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
