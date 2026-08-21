-- Product-line conflict context for company sales opportunities.
-- Safe to run repeatedly. These fields are used for conservative matching;
-- public UI only shows direct competitor details when competitor_info_public is true.

alter table opportunities add column if not exists direct_competitors text[] default '{}';
alter table opportunities add column if not exists competitor_categories text[] default '{}';
alter table opportunities add column if not exists competitor_info_public boolean default false;

alter table opportunities alter column direct_competitors set default '{}';
alter table opportunities alter column competitor_categories set default '{}';
alter table opportunities alter column competitor_info_public set default false;

create index if not exists opportunities_direct_competitors_gin_idx on opportunities using gin(direct_competitors);
create index if not exists opportunities_competitor_categories_gin_idx on opportunities using gin(competitor_categories);
