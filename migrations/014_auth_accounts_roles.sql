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

-- Public reads stay intentionally broad for marketplace discovery, while
-- authenticated owners and verified admins get write access through RLS.
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
