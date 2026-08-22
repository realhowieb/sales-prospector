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
alter table reps add column if not exists profile_slug text;
alter table reps add column if not exists headline text;
alter table reps add column if not exists years_experience integer default 0;
alter table reps add column if not exists industries text[] not null default '{}';
alter table reps add column if not exists customer_types text[] not null default '{}';
alter table reps add column if not exists states text[] not null default '{}';
alter table reps add column if not exists zip_codes text[] not null default '{}';
alter table reps add column if not exists territory_radius integer default 25;
alter table reps add column if not exists open_to_new_lines boolean default true;
alter table reps add column if not exists commission_min numeric(10,2);
alter table reps add column if not exists commission_max numeric(10,2);
alter table reps add column if not exists compensation_types text[] not null default '{}';
alter table reps add column if not exists existing_lines text[] not null default '{}';
alter table reps add column if not exists competing_lines text[] not null default '{}';
alter table reps add column if not exists website text;
alter table reps add column if not exists linkedin_url text;
alter table reps add column if not exists profile_status text default 'active';
alter table reps add column if not exists claimed boolean default false;
alter table reps add column if not exists claim_email text;
alter table reps add column if not exists last_active_at timestamptz default now();
alter table reps add column if not exists response_rate double precision default 0;
alter table reps add column if not exists response_time_hours double precision;
alter table reps add column if not exists featured boolean default false;
alter table reps add column if not exists source text default 'self_signup';
alter table reps add column if not exists updated_at timestamptz default now();
alter table reps add column if not exists availability_status text;
alter table reps add column if not exists preferred_categories text[] not null default '{}';
alter table reps add column if not exists preferred_company_types text[] not null default '{}';
alter table reps add column if not exists preferred_compensation text[] not null default '{}';
alter table reps add column if not exists minimum_commission numeric(10,2);
alter table reps add column if not exists notes_for_companies text;

alter table reps alter column years_experience set default 0;
alter table reps alter column industries set default '{}';
alter table reps alter column customer_types set default '{}';
alter table reps alter column states set default '{}';
alter table reps alter column zip_codes set default '{}';
alter table reps alter column territory_radius set default 25;
alter table reps alter column open_to_new_lines set default true;
alter table reps alter column compensation_types set default '{}';
alter table reps alter column existing_lines set default '{}';
alter table reps alter column competing_lines set default '{}';
alter table reps alter column profile_status set default 'active';
alter table reps alter column claimed set default false;
alter table reps alter column last_active_at set default now();
alter table reps alter column response_rate set default 0;
alter table reps alter column featured set default false;
alter table reps alter column source set default 'self_signup';
alter table reps alter column updated_at set default now();
alter table reps alter column availability_status set default 'open';
alter table reps alter column preferred_categories set default '{}';
alter table reps alter column preferred_company_types set default '{}';
alter table reps alter column preferred_compensation set default '{}';

update reps
set availability_status = case
  when open_to_new_lines is false then 'not_open'
  else 'open'
end
where availability_status is null;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'reps_profile_status_check') then
    alter table reps add constraint reps_profile_status_check
      check (profile_status is null or profile_status in ('draft', 'active', 'hidden', 'suspended'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'reps_source_check') then
    alter table reps add constraint reps_source_check
      check (source is null or source in ('self_signup', 'admin', 'imported', 'claimed'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'reps_availability_status_check') then
    alter table reps add constraint reps_availability_status_check
      check (availability_status is null or availability_status in ('open', 'selectively_open', 'not_open'));
  end if;
end;
$$;

create unique index if not exists reps_profile_slug_unique_idx on reps(profile_slug) where profile_slug is not null;
create index if not exists reps_profile_status_idx on reps(profile_status);
create index if not exists reps_featured_idx on reps(featured);
create index if not exists reps_open_to_new_lines_idx on reps(open_to_new_lines);
create index if not exists reps_last_active_at_idx on reps(last_active_at desc);
create index if not exists reps_response_rate_idx on reps(response_rate);
create index if not exists reps_response_time_hours_idx on reps(response_time_hours);
create index if not exists reps_source_idx on reps(source);
create index if not exists reps_availability_status_idx on reps(availability_status);
create index if not exists reps_minimum_commission_idx on reps(minimum_commission);
create index if not exists reps_industries_gin_idx on reps using gin(industries);
create index if not exists reps_customer_types_gin_idx on reps using gin(customer_types);
create index if not exists reps_states_gin_idx on reps using gin(states);
create index if not exists reps_zip_codes_gin_idx on reps using gin(zip_codes);
create index if not exists reps_compensation_types_gin_idx on reps using gin(compensation_types);
create index if not exists reps_preferred_categories_gin_idx on reps using gin(preferred_categories);
create index if not exists reps_preferred_company_types_gin_idx on reps using gin(preferred_company_types);
create index if not exists reps_preferred_compensation_gin_idx on reps using gin(preferred_compensation);

create or replace function set_reps_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists reps_updated_at on reps;
create trigger reps_updated_at
before update on reps
for each row execute function set_reps_updated_at();

alter table reps enable row level security;

drop policy if exists "public read reps" on reps;
drop policy if exists "public insert reps" on reps;
create policy "public read reps" on reps
  for select using (active is true and coalesce(profile_status, 'active') = 'active');
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
  owner_user_id  uuid,
  prospect_id    text not null,
  name           text,
  category       text,
  stage          text,
  note           text,
  next_follow_up date,
  last_contacted date,
  call_attempts  integer default 0,
  outcome        text,
  unique(owner_email, owner_key_hash, prospect_id)
);

alter table pipeline_entries add column if not exists owner_key_hash text;
alter table pipeline_entries add column if not exists owner_user_id uuid;
alter table pipeline_entries add column if not exists last_contacted date;
alter table pipeline_entries add column if not exists call_attempts integer default 0;
alter table pipeline_entries drop constraint if exists pipeline_entries_owner_email_prospect_id_key;
create unique index if not exists pipeline_entries_owner_key_unique
  on pipeline_entries(owner_email, owner_key_hash, prospect_id);
create index if not exists pipeline_entries_owner_user_id_idx
  on pipeline_entries(owner_user_id);

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
  company_id    bigint,
  opportunity_id bigint,
  rating        int not null check (rating between 1 and 5),
  reviewer      text,
  title         text,
  review        text,
  customer_name text,
  comment       text,
  verified      boolean default false,
  verified_relationship boolean default false,
  status        text default 'pending',
  approved_at   timestamptz,
  reviewed_at   timestamptz,
  reviewed_by   text,
  moderation_notes text
);

