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

