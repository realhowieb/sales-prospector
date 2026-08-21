-- Moderated, relationship-aware reviews.
-- Reviews are submitted through the app server after token validation and only
-- approved reviews are publicly readable or counted in aggregates.

alter table reviews add column if not exists reviewer text;
alter table reviews add column if not exists title text;
alter table reviews add column if not exists review text;
alter table reviews add column if not exists company_id bigint references companies(id) on delete set null;
alter table reviews add column if not exists opportunity_id bigint references opportunities(id) on delete set null;
alter table reviews add column if not exists verified_relationship boolean default false;
alter table reviews add column if not exists status text default 'pending';
alter table reviews add column if not exists approved_at timestamptz;
alter table reviews add column if not exists reviewed_at timestamptz;
alter table reviews add column if not exists reviewed_by text;
alter table reviews add column if not exists moderation_notes text;

alter table reviews alter column verified_relationship set default false;
alter table reviews alter column status set default 'pending';

update reviews
set
  reviewer = coalesce(reviewer, customer_name),
  review = coalesce(review, comment),
  verified_relationship = coalesce(verified_relationship, verified, false),
  status = case
    when status is null and coalesce(verified, false) is true then 'approved'
    when status is null then 'pending'
    else status
  end,
  approved_at = case
    when approved_at is null and coalesce(verified, false) is true then created_at
    else approved_at
  end
where reviewer is null
   or review is null
   or status is null
   or verified_relationship is null
   or approved_at is null;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'reviews_status_check') then
    alter table reviews add constraint reviews_status_check
      check (status in ('pending', 'approved', 'rejected'));
  end if;
end;
$$;

create unique index if not exists reviews_unique_lead_id_idx on reviews(lead_id) where lead_id is not null;
create index if not exists reviews_rep_status_idx on reviews(rep_id, status);
create index if not exists reviews_company_id_idx on reviews(company_id);
create index if not exists reviews_opportunity_id_idx on reviews(opportunity_id);
create index if not exists reviews_verified_relationship_idx on reviews(verified_relationship);
create index if not exists reviews_created_at_idx on reviews(created_at desc);

alter table reviews enable row level security;
drop policy if exists "public read reviews" on reviews;
drop policy if exists "public insert reviews" on reviews;
create policy "public read reviews" on reviews for select using (status = 'approved');
