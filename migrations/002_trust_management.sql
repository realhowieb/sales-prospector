-- Rep management and sample-listing trust markers.

alter table reps add column if not exists edit_code_hash text;
alter table reps add column if not exists active boolean default true;
alter table reps add column if not exists is_sample boolean default false;

alter table reps enable row level security;
drop policy if exists "public read reps" on reps;
drop policy if exists "public insert reps" on reps;
create policy "public read reps" on reps for select using (true);

