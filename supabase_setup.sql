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
-- If you created `reps` before rep management existed, add the new columns:
alter table reps add column if not exists edit_code_hash text;
alter table reps add column if not exists active boolean default true;
alter table reps add column if not exists is_sample boolean default false;

alter table reps enable row level security;

create policy "public read reps"   on reps for select using (true);
create policy "public insert reps" on reps for insert with check (true);
-- Edit / pause / delete are performed by the app with the service_role key
-- (which bypasses RLS) after verifying the rep's edit code — so no public
-- update/delete policy is granted here.

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


-- ------------------------------------------------------------------------- --
-- PIPELINE: private saved prospect stages, notes, and follow-up dates.
-- The app reads/writes this table only with the Supabase service_role key after
-- the rep enters their email in the sidebar. No public RLS policies are granted.
-- ------------------------------------------------------------------------- --
create table if not exists pipeline_entries (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  owner_email    text not null,
  prospect_id    text not null,
  name           text,
  category       text,
  stage          text,
  note           text,
  next_follow_up date,
  outcome        text,
  unique(owner_email, prospect_id)
);

alter table pipeline_entries enable row level security;

create or replace function set_pipeline_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists pipeline_entries_updated_at on pipeline_entries;
create trigger pipeline_entries_updated_at
before update on pipeline_entries
for each row execute function set_pipeline_updated_at();


-- ------------------------------------------------------------------------- --
-- REVIEWS: customers rate reps. Reviews are meant to be public, so both read
-- and insert are allowed for the anon key. The app shows a rep's real average
-- (replacing any seeded rating) once reviews exist.
-- ------------------------------------------------------------------------- --
create table if not exists reviews (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  rep_id        text not null,
  rating        int not null check (rating between 1 and 5),
  customer_name text,
  comment       text
);

alter table reviews enable row level security;

create policy "public read reviews"   on reviews for select using (true);
create policy "public insert reviews" on reviews for insert with check (true);
