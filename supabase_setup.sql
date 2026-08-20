-- Territory Prospector — open rep marketplace table.
-- Run this once in your Supabase project: SQL Editor → paste → Run.

create table if not exists reps (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  name          text not null,
  company       text not null,
  categories    text[] not null default '{}',
  metros        text[] not null default '{}',
  deal          text,
  deal_strength double precision default 0.5,
  rating        double precision default 0,
  reviews       integer default 0,
  response      text,
  verified      boolean default false,
  blurb         text,
  email         text,
  phone         text
);

-- Row Level Security: allow the public (anon key) to READ and INSERT listings,
-- but NOT update or delete. That's exactly what an open marketplace needs, and
-- it means the anon key is safe to ship in the app's secrets.
alter table reps enable row level security;

create policy "public read reps"   on reps for select using (true);
create policy "public insert reps" on reps for insert with check (true);

-- Note: no update/delete policies are created, so those are denied by default.
-- Moderation (e.g. flipping `verified` to true, removing spam) is done by you
-- from the Supabase Table Editor, or with the service_role key from a backend.


-- ------------------------------------------------------------------------- --
-- LEADS: customers' intro requests to reps.
-- Public (anon) can INSERT a lead but CANNOT read leads — so customer contact
-- details are never exposed through the public API. The app reads a rep's leads
-- only with the service_role key (server-side secret), and reps are also
-- notified by email. Run this block too.
-- ------------------------------------------------------------------------- --
create table if not exists leads (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  rep_id         text,
  rep_company    text,
  rep_email      text,
  customer_name  text,
  customer_email text,
  customer_phone text,
  message        text,
  category       text,
  metro          text,
  notified       boolean default false
);

alter table leads enable row level security;

-- Insert only for the public key; NO select policy => leads are not publicly
-- readable. Reading requires the service_role key (which bypasses RLS).
create policy "public insert leads" on leads for insert with check (true);
