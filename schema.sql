-- schema.sql — run this once in your Supabase project's SQL editor
-- (Dashboard -> SQL Editor -> New query -> paste this whole file -> Run).
--
-- Creates the articles table the scraper reads/writes, plus a small RPC
-- function used by `finance_news.py stats` (PostgREST can't express a plain
-- GROUP BY, so the grouped source counts go through this function instead).

create table if not exists articles (
    id            bigint generated always as identity primary key,
    title         text        not null,
    summary       text,
    link          text,
    source        text        not null,          -- feed label from sources.py
    publisher     text,                           -- underlying outlet (e.g. via Google News)
    category      text,
    published_at  timestamptz not null,           -- when the article was published
    published_ts  bigint      not null,           -- unix epoch (recency ranking / filtering)
    fetched_at    timestamptz not null default now(),
    content_hash  text        not null unique     -- sha256(normalised title) -> dedup
);

create index if not exists idx_published_ts on articles (published_ts desc);
create index if not exists idx_source       on articles (source);
create index if not exists idx_category     on articles (category);

-- Grouped counts per source, for `finance_news.py stats`.
create or replace function source_counts()
returns table (source text, n bigint)
language sql
stable
as $$
    select source, count(*) as n
    from articles
    group by source
    order by n desc;
$$;

-- Lock the table down by default. The scraper connects with the service_role
-- key, which bypasses RLS entirely, so this doesn't affect the tool — it just
-- means the table stays private if the anon/public key is ever used against
-- it (e.g. if you later build a public-facing app on the same project).
alter table articles enable row level security;

-- The web dashboard (docs/index.html) reads with the anon/publishable key
-- directly from the browser, so it needs an explicit read-only grant. Writes
-- (insert/update/delete) are still impossible for anon — only service_role
-- (used by the scraper, never exposed to a browser) can write.
create policy "public read access" on articles
    for select
    to anon
    using (true);
