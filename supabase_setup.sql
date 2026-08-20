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
