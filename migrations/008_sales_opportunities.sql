-- Sales opportunities posted by companies seeking independent reps.
-- Safe to run repeatedly. Public can read active, unexpired opportunities;
-- writes are intended to go through Streamlit with the service_role key.

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
