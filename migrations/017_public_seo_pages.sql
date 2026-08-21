-- Public SEO page support. Adds stable opportunity slugs without creating
-- synthetic SEO content.

alter table opportunities add column if not exists slug text;

update opportunities
set slug = lower(regexp_replace(regexp_replace(coalesce(title, 'opportunity') || '-' || id::text, '[^a-zA-Z0-9]+', '-', 'g'), '(^-|-$)', '', 'g'))
where slug is null or slug = '';

create unique index if not exists opportunities_slug_unique_idx on opportunities(slug) where slug is not null;
