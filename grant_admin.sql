-- ------------------------------------------------------------------------- --
-- Grant admin access (Path B: role=admin + admin_account_roles row).
--
-- Admin = account_profiles.role = 'admin'  AND  a row in admin_account_roles.
-- Public sign-up cannot self-assign admin, so promote your account here.
--
-- Prereqs:
--   1. The app must be in LIVE mode ([supabase] url/key/service_key set) —
--      the 🛡️ Admin Dashboard only appears when service_key is configured.
--   2. Sign up / sign in once in the app with the email below, so a
--      Supabase Auth user (and account_profiles row) exists for it.
--
-- Then: Supabase → SQL Editor → paste → set your email → Run.
-- Finally: sign OUT and back IN in the app (role is loaded at login).
-- ------------------------------------------------------------------------- --

-- 👇 change this to the email you signed up with
\set admin_email 'you@example.com'

-- 1) Promote the account_profiles row to the admin role (create it if missing).
insert into account_profiles (user_id, email, role)
select id, :'admin_email', 'admin'
from auth.users
where email = :'admin_email'
on conflict (user_id) do update set role = 'admin';

-- 2) Grant admin verification (what refresh_admin_verified() checks on login).
insert into admin_account_roles (user_id, granted_by)
select id, 'manual'
from auth.users
where email = :'admin_email'
on conflict (user_id) do nothing;

-- 3) Verify it took:
select p.email, p.role, (a.user_id is not null) as admin_verified
from account_profiles p
left join admin_account_roles a on a.user_id = p.user_id
where p.email = :'admin_email';