alter table reviews add column if not exists lead_id bigint;
alter table reviews add column if not exists company_id bigint;
alter table reviews add column if not exists opportunity_id bigint;
alter table reviews add column if not exists reviewer text;
alter table reviews add column if not exists title text;
alter table reviews add column if not exists review text;
alter table reviews add column if not exists verified boolean default false;
alter table reviews add column if not exists verified_relationship boolean default false;
alter table reviews add column if not exists status text default 'pending';
alter table reviews add column if not exists approved_at timestamptz;
alter table reviews add column if not exists reviewed_at timestamptz;
alter table reviews add column if not exists reviewed_by text;
alter table reviews add column if not exists moderation_notes text;

alter table reviews alter column verified_relationship set default false;
alter table reviews alter column status set default 'pending';

update reviews
set
  reviewer = coalesce(reviewer, customer_name),
  review = coalesce(review, comment),
  verified_relationship = coalesce(verified_relationship, verified, false),
  status = case
    when status is null and coalesce(verified, false) is true then 'approved'
    when status is null then 'pending'
    else status
  end,
  approved_at = case
    when approved_at is null and coalesce(verified, false) is true then created_at
    else approved_at
  end
where reviewer is null
   or review is null
   or status is null
   or verified_relationship is null
   or approved_at is null;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'reviews_status_check') then
    alter table reviews add constraint reviews_status_check
      check (status in ('pending', 'approved', 'rejected'));
  end if;
end;
$$;

create unique index if not exists reviews_unique_lead_id_idx on reviews(lead_id) where lead_id is not null;
create index if not exists reviews_rep_status_idx on reviews(rep_id, status);
create index if not exists reviews_company_id_idx on reviews(company_id);
create index if not exists reviews_opportunity_id_idx on reviews(opportunity_id);
create index if not exists reviews_verified_relationship_idx on reviews(verified_relationship);
create index if not exists reviews_created_at_idx on reviews(created_at desc);

alter table reviews enable row level security;

drop policy if exists "public read reviews" on reviews;
drop policy if exists "public insert reviews" on reviews;
create policy "public read reviews" on reviews for select using (status = 'approved');


-- ------------------------------------------------------------------------- --
-- COMPANIES: public company profiles for the two-sided marketplace.
-- Public visitors can read active company profiles. Contact fields are not
-- rendered by the app by default; writes go through service_role validation.
-- ------------------------------------------------------------------------- --
create table if not exists companies (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  name           text not null,
  slug           text,
  logo_url       text,
  website        text,
  description    text,
  industries     text[] default '{}',
  categories     text[] default '{}',
  company_size   text,
  headquarters   text,
  states_needed  text[] default '{}',
  metros_needed  text[] default '{}',
  customer_types text[] default '{}',
  opportunities  text,
  verified       boolean default false,
  profile_status text default 'active',
  contact_name   text,
  contact_email  text,
  edit_code_hash text,
  source         text default 'self_signup'
);

alter table companies add column if not exists updated_at timestamptz default now();
alter table companies add column if not exists slug text;
alter table companies add column if not exists logo_url text;
alter table companies add column if not exists website text;
alter table companies add column if not exists description text;
alter table companies add column if not exists industries text[] default '{}';
alter table companies add column if not exists categories text[] default '{}';
alter table companies add column if not exists company_size text;
alter table companies add column if not exists headquarters text;
alter table companies add column if not exists states_needed text[] default '{}';
alter table companies add column if not exists metros_needed text[] default '{}';
alter table companies add column if not exists customer_types text[] default '{}';
alter table companies add column if not exists opportunities text;
alter table companies add column if not exists verified boolean default false;
alter table companies add column if not exists profile_status text default 'active';
alter table companies add column if not exists contact_name text;
alter table companies add column if not exists contact_email text;
alter table companies add column if not exists edit_code_hash text;
alter table companies add column if not exists source text default 'self_signup';

