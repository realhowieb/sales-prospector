-- Availability and opportunity preferences for rep profiles.
-- Safe to run more than once. Preserves the legacy open_to_new_lines boolean.

alter table reps add column if not exists availability_status text;
alter table reps add column if not exists preferred_categories text[] default '{}';
alter table reps add column if not exists preferred_company_types text[] default '{}';
alter table reps add column if not exists preferred_compensation text[] default '{}';
alter table reps add column if not exists minimum_commission numeric(10,2);
alter table reps add column if not exists notes_for_companies text;

alter table reps alter column preferred_categories set default '{}';
alter table reps alter column preferred_company_types set default '{}';
alter table reps alter column preferred_compensation set default '{}';

update reps
set availability_status = case
  when open_to_new_lines is false then 'not_open'
  else 'open'
end
where availability_status is null;

alter table reps alter column availability_status set default 'open';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'reps_availability_status_check') then
    alter table reps add constraint reps_availability_status_check
      check (availability_status is null or availability_status in ('open', 'selectively_open', 'not_open'));
  end if;
end;
$$;

create index if not exists reps_availability_status_idx on reps(availability_status);
create index if not exists reps_minimum_commission_idx on reps(minimum_commission);
create index if not exists reps_preferred_categories_gin_idx on reps using gin(preferred_categories);
create index if not exists reps_preferred_company_types_gin_idx on reps using gin(preferred_company_types);
create index if not exists reps_preferred_compensation_gin_idx on reps using gin(preferred_compensation);
