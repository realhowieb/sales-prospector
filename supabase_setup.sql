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

-- Row Level Security: allow the public (anon key) to READ listings, but do not
-- allow direct public INSERT/UPDATE/DELETE. Public submissions go through the
-- Streamlit server with the service_role key after validation/rate limits.
-- If you created `reps` before rep management existed, add the new columns:
alter table reps add column if not exists edit_code_hash text;
alter table reps add column if not exists active boolean default true;
alter table reps add column if not exists is_sample boolean default false;
alter table reps add column if not exists service_area text;
alter table reps add column if not exists service_lat double precision;
alter table reps add column if not exists service_lon double precision;
alter table reps add column if not exists service_radius_miles integer default 25;

alter table reps enable row level security;

drop policy if exists "public read reps" on reps;
drop policy if exists "public insert reps" on reps;
create policy "public read reps"   on reps for select using (true);
-- Edit / pause / delete are performed by the app with the service_role key
-- (which bypasses RLS) after verifying the rep's edit code — so no public
-- insert/update/delete policy is granted here.

-- Note: no update/delete policies are created, so those are denied by default.
-- Moderation (e.g. flipping `verified` to true, removing spam) is done by you
-- from the Supabase Table Editor, or with the service_role key from a backend.


-- ------------------------------------------------------------------------- --
-- LEADS: customers' intro requests to reps.
-- Leads are private. The app writes/reads them only with the service_role key
-- (server-side secret), and reps are also notified by email. Run this block too.
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
  notified       boolean default false,
  review_token_hash text,
  review_token_used_at timestamptz
);

alter table leads add column if not exists review_token_hash text;
alter table leads add column if not exists review_token_used_at timestamptz;

alter table leads enable row level security;

-- No public policies: leads are private and written/read only by service_role.
drop policy if exists "public insert leads" on leads;


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
  owner_key_hash text,
  prospect_id    text not null,
  name           text,
  category       text,
  stage          text,
  note           text,
  next_follow_up date,
  outcome        text,
  unique(owner_email, owner_key_hash, prospect_id)
);

alter table pipeline_entries add column if not exists owner_key_hash text;
alter table pipeline_entries drop constraint if exists pipeline_entries_owner_email_prospect_id_key;
create unique index if not exists pipeline_entries_owner_key_unique
  on pipeline_entries(owner_email, owner_key_hash, prospect_id);

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
-- REVIEWS: public can read verified reviews, but cannot insert directly. The
-- app writes reviews with service_role only after validating a one-time token
-- from an intro request.
-- ------------------------------------------------------------------------- --
create table if not exists reviews (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  rep_id        text not null,
  lead_id       bigint,
  rating        int not null check (rating between 1 and 5),
  customer_name text,
  comment       text,
  verified      boolean default false
);

alter table reviews add column if not exists lead_id bigint;
alter table reviews add column if not exists verified boolean default false;

alter table reviews enable row level security;

drop policy if exists "public read reviews" on reviews;
drop policy if exists "public insert reviews" on reviews;
create policy "public read reviews"   on reviews for select using (verified is true);
