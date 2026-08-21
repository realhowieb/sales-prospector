-- Admin dashboard support: reported content plus explicit admin RLS policies.
-- Safe to run repeatedly. Public reporting can later be added through a
-- server-side function; direct anon writes remain closed by default.

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
