-- FirstKnock weekly email: one row per user (territory, sources, on/off, leads already emailed).
-- Written only by the API with the service key; RLS on with no policies keeps it private.
create table if not exists lead_digests (
  user_id      uuid primary key,
  email        text not null,
  active       boolean not null default true,
  territory    text not null default 'Peninsula (Belmont → Mountain View)',
  sources      text[] not null default '{permits,licenses,news,map}',
  seen         text[] not null default '{}',
  last_sent_at timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

alter table lead_digests enable row level security;

create index if not exists lead_digests_active_idx on lead_digests(active, last_sent_at);
