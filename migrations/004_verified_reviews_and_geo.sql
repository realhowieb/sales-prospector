-- Verified reviews, service-area radius matching, and private public-write posture.

alter table reps add column if not exists service_area text;
alter table reps add column if not exists service_lat double precision;
alter table reps add column if not exists service_lon double precision;
alter table reps add column if not exists service_radius_miles integer default 25;

alter table leads add column if not exists review_token_hash text;
alter table leads add column if not exists review_token_used_at timestamptz;
alter table leads enable row level security;
drop policy if exists "public insert leads" on leads;

alter table reviews add column if not exists lead_id bigint;
alter table reviews add column if not exists verified boolean default false;
alter table reviews enable row level security;
drop policy if exists "public read reviews" on reviews;
drop policy if exists "public insert reviews" on reviews;
create policy "public read reviews" on reviews for select using (verified is true);

