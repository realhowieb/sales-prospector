-- Professional rep profile fields for the marketplace.
-- Safe to run more than once. Adds columns/indexes only; does not remove data
-- or change existing RLS policies.

alter table reps add column if not exists profile_slug text;
alter table reps add column if not exists headline text;
alter table reps add column if not exists years_experience integer default 0;
alter table reps add column if not exists industries text[] default '{}';
alter table reps add column if not exists customer_types text[] default '{}';
alter table reps add column if not exists states text[] default '{}';
alter table reps add column if not exists zip_codes text[] default '{}';
alter table reps add column if not exists territory_radius integer default 25;
alter table reps add column if not exists open_to_new_lines boolean default true;
alter table reps add column if not exists commission_min numeric(10,2);
alter table reps add column if not exists commission_max numeric(10,2);
alter table reps add column if not exists compensation_types text[] default '{}';
alter table reps add column if not exists existing_lines text[] default '{}';
alter table reps add column if not exists competing_lines text[] default '{}';
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
end;
$$;

create unique index if not exists reps_profile_slug_unique_idx
  on reps(profile_slug)
  where profile_slug is not null;

create index if not exists reps_profile_status_idx on reps(profile_status);
create index if not exists reps_featured_idx on reps(featured);
create index if not exists reps_open_to_new_lines_idx on reps(open_to_new_lines);
create index if not exists reps_last_active_at_idx on reps(last_active_at desc);
create index if not exists reps_response_rate_idx on reps(response_rate);
create index if not exists reps_response_time_hours_idx on reps(response_time_hours);
create index if not exists reps_source_idx on reps(source);
create index if not exists reps_industries_gin_idx on reps using gin(industries);
create index if not exists reps_customer_types_gin_idx on reps using gin(customer_types);
create index if not exists reps_states_gin_idx on reps using gin(states);
create index if not exists reps_zip_codes_gin_idx on reps using gin(zip_codes);
create index if not exists reps_compensation_types_gin_idx on reps using gin(compensation_types);

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
