-- Claim requests for imported/admin-created rep profiles.
-- Public users submit through the Streamlit server using service_role. Do not
-- expose private rep ownership data or grant access automatically.

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
