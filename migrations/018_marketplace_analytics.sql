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
