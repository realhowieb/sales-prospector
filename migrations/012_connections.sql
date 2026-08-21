-- Company-to-rep connection requests.
-- Contact information remains app-gated: requests are private and public users
-- create/update them through the Streamlit server using service_role.

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
