#!/usr/bin/env python3
"""
finance_news.py — a terminal financial-news scraper.

Pulls headlines from a list of financial RSS/Atom feeds (see sources.py),
keeps only items published in the last 24 hours, de-duplicates syndicated
copies, and stores everything in a shared Supabase (Postgres) database.
Articles are ranked by recency.

No GUI — everything runs from the terminal. See README.md for one-time setup
(Supabase project + schema.sql + .env) and how to run it daily via GitHub
Actions so it stays current even when this machine is off.

Commands
--------
  scrape    Fetch every source, insert last-24h items into Supabase.
  top       Print the most recent stored articles (ranked newest-first).
  report    Build a printable digest of the day's news (HTML or text).
  stats     Summarise what's in the database.
  sources   List configured feeds (add --check to test connectivity).
  export    Dump the database to CSV or JSON.
  prune     Delete articles older than the retention window.

Examples
--------
  python finance_news.py scrape
  python finance_news.py top --limit 40
  python finance_news.py top --source CNBC --keyword "fed"
  python finance_news.py report --open
  python finance_news.py report --format txt
  python finance_news.py stats
  python finance_news.py export --format json --out news.json
  python finance_news.py sources --check

Requires SUPABASE_URL / SUPABASE_KEY — see .env.example.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import db

try:
    from zoneinfo import ZoneInfo
    _EASTERN = ZoneInfo("America/New_York")   # handles EST/EDT automatically
except Exception:
    _EASTERN = None                           # fall back to manual DST rules

import feedparser
import requests

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _console = Console()
    _RICH = True
except Exception:  # rich is optional; fall back to plain text
    _console = None
    _RICH = False

from sources import SOURCES

# ── Config ─────────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HOURS = 24
DEFAULT_WORKERS = 12
FETCH_TIMEOUT = 20  # seconds per feed
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FinanceNewsScraper/1.0 "
    "(+terminal RSS reader)"
)


# ── Helpers ────────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise_title(title: str) -> str:
    """Lower-case, strip punctuation & whitespace, and drop a trailing
    ' - Publisher' suffix (Google News appends it) so syndicated copies of the
    same headline collapse to one row."""
    t = title.strip()
    t = re.sub(r"\s+-\s+[^-]+$", "", t)          # drop " - CNBC" style suffix
    t = t.lower()
    t = re.sub(r"[^\w\s]", "", t)                # strip punctuation
    t = re.sub(r"\s+", " ", t).strip()
    return t


def content_hash(title: str) -> str:
    return hashlib.sha256(normalise_title(title).encode("utf-8")).hexdigest()


def entry_timestamp(entry) -> int | None:
    """Return a unix epoch for the entry's publish time, or None if unparseable.
    feedparser normalises *_parsed struct_times to UTC, so timegm is correct."""
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return calendar.timegm(st)
            except Exception:
                continue
    return None


def _eastern_is_dst(dt_utc: datetime) -> bool:
    """US Eastern DST: 2nd Sunday of March 07:00 UTC → 1st Sunday of Nov 06:00 UTC."""
    year = dt_utc.year
    march = datetime(year, 3, 8, tzinfo=timezone.utc)      # 2nd Sunday of March
    dst_start = (march + timedelta(days=(6 - march.weekday()) % 7)).replace(hour=7)
    nov = datetime(year, 11, 1, tzinfo=timezone.utc)       # 1st Sunday of November
    dst_end = (nov + timedelta(days=(6 - nov.weekday()) % 7)).replace(hour=6)
    return dst_start <= dt_utc < dst_end


def format_eastern(ts: int) -> str:
    """Format a UTC epoch as US Eastern wall-clock time, e.g. 'Jul 25, 09:53 AM EDT'."""
    dt_utc = datetime.fromtimestamp(ts, timezone.utc)
    if _EASTERN is not None:
        return dt_utc.astimezone(_EASTERN).strftime("%b %d, %I:%M %p %Z")
    dst = _eastern_is_dst(dt_utc)
    e = dt_utc.astimezone(timezone(timedelta(hours=-4 if dst else -5)))
    return e.strftime("%b %d, %I:%M %p ") + ("EDT" if dst else "EST")


def humanise_age(ts: int, ref: int | None = None) -> str:
    ref = ref if ref is not None else int(time.time())
    secs = max(0, ref - ts)
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        h, m = divmod(secs // 60, 60)
        return f"{h}h {m}m ago" if m else f"{h}h ago"
    return f"{secs // 86400}d ago"


def clean_summary(raw: str, limit: int = 500) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", "", raw)           # strip HTML tags
    text = html.unescape(text)                   # decode &nbsp; &amp; &#39; etc.
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


# ── Opinion / editorial filter ──────────────────────────────────────────────────
# Op-eds, columns and editorials are viewpoints, not reported facts — dropped by
# default (a title label like "Opinion:" / "COLUMN-", or an /opinion/ URL path).
_OPINION_TITLE_RE = re.compile(
    r"^\s*(?:[\[(]\s*)?"
    r"(opinion|op[-\s]?ed|editorial|commentary|viewpoint|column|columnist|"
    r"perspective|letters?\s+to\s+the\s+editor)"
    r"\s*(?:[\])]|[:|\-–—])",
    re.IGNORECASE,
)
_OPINION_SUFFIX_RE = re.compile(
    r"[|–—-]\s*(opinion|editorial|commentary|comment)\s*$",
    re.IGNORECASE,
)
_OPINION_URL_RE = re.compile(
    r"/(opinion|opinions|commentary|editorial|editorials|op-?ed|columns?|voices)"
    r"(?:/|$|\?|#|\.)",
    re.IGNORECASE,
)


def is_opinion(title: str, link: str = "") -> bool:
    """Heuristically detect opinion/editorial content by title label or URL path."""
    t = title or ""
    if _OPINION_TITLE_RE.search(t) or _OPINION_SUFFIX_RE.search(t):
        return True
    if link and _OPINION_URL_RE.search(link):
        return True
    return False


# ── Fetching ───────────────────────────────────────────────────────────────────
def fetch_feed(name: str, category: str, url: str):
    """Fetch and parse one feed. Returns (name, entries|None, error|None)."""
    try:
        resp = requests.get(
            url, timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        return name, category, parsed.entries, None
    except Exception as e:
        return name, category, None, f"{type(e).__name__}: {e}"


def scrape(hours: int, workers: int, quiet: bool, filter_opinion: bool = True) -> None:
    cutoff = int(time.time()) - hours * 3600
    fetched_at = iso(now_utc())

    log = (lambda *a, **k: None) if quiet else _log
    op = " (opinion pieces excluded)" if filter_opinion else ""
    log(f"Scraping {len(SOURCES)} feeds (keeping items from the last {hours}h){op}...")

    rows: list[dict] = []
    seen_hashes: set[str] = set()
    stats = {"ok": 0, "failed": 0, "entries": 0, "in_window": 0,
             "no_date": 0, "dup_in_batch": 0, "opinion": 0}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(fetch_feed, name, cat, url): name
            for name, cat, url in SOURCES
        }
        for fut in as_completed(futures):
            name, category, entries, err = fut.result()
            if err is not None:
                stats["failed"] += 1
                log(f"  [skip] {name}: {err}")
                continue
            stats["ok"] += 1
            n_win = 0
            for e in entries or []:
                stats["entries"] += 1
                title = (e.get("title") or "").strip()
                if not title:
                    continue
                link = e.get("link", "")
                if filter_opinion and is_opinion(title, link):
                    stats["opinion"] += 1
                    continue                      # op-ed / column / editorial
                ts = entry_timestamp(e)
                if ts is None:
                    stats["no_date"] += 1
                    continue                      # can't prove it's <24h -> drop
                if ts < cutoff:
                    continue                      # older than the window
                stats["in_window"] += 1
                n_win += 1

                h = content_hash(title)
                if h in seen_hashes:
                    stats["dup_in_batch"] += 1
                    continue
                seen_hashes.add(h)

                publisher = None
                src = e.get("source")
                if isinstance(src, dict):
                    publisher = src.get("title")

                rows.append({
                    "title": title,
                    "summary": clean_summary(e.get("summary", "")),
                    "link": link,
                    "source": name,
                    "publisher": publisher,
                    "category": category,
                    "published_at": iso(datetime.fromtimestamp(ts, timezone.utc)),
                    "published_ts": ts,
                    "fetched_at": fetched_at,
                    "content_hash": h,
                })
            log(f"  [ok]   {name}: {n_win} in-window / {len(entries or [])} total")

    # Dedup against previously-stored rows happens in the DB itself (unique
    # constraint on content_hash + ON CONFLICT DO NOTHING) — inserted below is
    # exactly the count of genuinely new rows.
    inserted = db.upsert_articles(rows)

    log("")
    _log(
        f"Done. feeds ok={stats['ok']} failed={stats['failed']} | "
        f"entries seen={stats['entries']} in-window={stats['in_window']} | "
        f"new inserted={inserted} "
        f"(batch dups={stats['dup_in_batch']}, "
        f"already stored={stats['in_window'] - stats['dup_in_batch'] - inserted}, "
        f"undated dropped={stats['no_date']}, "
        f"opinion filtered={stats['opinion']})"
    )


# ── Queries / output ───────────────────────────────────────────────────────────
def show_top(hours, source, keyword, category, limit, show_links):
    rows = db.query_articles(hours=hours, source=source, keyword=keyword,
                             category=category, limit=limit)
    if not rows:
        _log("No matching articles. Run `scrape` first, or widen the filters.")
        return

    ref = int(time.time())
    if _RICH:
        title = f"Financial news — last {hours}h, newest first ({len(rows)} shown)"
        table = Table(title=title, box=box.SIMPLE_HEAVY, expand=True,
                      header_style="bold")
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Published (ET)", width=21, style="yellow")
        table.add_column("Age", width=9, style="cyan")
        table.add_column("Source", width=20, style="green")
        table.add_column("Headline", ratio=1, no_wrap=not show_links,
                         overflow="ellipsis")
        for i, r in enumerate(rows, 1):
            headline = r["title"]
            if show_links and r["link"]:
                headline = f"{headline}\n[dim blue]{r['link']}[/dim blue]"
            table.add_row(str(i), format_eastern(r["published_ts"]),
                          humanise_age(r["published_ts"], ref),
                          r["source"], headline)
        _console.print(table)
    else:
        print(f"Financial news — last {hours}h, newest first ({len(rows)} shown)\n")
        for i, r in enumerate(rows, 1):
            print(f"{i:>3}. {format_eastern(r['published_ts']):<21} "
                  f"[{humanise_age(r['published_ts'], ref):>9}] "
                  f"({r['source']}) {r['title']}")
            if show_links and r["link"]:
                print(f"      {r['link']}")


def show_stats():
    total = db.count_total()
    last24 = db.count_since(24)
    per_source = db.source_counts()
    lo, hi = db.published_span()

    _log(f"Supabase project: {db.SUPABASE_URL}")
    _log(f"Total articles stored : {total}")
    _log(f"From the last 24h     : {last24}")
    if lo is not None:
        _log(f"Oldest published      : {iso(datetime.fromtimestamp(lo, timezone.utc))}")
        _log(f"Newest published      : {iso(datetime.fromtimestamp(hi, timezone.utc))}")
    if per_source:
        _log("\nBy source:")
        for r in per_source:
            _log(f"  {r['n']:>4}  {r['source']}")


def check_sources(do_check):
    if not do_check:
        _log(f"{len(SOURCES)} configured feeds:\n")
        for name, cat, url in SOURCES:
            _log(f"  [{cat:<9}] {name:<28} {url}")
        _log("\nRun with --check to test connectivity.")
        return

    _log(f"Testing {len(SOURCES)} feeds...\n")
    ok = 0
    with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as ex:
        futures = {ex.submit(fetch_feed, n, c, u): n for n, c, u in SOURCES}
        results = []
        for fut in as_completed(futures):
            name, cat, entries, err = fut.result()
            results.append((name, entries, err))
    for name, entries, err in sorted(results, key=lambda x: x[0]):
        if err:
            _log(f"  FAIL  {name:<28} {err}")
        else:
            ok += 1
            _log(f"  OK    {name:<28} {len(entries)} entries")
    _log(f"\n{ok}/{len(SOURCES)} feeds reachable.")


def export_db(fmt, out):
    rows = db.export_columns()
    if fmt == "json":
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    else:  # csv
        with open(out, "w", encoding="utf-8", newline="") as f:
            if rows:
                w = csv.DictWriter(f, fieldnames=rows[0].keys())
                w.writeheader()
                for r in rows:
                    w.writerow(r)
    _log(f"Exported {len(rows)} articles to {out}")


def prune(hours):
    deleted = db.prune(hours)
    _log(f"Deleted {deleted} articles older than {hours}h.")


# ── Printable report ────────────────────────────────────────────────────────────
CATEGORY_ORDER = ["central-bank", "data", "economy", "markets", "wire",
                  "global", "business"]
CATEGORY_LABELS = {
    "central-bank": "Central Banks & Policy",
    "data": "Official Data Releases",
    "economy": "Economy",
    "markets": "Markets",
    "wire": "Wires & Top-Tier Press",
    "global": "Global / Non-US",
    "business": "Business",
}


def _grouped_by_category(rows):
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["category"] or "other", []).append(r)
    ordered = [c for c in CATEGORY_ORDER if c in groups]
    ordered += sorted(c for c in groups if c not in CATEGORY_ORDER)
    return [(c, groups[c]) for c in ordered]


def generate_report(hours, source, keyword, category, fmt, out, open_after):
    """Build a printable digest of all matching articles, grouped by category."""
    rows = db.query_articles(hours=hours, source=source, keyword=keyword,
                             category=category, limit=None)
    if not rows:
        _log("No matching articles to report. Run `scrape` first, or widen filters.")
        return

    now_e = datetime.now(_EASTERN) if _EASTERN else now_utc()
    generated = format_eastern(int(time.time()))
    date_str = now_e.strftime("%A, %B %d, %Y")
    groups = _grouped_by_category(rows)

    if not out:
        out = os.path.join(HERE, f"news_report_{now_e.strftime('%Y-%m-%d')}.{fmt}")

    if fmt == "html":
        _write_html_report(out, rows, groups, date_str, generated, hours)
    else:
        _write_txt_report(out, rows, groups, date_str, generated, hours)

    _log(f"Report written: {out}")
    _log(f"  {len(rows)} stories across {len(groups)} categories (last {hours}h).")
    if fmt == "html":
        _log("  Open it in a browser and press Ctrl+P to print.")
    if open_after:
        path = os.path.abspath(out)
        try:
            os.startfile(path)                 # Windows
        except AttributeError:
            webbrowser.open(f"file://{path}")   # macOS / Linux


def _write_html_report(out, rows, groups, date_str, generated, hours):
    ref = int(time.time())
    p = [f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Financial News Digest — {html.escape(date_str)}</title>
<style>
  @page {{ margin: 1.6cm; }}
  html, body {{ background:#fff; }}
  body {{ font-family: Georgia, 'Times New Roman', serif; color:#111;
         max-width: 860px; margin: 0 auto; padding: 24px; line-height: 1.45; }}
  h1, h2 {{ color:#111; }}
  header {{ border-bottom: 3px solid #111; padding-bottom: 10px; margin-bottom: 4px; }}
  h1 {{ font-size: 1.7rem; margin: 0 0 4px; }}
  .sub {{ color:#555; font-size: .85rem; }}
  h2 {{ font-size: 1.02rem; text-transform: uppercase; letter-spacing: .04em;
        border-bottom: 1px solid #999; padding-bottom: 3px; margin: 26px 0 10px;
        page-break-after: avoid; }}
  .item {{ margin: 9px 0; padding-left: 12px; border-left: 3px solid #ddd;
           page-break-inside: avoid; }}
  .meta {{ font-size: .74rem; color:#666; margin-bottom: 1px; }}
  .src {{ font-weight: bold; color:#333; }}
  .headline {{ font-size: .98rem; }}
  .headline a {{ color:#0645ad; text-decoration: none; }}
  .summary {{ font-size: .82rem; color:#444; margin-top: 2px; }}
  footer {{ margin-top: 30px; border-top: 1px solid #ccc; padding-top: 8px;
            font-size: .74rem; color:#888; }}
  @media print {{ body {{ padding: 0; }} .headline a {{ color:#000; }} }}
</style></head><body>
<header>
  <h1>Financial News Digest</h1>
  <div class="sub">{html.escape(date_str)} &middot; last {hours}h &middot;
       {len(rows)} stories &middot; generated {html.escape(generated)}</div>
</header>"""]
    for cat, items in groups:
        label = CATEGORY_LABELS.get(cat, cat.replace("-", " ").title())
        p.append(f'<h2>{html.escape(label)} '
                 f'<span style="color:#999;font-weight:normal">({len(items)})</span></h2>')
        for r in items:
            t = html.escape(format_eastern(r["published_ts"]))
            age = humanise_age(r["published_ts"], ref)
            src = html.escape(r["source"] or "")
            pub = f' &middot; {html.escape(r["publisher"])}' if r["publisher"] else ""
            title = html.escape(r["title"] or "")
            link = html.escape(r["link"] or "", quote=True)
            headline = f'<a href="{link}">{title}</a>' if link else title
            summary = html.escape(r["summary"] or "")
            summ = f'<div class="summary">{summary}</div>' if summary else ""
            p.append(
                f'<div class="item"><div class="meta">'
                f'<span class="src">{src}</span>{pub} &middot; {t} ({age})</div>'
                f'<div class="headline">{headline}</div>{summ}</div>'
            )
    p.append('<footer>Generated by finance_news.py &middot; headlines are '
             'informational only, not investment advice.</footer>')
    p.append("</body></html>")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(p))


