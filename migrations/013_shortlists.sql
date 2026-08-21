-- Favorites / shortlists for reps, companies, and opportunities.
-- Anonymous session saves are supported now; owner_id can later point to an
-- authenticated user/company/rep without changing target relationships.

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
