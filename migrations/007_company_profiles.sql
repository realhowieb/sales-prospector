-- Company profiles for the two-sided marketplace.
-- Safe to run more than once. Public submissions are intended to go through
-- the Streamlit server with service_role validation; no public writes.

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