def _write_txt_report(out, rows, groups, date_str, generated, hours):
    ref = int(time.time())
    L = ["FINANCIAL NEWS DIGEST", "=" * 21,
         f"{date_str}  |  last {hours}h  |  {len(rows)} stories  |  generated {generated}",
         ""]
    for cat, items in groups:
        label = CATEGORY_LABELS.get(cat, cat.replace("-", " ").title())
        head = f"{label.upper()} ({len(items)})"
        L += [head, "-" * len(head)]
        for r in items:
            t = format_eastern(r["published_ts"])
            age = humanise_age(r["published_ts"], ref)
            pub = f' / {r["publisher"]}' if r["publisher"] else ""
            L.append(f"[{t}] ({age})  {r['source']}{pub}")
            L.append(f"    {r['title']}")
            if r["summary"]:
                L.append(f"    {r['summary']}")
            if r["link"]:
                L.append(f"    {r['link']}")
            L.append("")
        L.append("")
    L.append("-- Not investment advice. Generated by finance_news.py --")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# ── plain logger (rich if available) ────────────────────────────────────────────
def _log(*args):
    msg = " ".join(str(a) for a in args)
    if _RICH:
        _console.print(msg, highlight=False)
    else:
        print(msg)


# ── CLI ────────────────────────────────────────────────────────────────────────
def main(argv=None):
    p = argparse.ArgumentParser(
        prog="finance_news.py",
        description="Terminal financial-news scraper (RSS -> Supabase, last 24h, "
                    "ranked by recency).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scrape", help="Fetch feeds and store last-24h items.")
    sp.add_argument("--hours", type=int, default=DEFAULT_HOURS,
                    help=f"Recency window in hours (default: {DEFAULT_HOURS}).")
    sp.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                    help=f"Parallel fetch workers (default: {DEFAULT_WORKERS}).")
    sp.add_argument("--quiet", action="store_true",
                    help="Only print the final summary line.")
    sp.add_argument("--keep-opinion", action="store_true", dest="keep_opinion",
                    help="Include opinion/editorial pieces (excluded by default).")

    tp = sub.add_parser("top", help="Show most recent stored articles.")
    tp.add_argument("--limit", type=int, default=30, help="Max rows (default 30).")
    tp.add_argument("--hours", type=int, default=DEFAULT_HOURS,
                    help=f"Only show items newer than N hours (default {DEFAULT_HOURS}).")
    tp.add_argument("--source", help="Filter by source/publisher substring.")
    tp.add_argument("--category", help="Filter by category (markets, economy, ...).")
    tp.add_argument("--keyword", help="Filter title/summary by keyword.")
    tp.add_argument("--links", action="store_true", help="Show article URLs.")

    sub.add_parser("stats", help="Summarise the database.")

    cs = sub.add_parser("sources", help="List configured feeds.")
    cs.add_argument("--check", action="store_true",
                    help="Test each feed's connectivity.")

    ep = sub.add_parser("export", help="Dump the database to CSV/JSON.")
    ep.add_argument("--format", choices=["csv", "json"], default="json")
    ep.add_argument("--out", help="Output path (default: news_export.<fmt>).")

    pp = sub.add_parser("prune", help="Delete articles older than the window.")
    pp.add_argument("--hours", type=int, default=DEFAULT_HOURS,
                    help=f"Keep only items newer than N hours (default {DEFAULT_HOURS}).")

    rp = sub.add_parser("report",
                        help="Build a printable digest of the day's news (HTML/text).")
    rp.add_argument("--format", choices=["html", "txt"], default="html",
                    help="Output format (default: html — open in a browser to print).")
    rp.add_argument("--out", help="Output path (default: news_report_<date>.<fmt>).")
    rp.add_argument("--hours", type=int, default=DEFAULT_HOURS,
                    help=f"Window in hours (default {DEFAULT_HOURS}).")
    rp.add_argument("--source", help="Filter by source/publisher substring.")
    rp.add_argument("--category", help="Filter by category (central-bank, data, ...).")
    rp.add_argument("--keyword", help="Filter title/summary by keyword.")
    rp.add_argument("--open", action="store_true", dest="open_after",
                    help="Open the report when done (browser for HTML).")

    args = p.parse_args(argv)

    # sources listing needs no DB
    if args.cmd == "sources":
        check_sources(args.check)
        return

    if args.cmd == "scrape":
        scrape(args.hours, args.workers, args.quiet,
               filter_opinion=not args.keep_opinion)
    elif args.cmd == "top":
        show_top(args.hours, args.source, args.keyword,
                 args.category, args.limit, args.links)
    elif args.cmd == "stats":
        show_stats()
    elif args.cmd == "export":
        out = args.out or f"news_export.{args.format}"
        export_db(args.format, out)
    elif args.cmd == "report":
        generate_report(args.hours, args.source, args.keyword,
                        args.category, args.format, args.out, args.open_after)
    elif args.cmd == "prune":
        prune(args.hours)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
