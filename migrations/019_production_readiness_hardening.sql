-- 019_production_readiness_hardening.sql
-- Tighten public marketplace reads without destroying existing data.

-- Public visitors should only browse active rep profiles.
drop policy if exists "public read reps" on reps;
create policy "public read reps" on reps
  for select using (active is true and coalesce(profile_status, 'active') = 'active');

-- Keep common public filters indexable as the marketplace grows.
create index if not exists reps_active_profile_status_idx on reps(active, profile_status);
create index if not exists companies_profile_status_featured_idx on companies(profile_status, featured);
create index if not exists opportunities_active_featured_idx on opportunities(active, featured);

-- Defense in depth: these columns are for ownership, administration, or private
-- contact workflows and should not be readable through the public anon API.
revoke select (email, phone, edit_code_hash, claim_email, owner_user_id)
  on reps from anon, authenticated;
revoke select (contact_name, contact_email, edit_code_hash, owner_user_id)
  on companies from anon, authenticated;
revoke select (direct_competitors, competitor_categories, owner_user_id)
  on opportunities from anon, authenticated;