alter table companies alter column industries set default '{}';
alter table companies alter column categories set default '{}';
alter table companies alter column states_needed set default '{}';
alter table companies alter column metros_needed set default '{}';
alter table companies alter column customer_types set default '{}';
alter table companies alter column verified set default false;
alter table companies alter column profile_status set default 'active';
alter table companies alter column source set default 'self_signup';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'companies_profile_status_check') then
    alter table companies add constraint companies_profile_status_check
      check (profile_status is null or profile_status in ('draft', 'active', 'hidden', 'suspended'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'companies_source_check') then
    alter table companies add constraint companies_source_check
      check (source is null or source in ('self_signup', 'admin', 'imported', 'claimed'));
  end if;
end;
$$;

create unique index if not exists companies_slug_unique_idx on companies(slug) where slug is not null;
create index if not exists companies_profile_status_idx on companies(profile_status);
create index if not exists companies_verified_idx on companies(verified);
create index if not exists companies_created_at_idx on companies(created_at desc);
create index if not exists companies_industries_gin_idx on companies using gin(industries);
create index if not exists companies_categories_gin_idx on companies using gin(categories);
create index if not exists companies_states_needed_gin_idx on companies using gin(states_needed);
create index if not exists companies_metros_needed_gin_idx on companies using gin(metros_needed);
create index if not exists companies_customer_types_gin_idx on companies using gin(customer_types);

create or replace function set_companies_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists companies_updated_at on companies;
create trigger companies_updated_at
before update on companies
for each row execute function set_companies_updated_at();

alter table companies enable row level security;
drop policy if exists "public read companies" on companies;
drop policy if exists "public insert companies" on companies;
create policy "public read companies" on companies
  for select using (profile_status = 'active');


-- ------------------------------------------------------------------------- --
-- OPPORTUNITIES: companies seeking independent sales representatives.
-- Public visitors can read active, unexpired opportunities. Writes go through
-- the app with service_role validation; payments are intentionally out of scope.
-- ------------------------------------------------------------------------- --
create table if not exists opportunities (
  id                    bigint generated always as identity primary key,
  company_id            bigint references companies(id) on delete set null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  title                 text not null,
  description           text,
  categories            text[] default '{}',
  industries            text[] default '{}',
  customer_types        text[] default '{}',
  metros                text[] default '{}',
  states                text[] default '{}',
  zip_codes             text[] default '{}',
  territory_type        text default 'flexible',
  compensation_types    text[] default '{}',
  commission_min        numeric(10,2),
  commission_max        numeric(10,2),
  recurring_commission  boolean default false,
  exclusive_territory   boolean default false,
  experience_required   integer default 0,
  active                boolean default true,
  featured              boolean default false,
  application_count     integer default 0,
  direct_competitors    text[] default '{}',
  competitor_categories text[] default '{}',
  competitor_info_public boolean default false,
  expires_at            timestamptz
);

alter table opportunities add column if not exists company_id bigint references companies(id) on delete set null;
alter table opportunities add column if not exists updated_at timestamptz default now();
alter table opportunities add column if not exists title text;
alter table opportunities add column if not exists description text;
alter table opportunities add column if not exists categories text[] default '{}';
alter table opportunities add column if not exists industries text[] default '{}';
alter table opportunities add column if not exists customer_types text[] default '{}';
alter table opportunities add column if not exists metros text[] default '{}';
alter table opportunities add column if not exists states text[] default '{}';
alter table opportunities add column if not exists zip_codes text[] default '{}';
alter table opportunities add column if not exists territory_type text default 'flexible';
alter table opportunities add column if not exists compensation_types text[] default '{}';
alter table opportunities add column if not exists commission_min numeric(10,2);
alter table opportunities add column if not exists commission_max numeric(10,2);
alter table opportunities add column if not exists recurring_commission boolean default false;
alter table opportunities add column if not exists exclusive_territory boolean default false;
alter table opportunities add column if not exists experience_required integer default 0;
alter table opportunities add column if not exists active boolean default true;
alter table opportunities add column if not exists featured boolean default false;
alter table opportunities add column if not exists application_count integer default 0;
alter table opportunities add column if not exists direct_competitors text[] default '{}';
alter table opportunities add column if not exists competitor_categories text[] default '{}';
alter table opportunities add column if not exists competitor_info_public boolean default false;
alter table opportunities add column if not exists expires_at timestamptz;

alter table opportunities alter column categories set default '{}';
alter table opportunities alter column industries set default '{}';
alter table opportunities alter column customer_types set default '{}';
alter table opportunities alter column metros set default '{}';
alter table opportunities alter column states set default '{}';
alter table opportunities alter column zip_codes set default '{}';
alter table opportunities alter column territory_type set default 'flexible';
alter table opportunities alter column compensation_types set default '{}';
alter table opportunities alter column recurring_commission set default false;
alter table opportunities alter column exclusive_territory set default false;
alter table opportunities alter column experience_required set default 0;
alter table opportunities alter column active set default true;
alter table opportunities alter column featured set default false;
alter table opportunities alter column application_count set default 0;
alter table opportunities alter column direct_competitors set default '{}';
alter table opportunities alter column competitor_categories set default '{}';
alter table opportunities alter column competitor_info_public set default false;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'opportunities_territory_type_check') then
    alter table opportunities add constraint opportunities_territory_type_check
      check (territory_type is null or territory_type in ('exclusive', 'shared', 'flexible', 'remote'));
  end if;
end;
$$;

