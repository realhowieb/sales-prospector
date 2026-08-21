-- Initial marketplace, lead, review, and pipeline tables.
-- Safe to run on a fresh Supabase project; for existing projects, use the
-- later migrations or the idempotent supabase_setup.sql.

create table if not exists reps (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  name          text not null,
  company       text not null,
  categories    text[] not null default '{}',
  metros        text[] not null default '{}',
  deal          text,
  deal_strength double precision default 0.5,
  rating        double precision default 0,
  reviews       integer default 0,
  response      text,
  verified      boolean default false,
  blurb         text,
  email         text,
  phone         text
);

create table if not exists leads (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  rep_id         text,
  rep_company    text,
  rep_email      text,
  customer_name  text,
  customer_email text,
  customer_phone text,
  message        text,
  category       text,
  metro          text,
  notified       boolean default false
);

create table if not exists reviews (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  rep_id        text not null,
  rating        int not null check (rating between 1 and 5),
  customer_name text,
  comment       text
);

