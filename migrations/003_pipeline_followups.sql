-- Private pipeline persistence with follow-up workflow fields.

create table if not exists pipeline_entries (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  owner_email    text not null,
  owner_key_hash text,
  owner_user_id  uuid,
  prospect_id    text not null,
  name           text,
  category       text,
  stage          text,
  note           text,
  next_follow_up date,
  last_contacted date,
  call_attempts  integer default 0,
  outcome        text,
  unique(owner_email, owner_key_hash, prospect_id)
);

alter table pipeline_entries add column if not exists owner_key_hash text;
alter table pipeline_entries add column if not exists owner_user_id uuid;
alter table pipeline_entries add column if not exists last_contacted date;
alter table pipeline_entries add column if not exists call_attempts integer default 0;
alter table pipeline_entries drop constraint if exists pipeline_entries_owner_email_prospect_id_key;
create unique index if not exists pipeline_entries_owner_key_unique
  on pipeline_entries(owner_email, owner_key_hash, prospect_id);
create index if not exists pipeline_entries_owner_user_id_idx
  on pipeline_entries(owner_user_id);

alter table pipeline_entries enable row level security;

create or replace function set_pipeline_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists pipeline_entries_updated_at on pipeline_entries;
create trigger pipeline_entries_updated_at
before update on pipeline_entries
for each row execute function set_pipeline_updated_at();