create index if not exists opportunities_company_id_idx on opportunities(company_id);
create index if not exists opportunities_active_idx on opportunities(active);
create index if not exists opportunities_featured_idx on opportunities(featured);
create index if not exists opportunities_created_at_idx on opportunities(created_at desc);
create index if not exists opportunities_expires_at_idx on opportunities(expires_at);
create index if not exists opportunities_exclusive_territory_idx on opportunities(exclusive_territory);
create index if not exists opportunities_experience_required_idx on opportunities(experience_required);
create index if not exists opportunities_categories_gin_idx on opportunities using gin(categories);
create index if not exists opportunities_industries_gin_idx on opportunities using gin(industries);
create index if not exists opportunities_customer_types_gin_idx on opportunities using gin(customer_types);
create index if not exists opportunities_metros_gin_idx on opportunities using gin(metros);
create index if not exists opportunities_states_gin_idx on opportunities using gin(states);
create index if not exists opportunities_compensation_types_gin_idx on opportunities using gin(compensation_types);
create index if not exists opportunities_direct_competitors_gin_idx on opportunities using gin(direct_competitors);
create index if not exists opportunities_competitor_categories_gin_idx on opportunities using gin(competitor_categories);

create or replace function set_opportunities_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists opportunities_updated_at on opportunities;
create trigger opportunities_updated_at
before update on opportunities
for each row execute function set_opportunities_updated_at();

alter table opportunities enable row level security;
drop policy if exists "public read opportunities" on opportunities;
drop policy if exists "public insert opportunities" on opportunities;
create policy "public read opportunities" on opportunities
  for select using (active is true and (expires_at is null or expires_at > now()));

-- PROFILE CLAIMS: claim requests are private. Public visitors submit through
-- the Streamlit server using service_role, and admins review them in-app.
create table if not exists profile_claims (
  id                       bigint generated always as identity primary key,
  rep_id                   bigint references reps(id) on delete cascade,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),
  reviewed_at              timestamptz,
  claimant_email           text not null,
  claimant_name            text,
  message                  text,
  status                   text not null default 'pending',
  verification_token_hash  text,
  verification_sent_at     timestamptz,
  email_verified_at        timestamptz,
  reviewed_by              text,
  admin_notes              text
);

alter table profile_claims add column if not exists rep_id bigint references reps(id) on delete cascade;
alter table profile_claims add column if not exists created_at timestamptz default now();
alter table profile_claims add column if not exists updated_at timestamptz default now();
alter table profile_claims add column if not exists reviewed_at timestamptz;
alter table profile_claims add column if not exists claimant_email text;
alter table profile_claims add column if not exists claimant_name text;
alter table profile_claims add column if not exists message text;
alter table profile_claims add column if not exists status text default 'pending';
alter table profile_claims add column if not exists verification_token_hash text;
alter table profile_claims add column if not exists verification_sent_at timestamptz;
alter table profile_claims add column if not exists email_verified_at timestamptz;
alter table profile_claims add column if not exists reviewed_by text;
alter table profile_claims add column if not exists admin_notes text;

alter table profile_claims alter column created_at set default now();
alter table profile_claims alter column updated_at set default now();
alter table profile_claims alter column status set default 'pending';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'profile_claims_status_check') then
    alter table profile_claims add constraint profile_claims_status_check
      check (status in ('pending', 'approved', 'rejected'));
  end if;
end;
$$;

create index if not exists profile_claims_rep_id_idx on profile_claims(rep_id);
create index if not exists profile_claims_status_idx on profile_claims(status);
create index if not exists profile_claims_claimant_email_idx on profile_claims(lower(claimant_email));
create index if not exists profile_claims_created_at_idx on profile_claims(created_at desc);

create or replace function set_profile_claims_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists profile_claims_updated_at on profile_claims;
create trigger profile_claims_updated_at
before update on profile_claims
for each row execute function set_profile_claims_updated_at();

alter table profile_claims enable row level security;
drop policy if exists "public read profile claims" on profile_claims;
drop policy if exists "public insert profile claims" on profile_claims;

-- CONNECTIONS: private company-to-rep connection requests. Contact details are
-- shown by the app only after accepted.
create table if not exists connections (
  id              bigint generated always as identity primary key,
  company_id      bigint references companies(id) on delete cascade,
  rep_id          bigint references reps(id) on delete cascade,
  opportunity_id  bigint references opportunities(id) on delete set null,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  status          text not null default 'pending',
  message         text,
  initiated_by    text not null default 'company'
);

alter table connections add column if not exists company_id bigint references companies(id) on delete cascade;
alter table connections add column if not exists rep_id bigint references reps(id) on delete cascade;
alter table connections add column if not exists opportunity_id bigint references opportunities(id) on delete set null;
alter table connections add column if not exists created_at timestamptz default now();
alter table connections add column if not exists updated_at timestamptz default now();
alter table connections add column if not exists status text default 'pending';
alter table connections add column if not exists message text;
alter table connections add column if not exists initiated_by text default 'company';

