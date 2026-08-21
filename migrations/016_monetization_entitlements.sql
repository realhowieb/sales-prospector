-- Monetization architecture prep. This does not integrate payment processing.
-- Stripe identifiers are placeholders for a future server-side integration.

alter table account_profiles add column if not exists subscription_plan text;
alter table account_profiles add column if not exists subscription_status text default 'free';
alter table account_profiles add column if not exists entitlement_overrides jsonb default '{}'::jsonb;
alter table account_profiles add column if not exists stripe_customer_id text;
alter table account_profiles add column if not exists stripe_subscription_id text;
alter table account_profiles add column if not exists subscription_current_period_end timestamptz;

alter table account_profiles alter column subscription_status set default 'free';
alter table account_profiles alter column entitlement_overrides set default '{}'::jsonb;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'account_profiles_subscription_plan_check') then
    alter table account_profiles add constraint account_profiles_subscription_plan_check
      check (
        subscription_plan is null
        or subscription_plan in ('rep_free', 'rep_pro', 'company_free', 'company_pro', 'admin')
      );
  end if;
  if not exists (select 1 from pg_constraint where conname = 'account_profiles_subscription_status_check') then
    alter table account_profiles add constraint account_profiles_subscription_status_check
      check (
        subscription_status is null
        or subscription_status in ('free', 'trialing', 'active', 'past_due', 'canceled', 'incomplete')
      );
  end if;
end;
$$;

create index if not exists account_profiles_subscription_plan_idx on account_profiles(subscription_plan);
create index if not exists account_profiles_subscription_status_idx on account_profiles(subscription_status);
create index if not exists account_profiles_stripe_customer_id_idx on account_profiles(stripe_customer_id);

revoke update (
  subscription_plan,
  subscription_status,
  entitlement_overrides,
  stripe_customer_id,
  stripe_subscription_id,
  subscription_current_period_end
) on account_profiles from anon, authenticated;
