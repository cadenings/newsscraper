"""
db.py — Supabase-backed storage layer for finance_news.py.

Reads SUPABASE_URL / SUPABASE_KEY from the environment (loaded from a local
.env via python-dotenv if present, or from real env vars — e.g. GitHub Actions
secrets in CI).

Use the project's *service_role* key here, not the anon key. This is a
trusted backend script (your own terminal / a CI job), not a browser app —
service_role bypasses Row Level Security, which is what lets scrape/prune work
without extra RLS policies. Never expose this key publicly, in client-side
code, or commit it to git (.env is gitignored; see .env.example).

Table + indexes + the source_counts() RPC function live in schema.sql — run
that once in the Supabase SQL editor before using this module.
"""
from __future__ import annotations

import os
import re
import time

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()  # no-op in CI, where secrets already come from real env vars

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

PAGE_SIZE = 1000      # PostgREST's default max rows per response — paginate past it
UPSERT_CHUNK = 500    # rows per insert request, keeps request bodies modest

_client: Client | None = None


def get_client() -> Client:
    """Lazily create (and cache) the Supabase client. Fails fast with a clear
    message if credentials are missing, rather than a confusing traceback."""
    global _client
    if _client is not None:
        return _client
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit(
            "Missing Supabase credentials.\n"
            "  Copy .env.example to .env and fill in SUPABASE_URL and "
            "SUPABASE_KEY\n"
            "  (find them in your Supabase project: Settings -> API -> "
            "service_role key)."
        )
    _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


_OR_ESCAPE_RE = re.compile(r"([,.():\\])")


def _or_escape(value: str) -> str:
    """Escape characters that are special inside a PostgREST or_() filter
    string, so a source/keyword filter containing e.g. a comma or parens
    doesn't corrupt the query."""
    return _OR_ESCAPE_RE.sub(r"\\\1", value)


def _filtered_query(hours, source, keyword, category):
    """Build a fresh, ordered, filtered select query (no limit applied yet)."""
    q = get_client().table("articles").select("*")
    if hours:
        cutoff = int(time.time()) - hours * 3600
        q = q.gte("published_ts", cutoff)
    if source:
        s = _or_escape(source)
        q = q.or_(f"source.ilike.%{s}%,publisher.ilike.%{s}%")
    if category:
        q = q.eq("category", category)
    if keyword:
        k = _or_escape(keyword)
        q = q.or_(f"title.ilike.%{k}%,summary.ilike.%{k}%")
    return q.order("published_ts", desc=True)


def query_articles(hours=None, source=None, keyword=None, category=None,
                   limit=None) -> list[dict]:
    """Matching articles as a list of dicts, newest first.
    limit=None fetches every match, paginating past PostgREST's row cap."""
    if limit:
        return (_filtered_query(hours, source, keyword, category)
                .limit(limit).execute().data)
    out: list[dict] = []
    start = 0
    while True:
        batch = (_filtered_query(hours, source, keyword, category)
                 .range(start, start + PAGE_SIZE - 1).execute().data)
        out.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return out


def upsert_articles(rows: list[dict]) -> int:
    """Insert rows, silently skipping any whose content_hash already exists
    (DB-level dedup via the unique constraint + ON CONFLICT DO NOTHING).
    Returns the number of rows actually newly inserted."""
    if not rows:
        return 0
    sb = get_client()
    inserted = 0
    for i in range(0, len(rows), UPSERT_CHUNK):
        chunk = rows[i:i + UPSERT_CHUNK]
        resp = (sb.table("articles")
                  .upsert(chunk, on_conflict="content_hash", ignore_duplicates=True)
                  .execute())
        inserted += len(resp.data or [])
    return inserted


def count_total() -> int:
    resp = get_client().table("articles").select("*", count="exact", head=True).execute()
    return resp.count or 0


def count_since(hours: int) -> int:
    cutoff = int(time.time()) - hours * 3600
    resp = (get_client().table("articles").select("*", count="exact", head=True)
            .gte("published_ts", cutoff).execute())
    return resp.count or 0


def source_counts() -> list[dict]:
    """[{'source': ..., 'n': ...}, ...], via the source_counts() SQL function
    in schema.sql (PostgREST can't express GROUP BY directly)."""
    return get_client().rpc("source_counts").execute().data or []


def published_span() -> tuple[int | None, int | None]:
    """(oldest published_ts, newest published_ts), or (None, None) if empty."""
    sb = get_client()
    oldest = (sb.table("articles").select("published_ts")
              .order("published_ts").limit(1).execute().data)
    newest = (sb.table("articles").select("published_ts")
              .order("published_ts", desc=True).limit(1).execute().data)
    lo = oldest[0]["published_ts"] if oldest else None
    hi = newest[0]["published_ts"] if newest else None
    return lo, hi


def export_columns() -> list[dict]:
    """All articles, newest first, restricted to the columns export_db() writes."""
    cols = "title,summary,link,source,publisher,category,published_at,fetched_at"
    out: list[dict] = []
    start = 0
    while True:
        batch = (get_client().table("articles").select(cols)
                 .order("published_ts", desc=True)
                 .range(start, start + PAGE_SIZE - 1).execute().data)
        out.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return out


def prune(hours: int) -> int:
    """Delete articles older than `hours`. Returns the number deleted."""
    cutoff = int(time.time()) - hours * 3600
    resp = get_client().table("articles").delete().lt("published_ts", cutoff).execute()
    return len(resp.data or [])