alter table connections alter column created_at set default now();
alter table connections alter column updated_at set default now();
alter table connections alter column status set default 'pending';
alter table connections alter column initiated_by set default 'company';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'connections_status_check') then
    alter table connections add constraint connections_status_check
      check (status in ('pending', 'accepted', 'declined', 'withdrawn'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'connections_initiated_by_check') then
    alter table connections add constraint connections_initiated_by_check
      check (initiated_by in ('company', 'rep', 'admin'));
  end if;
end;
$$;

create unique index if not exists connections_open_unique_idx
  on connections(company_id, rep_id, coalesce(opportunity_id, 0))
  where status in ('pending', 'accepted');
create index if not exists connections_company_id_idx on connections(company_id);
create index if not exists connections_rep_id_idx on connections(rep_id);
create index if not exists connections_opportunity_id_idx on connections(opportunity_id);
create index if not exists connections_status_idx on connections(status);
create index if not exists connections_created_at_idx on connections(created_at desc);

create or replace function set_connections_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists connections_updated_at on connections;
create trigger connections_updated_at
before update on connections
for each row execute function set_connections_updated_at();

alter table connections enable row level security;
drop policy if exists "public read connections" on connections;
drop policy if exists "public insert connections" on connections;
drop policy if exists "public update connections" on connections;

-- SHORTLISTS: normalized favorites/saves. Anonymous session saves work now;
-- owner_id can later attach rows to authenticated accounts.
create table if not exists shortlist_items (
  id           bigint generated always as identity primary key,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  owner_type   text not null default 'anonymous',
  owner_id     text,
  session_key  text,
  target_type  text not null,
  target_id    text not null,
  collection   text not null default 'Saved',
  notes        text
);

alter table shortlist_items add column if not exists created_at timestamptz default now();
alter table shortlist_items add column if not exists updated_at timestamptz default now();
alter table shortlist_items add column if not exists owner_type text default 'anonymous';
alter table shortlist_items add column if not exists owner_id text;
alter table shortlist_items add column if not exists session_key text;
alter table shortlist_items add column if not exists target_type text;
alter table shortlist_items add column if not exists target_id text;
alter table shortlist_items add column if not exists collection text default 'Saved';
alter table shortlist_items add column if not exists notes text;

alter table shortlist_items alter column created_at set default now();
alter table shortlist_items alter column updated_at set default now();
alter table shortlist_items alter column owner_type set default 'anonymous';
alter table shortlist_items alter column collection set default 'Saved';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'shortlist_items_owner_type_check') then
    alter table shortlist_items add constraint shortlist_items_owner_type_check
      check (owner_type in ('anonymous', 'company', 'rep', 'user'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'shortlist_items_target_type_check') then
    alter table shortlist_items add constraint shortlist_items_target_type_check
      check (target_type in ('rep', 'company', 'opportunity'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'shortlist_items_collection_check') then
    alter table shortlist_items add constraint shortlist_items_collection_check
      check (collection in ('Saved', 'Contact Later', 'Strong Candidates'));
  end if;
end;
$$;

create unique index if not exists shortlist_items_unique_target_idx
  on shortlist_items(owner_type, coalesce(owner_id, ''), coalesce(session_key, ''), target_type, target_id);
create index if not exists shortlist_items_owner_idx on shortlist_items(owner_type, owner_id, session_key);
create index if not exists shortlist_items_target_idx on shortlist_items(target_type, target_id);
create index if not exists shortlist_items_collection_idx on shortlist_items(collection);
create index if not exists shortlist_items_created_at_idx on shortlist_items(created_at desc);

create or replace function set_shortlist_items_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists shortlist_items_updated_at on shortlist_items;
create trigger shortlist_items_updated_at
before update on shortlist_items
for each row execute function set_shortlist_items_updated_at();

alter table shortlist_items enable row level security;
drop policy if exists "public read shortlist items" on shortlist_items;
drop policy if exists "public insert shortlist items" on shortlist_items;
drop policy if exists "public update shortlist items" on shortlist_items;
drop policy if exists "public delete shortlist items" on shortlist_items;

-- 014_auth_accounts_roles.sql
-- Supabase Auth account profiles, roles, ownership, and RLS hardening.
-- Admin authorization uses admin_account_roles, not user-editable profile metadata.

create table if not exists account_profiles (
  user_id      uuid primary key references auth.users(id) on delete cascade,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  email        text,
  display_name text,
  role         text not null default 'rep'
);

alter table account_profiles add column if not exists email text;
alter table account_profiles add column if not exists display_name text;
alter table account_profiles add column if not exists role text default 'rep';
alter table account_profiles add column if not exists updated_at timestamptz default now();
alter table account_profiles alter column role set default 'rep';

create table if not exists admin_account_roles (
  user_id    uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  granted_by text
);

alter table reps add column if not exists owner_user_id uuid references auth.users(id) on delete set null;
alter table companies add column if not exists owner_user_id uuid references auth.users(id) on delete set null;
alter table opportunities add column if not exists owner_user_id uuid references auth.users(id) on delete set null;
alter table connections add column if not exists owner_user_id uuid references auth.users(id) on delete set null;
alter table shortlist_items add column if not exists owner_user_id uuid references auth.users(id) on delete cascade;
alter table pipeline_entries add column if not exists owner_user_id uuid references auth.users(id) on delete set null;

create index if not exists reps_owner_user_id_idx on reps(owner_user_id);
create index if not exists companies_owner_user_id_idx on companies(owner_user_id);
create index if not exists opportunities_owner_user_id_idx on opportunities(owner_user_id);
create index if not exists connections_owner_user_id_idx on connections(owner_user_id);
create index if not exists shortlist_items_owner_user_id_idx on shortlist_items(owner_user_id);
create index if not exists pipeline_entries_owner_user_id_idx on pipeline_entries(owner_user_id);

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'account_profiles_role_check') then
    alter table account_profiles add constraint account_profiles_role_check
      check (role in ('rep', 'company', 'admin'));
  end if;
end;
$$;

create or replace function set_account_profiles_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists account_profiles_updated_at on account_profiles;
create trigger account_profiles_updated_at
before update on account_profiles
for each row execute function set_account_profiles_updated_at();

create or replace function is_admin_user()
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (
    select 1 from admin_account_roles
    where user_id = auth.uid()
  );
$$;

alter table account_profiles enable row level security;
alter table admin_account_roles enable row level security;
alter table reps enable row level security;
alter table companies enable row level security;
alter table opportunities enable row level security;
alter table connections enable row level security;
alter table shortlist_items enable row level security;
alter table pipeline_entries enable row level security;

drop policy if exists "account profile owner read" on account_profiles;
drop policy if exists "account profile owner insert" on account_profiles;
drop policy if exists "account profile owner update safe fields" on account_profiles;
drop policy if exists "account profile admin read" on account_profiles;
create policy "account profile owner read" on account_profiles
  for select using (auth.uid() = user_id);
create policy "account profile owner insert" on account_profiles
  for insert with check (auth.uid() = user_id and role in ('rep', 'company'));
create policy "account profile owner update safe fields" on account_profiles
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id and role in ('rep', 'company'));
create policy "account profile admin read" on account_profiles
  for select using (is_admin_user());

drop policy if exists "admin roles admin read" on admin_account_roles;
create policy "admin roles admin read" on admin_account_roles
  for select using (is_admin_user());

drop policy if exists "owner insert reps" on reps;
drop policy if exists "owner update reps" on reps;
drop policy if exists "owner delete reps" on reps;
drop policy if exists "admin manage reps" on reps;
create policy "owner insert reps" on reps for insert
  with check (auth.uid() = owner_user_id);
create policy "owner update reps" on reps for update
  using (auth.uid() = owner_user_id)
  with check (auth.uid() = owner_user_id);
create policy "owner delete reps" on reps for delete
  using (auth.uid() = owner_user_id);
create policy "admin manage reps" on reps for all
  using (is_admin_user()) with check (is_admin_user());

drop policy if exists "owner insert companies" on companies;
drop policy if exists "owner update companies" on companies;
drop policy if exists "owner delete companies" on companies;
drop policy if exists "admin manage companies" on companies;
create policy "owner insert companies" on companies for insert
  with check (auth.uid() = owner_user_id);
create policy "owner update companies" on companies for update
  using (auth.uid() = owner_user_id)
  with check (auth.uid() = owner_user_id);
create policy "owner delete companies" on companies for delete
  using (auth.uid() = owner_user_id);
create policy "admin manage companies" on companies for all
  using (is_admin_user()) with check (is_admin_user());

drop policy if exists "owner insert opportunities" on opportunities;
drop policy if exists "owner update opportunities" on opportunities;
drop policy if exists "owner delete opportunities" on opportunities;
drop policy if exists "admin manage opportunities" on opportunities;
create policy "owner insert opportunities" on opportunities for insert
  with check (
    auth.uid() = owner_user_id
    and exists (
      select 1 from companies
      where companies.id = opportunities.company_id
        and companies.owner_user_id = auth.uid()
    )
  );
create policy "owner update opportunities" on opportunities for update
  using (auth.uid() = owner_user_id)
  with check (
    auth.uid() = owner_user_id
    and exists (
      select 1 from companies
      where companies.id = opportunities.company_id
        and companies.owner_user_id = auth.uid()
    )
  );
create policy "owner delete opportunities" on opportunities for delete
  using (auth.uid() = owner_user_id);
create policy "admin manage opportunities" on opportunities for all
  using (is_admin_user()) with check (is_admin_user());

drop policy if exists "owner read connections" on connections;
drop policy if exists "owner insert connections" on connections;
drop policy if exists "owner update connections" on connections;
drop policy if exists "admin manage connections" on connections;
create policy "owner read connections" on connections for select
  using (auth.uid() = owner_user_id or exists (select 1 from reps where reps.id = connections.rep_id and reps.owner_user_id = auth.uid()));
create policy "owner insert connections" on connections for insert
  with check (
    auth.uid() = owner_user_id
    and exists (
      select 1 from companies
      where companies.id = connections.company_id
        and companies.owner_user_id = auth.uid()
    )
  );
create policy "owner update connections" on connections for update
  using (auth.uid() = owner_user_id or exists (select 1 from reps where reps.id = connections.rep_id and reps.owner_user_id = auth.uid()))
  with check (auth.uid() = owner_user_id or exists (select 1 from reps where reps.id = connections.rep_id and reps.owner_user_id = auth.uid()));
create policy "admin manage connections" on connections for all
  using (is_admin_user()) with check (is_admin_user());

drop policy if exists "owner manage shortlist items" on shortlist_items;
create policy "owner manage shortlist items" on shortlist_items for all
  using (auth.uid() = owner_user_id)
  with check (auth.uid() = owner_user_id);

drop policy if exists "owner read pipeline entries" on pipeline_entries;
drop policy if exists "owner manage pipeline entries" on pipeline_entries;
create policy "owner read pipeline entries" on pipeline_entries for select
  using (auth.uid() = owner_user_id);
create policy "owner manage pipeline entries" on pipeline_entries for all
  using (auth.uid() = owner_user_id)
  with check (auth.uid() = owner_user_id);

-- 015_admin_dashboard_reports.sql
-- Admin dashboard support: reported content plus explicit admin RLS policies.

alter table companies add column if not exists featured boolean default false;
alter table companies alter column featured set default false;
create index if not exists companies_featured_idx on companies(featured);

create table if not exists content_reports (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  target_type    text not null,
  target_id      text not null,
  reason         text not null,
  details        text,
  reporter_email text,
  status         text not null default 'pending',
  reviewed_at    timestamptz,
  reviewed_by    text,
  admin_notes    text
);

alter table content_reports add column if not exists created_at timestamptz default now();
alter table content_reports add column if not exists updated_at timestamptz default now();
alter table content_reports add column if not exists target_type text;
alter table content_reports add column if not exists target_id text;
alter table content_reports add column if not exists reason text;
alter table content_reports add column if not exists details text;
alter table content_reports add column if not exists reporter_email text;
alter table content_reports add column if not exists status text default 'pending';
alter table content_reports add column if not exists reviewed_at timestamptz;
alter table content_reports add column if not exists reviewed_by text;
alter table content_reports add column if not exists admin_notes text;

alter table content_reports alter column created_at set default now();
alter table content_reports alter column updated_at set default now();
alter table content_reports alter column status set default 'pending';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'content_reports_target_type_check') then
    alter table content_reports add constraint content_reports_target_type_check
      check (target_type in ('rep', 'company', 'opportunity', 'review'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'content_reports_status_check') then
    alter table content_reports add constraint content_reports_status_check
      check (status in ('pending', 'reviewed', 'dismissed'));
  end if;
end;
$$;

create index if not exists content_reports_target_idx on content_reports(target_type, target_id);
create index if not exists content_reports_status_idx on content_reports(status);
create index if not exists content_reports_created_at_idx on content_reports(created_at desc);

create or replace function set_content_reports_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists content_reports_updated_at on content_reports;
create trigger content_reports_updated_at
before update on content_reports
for each row execute function set_content_reports_updated_at();

alter table content_reports enable row level security;
drop policy if exists "public read content reports" on content_reports;
drop policy if exists "public insert content reports" on content_reports;
drop policy if exists "admin manage content reports" on content_reports;
create policy "admin manage content reports" on content_reports for all
  using (is_admin_user()) with check (is_admin_user());

drop policy if exists "admin manage profile claims" on profile_claims;
create policy "admin manage profile claims" on profile_claims for all
  using (is_admin_user()) with check (is_admin_user());

drop policy if exists "admin manage reviews" on reviews;
create policy "admin manage reviews" on reviews for all
  using (is_admin_user()) with check (is_admin_user());

drop policy if exists "admin read leads" on leads;
create policy "admin read leads" on leads for select
  using (is_admin_user());

-- 016_monetization_entitlements.sql
-- Monetization architecture prep. This does not integrate payment processing.

alter table account_profiles add column if not exists subscription_plan text;
alter table account_profiles add column if not exists subscription_status text default 'free';
alter table account_profiles add column if not exists entitlement_overrides jsonb default '{}'::jsonb;
alter table account_profiles add column if not exists stripe_customer_id text;
alter table account_profiles add column if not exists stripe_subscription_id text;
alter table account_profiles add column if not exists subscription_current_period_end timestamptz;

alter table account_profiles alter column subscription_status set default 'free';
alter table account_profiles alter column entitlement_overrides set default '{}'::jsonb;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'account_profiles_subscription_plan_check') then
    alter table account_profiles add constraint account_profiles_subscription_plan_check
      check (
        subscription_plan is null
        or subscription_plan in ('rep_free', 'rep_pro', 'company_free', 'company_pro', 'admin')
      );
  end if;
  if not exists (select 1 from pg_constraint where conname = 'account_profiles_subscription_status_check') then
    alter table account_profiles add constraint account_profiles_subscription_status_check
      check (
        subscription_status is null
        or subscription_status in ('free', 'trialing', 'active', 'past_due', 'canceled', 'incomplete')
      );
  end if;
end;
$$;

create index if not exists account_profiles_subscription_plan_idx on account_profiles(subscription_plan);
create index if not exists account_profiles_subscription_status_idx on account_profiles(subscription_status);
create index if not exists account_profiles_stripe_customer_id_idx on account_profiles(stripe_customer_id);

revoke update (
  subscription_plan,
  subscription_status,
  entitlement_overrides,
  stripe_customer_id,
  stripe_subscription_id,
  subscription_current_period_end
) on account_profiles from anon, authenticated;

-- 017_public_seo_pages.sql
-- Public SEO page support. Adds stable opportunity slugs without creating
-- synthetic SEO content.

alter table opportunities add column if not exists slug text;

update opportunities
set slug = lower(regexp_replace(regexp_replace(coalesce(title, 'opportunity') || '-' || id::text, '[^a-zA-Z0-9]+', '-', 'g'), '(^-|-$)', '', 'g'))
where slug is null or slug = '';

create unique index if not exists opportunities_slug_unique_idx on opportunities(slug) where slug is not null;

-- 018_marketplace_analytics.sql
-- Lightweight internal marketplace analytics. Events avoid personal details and
-- store small structured metadata only.

create table if not exists marketplace_events (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  event_name  text not null,
  actor_role  text,
  actor_user_id uuid references auth.users(id) on delete set null,
  target_type text,
  target_id   text,
  category    text,
  metro       text,
  metadata    jsonb not null default '{}'::jsonb
);

alter table marketplace_events add column if not exists created_at timestamptz default now();
alter table marketplace_events add column if not exists event_name text;
alter table marketplace_events add column if not exists actor_role text;
alter table marketplace_events add column if not exists actor_user_id uuid references auth.users(id) on delete set null;
alter table marketplace_events add column if not exists target_type text;
alter table marketplace_events add column if not exists target_id text;
alter table marketplace_events add column if not exists category text;
alter table marketplace_events add column if not exists metro text;
alter table marketplace_events add column if not exists metadata jsonb default '{}'::jsonb;

alter table marketplace_events alter column created_at set default now();
alter table marketplace_events alter column metadata set default '{}'::jsonb;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'marketplace_events_event_name_check') then
    alter table marketplace_events add constraint marketplace_events_event_name_check
      check (event_name in (
        'rep_profile_view',
        'opportunity_view',
        'search',
        'save_rep',
        'save_opportunity',
        'connection_request',
        'connection_accept',
        'claim_profile',
        'signup',
        'company_profile_view'
      ));
  end if;
end;
$$;

create index if not exists marketplace_events_created_at_idx on marketplace_events(created_at desc);
create index if not exists marketplace_events_event_created_idx on marketplace_events(event_name, created_at desc);
create index if not exists marketplace_events_category_idx on marketplace_events(category);
create index if not exists marketplace_events_metro_idx on marketplace_events(metro);
create index if not exists marketplace_events_target_idx on marketplace_events(target_type, target_id);

alter table marketplace_events enable row level security;
drop policy if exists "public read marketplace events" on marketplace_events;
drop policy if exists "public insert marketplace events" on marketplace_events;
drop policy if exists "admin read marketplace events" on marketplace_events;
create policy "admin read marketplace events" on marketplace_events for select
  using (is_admin_user());

-- 019_production_readiness_hardening.sql
-- Tighten public marketplace reads without destroying existing data.
drop policy if exists "public read reps" on reps;
create policy "public read reps" on reps
  for select using (active is true and coalesce(profile_status, 'active') = 'active');

create index if not exists reps_active_profile_status_idx on reps(active, profile_status);
create index if not exists companies_profile_status_featured_idx on companies(profile_status, featured);
create index if not exists opportunities_active_featured_idx on opportunities(active, featured);

revoke select (email, phone, edit_code_hash, claim_email, owner_user_id)
  on reps from anon, authenticated;
revoke select (contact_name, contact_email, edit_code_hash, owner_user_id)
  on companies from anon, authenticated;
revoke select (direct_competitors, competitor_categories, owner_user_id)
  on opportunities from anon, authenticated;

-- 020_schema_tracking_and_admin_audit.sql
-- Track applied schema versions and prepare an admin audit trail.
create table if not exists schema_migrations (
  version     text primary key,
  name        text not null,
  applied_at  timestamptz not null default now()
);

insert into schema_migrations(version, name) values
  ('001', 'initial_marketplace'),
  ('002', 'trust_management'),
  ('003', 'pipeline_followups'),
  ('004', 'verified_reviews_and_geo'),
  ('005', 'rep_profiles'),
  ('006', 'rep_availability_preferences'),
  ('007', 'company_profiles'),
  ('008', 'sales_opportunities'),
  ('009', 'product_line_conflicts'),
  ('010', 'profile_claims'),
  ('011', 'moderated_reviews'),
  ('012', 'connections'),
  ('013', 'shortlists'),
  ('014', 'auth_accounts_roles'),
  ('015', 'admin_dashboard_reports'),
  ('016', 'monetization_entitlements'),
  ('017', 'public_seo_pages'),
  ('018', 'marketplace_analytics'),
  ('019', 'production_readiness_hardening'),
  ('020', 'schema_tracking_and_admin_audit')
on conflict (version) do update
  set name = excluded.name;

create table if not exists admin_audit_log (
  id              bigint generated always as identity primary key,
  created_at      timestamptz not null default now(),
  actor_user_id   uuid references auth.users(id) on delete set null,
  actor_email     text,
  action          text not null,
  target_type     text,
  target_id       text,
  metadata        jsonb not null default '{}'::jsonb
);

alter table admin_audit_log add column if not exists created_at timestamptz default now();
alter table admin_audit_log add column if not exists actor_user_id uuid references auth.users(id) on delete set null;
alter table admin_audit_log add column if not exists actor_email text;
alter table admin_audit_log add column if not exists action text;
alter table admin_audit_log add column if not exists target_type text;
alter table admin_audit_log add column if not exists target_id text;
alter table admin_audit_log add column if not exists metadata jsonb default '{}'::jsonb;

alter table admin_audit_log alter column created_at set default now();
alter table admin_audit_log alter column metadata set default '{}'::jsonb;

create index if not exists admin_audit_log_created_at_idx on admin_audit_log(created_at desc);
create index if not exists admin_audit_log_actor_idx on admin_audit_log(actor_user_id, created_at desc);
create index if not exists admin_audit_log_target_idx on admin_audit_log(target_type, target_id);
create index if not exists admin_audit_log_action_idx on admin_audit_log(action);

alter table schema_migrations enable row level security;
alter table admin_audit_log enable row level security;

drop policy if exists "public read schema migrations" on schema_migrations;
create policy "public read schema migrations" on schema_migrations
  for select using (true);

drop policy if exists "admin read admin audit log" on admin_audit_log;
create policy "admin read admin audit log" on admin_audit_log
  for select using (is_admin_user());
