"""
RSS/Atom feed sources for the financial news scraper.

Each entry: (name, category, url)
  - name:     short label stored on every article (the outlet / feed)
  - category: coarse bucket, handy for filtering (see categories below)
  - url:      RSS or Atom feed URL

The list is curated toward REPUTABLE, MACRO-INDICATIVE sources — the outlets and
official bodies whose reporting actually moves macroeconomic sentiment:

  central-bank : the policy movers — Fed, ECB, BoE, BoJ, IMF, BIS
  data         : official statistics — BLS (jobs/CPI), BEA (GDP/PCE), EIA, Treasury
  wire         : top-tier financial press — WSJ, FT, Reuters/AP (via Google News),
                 CNBC, MarketWatch, The Economist, Bloomberg-adjacent
  economy      : macro / economics desks
  markets      : market & trading coverage
  business     : general business desks
  global       : non-US macro (Europe, Asia)

Dead or unreachable feeds are skipped gracefully at scrape time, so an occasional
broken feed never stops a run. Test connectivity any time with:

    python finance_news.py sources --check

Reuters and AP no longer publish public RSS, so their macro coverage is pulled
via targeted Google News searches (`when:1d` = last 24h) that aggregate the wires
and other reputable outlets around specific macro topics.
"""

SOURCES = [
    # ── Central banks & multilaterals (highest signal-per-headline) ────────────
    ("Federal Reserve",   "central-bank",
     "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB Press",         "central-bank",
     "https://www.ecb.europa.eu/rss/press.xml"),
    ("Bank of England",   "central-bank",
     "https://www.bankofengland.co.uk/rss/news"),
    ("Bank of Japan",     "central-bank",
     "https://www.boj.or.jp/en/rss/whatsnew.xml"),
    # IMF (403 bot-block) and BIS (feed returns empty) disabled — Fed/ECB/BoE/BoJ
    # already cover central-bank signal. Re-enable if they start responding.
    # ("IMF News", "central-bank", "https://www.imf.org/en/News/rss"),
    # ("BIS",      "central-bank", "https://www.bis.org/list/press_releases/rss.xml"),

    # ── Official statistics (the data releases that move markets) ──────────────
    ("BLS (US labor/CPI)", "data",
     "https://www.bls.gov/feed/bls_latest.rss"),
    ("BEA (US GDP/PCE)",   "data",
     "https://apps.bea.gov/rss/rss.xml"),
    # US Treasury direct RSS is 404 (no public feed found) — fiscal/debt coverage
    # comes via WSJ + the wire searches below.
    # ("US Treasury Press", "data", "https://home.treasury.gov/rss/press.xml"),
    ("EIA Today in Energy", "data",
     "https://www.eia.gov/rss/todayinenergy.xml"),

    # ── Top-tier financial press (RSS) ─────────────────────────────────────────
    # WSJ's public feeds.a.dj.com feeds are frozen (stuck at Jan 2025) — disabled.
    # WSJ headlines still arrive via the Google News wire searches below.
    # ("WSJ Markets",  "wire", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    # ("WSJ World",    "wire", "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
    # ("WSJ Business", "wire", "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
    ("FT Home",       "wire",
     "https://www.ft.com/rss/home"),
    ("The Economist: Finance & Economics", "wire",
     "https://www.economist.com/finance-and-economics/rss.xml"),
    ("CNBC Top News", "wire",
     "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC Economy",  "economy",
     "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC Finance",  "markets",
     "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("MarketWatch Top Stories", "markets",
     "http://feeds.marketwatch.com/marketwatch/topstories/"),
    ("MarketWatch Market Pulse", "markets",
     "http://feeds.marketwatch.com/marketwatch/marketpulse/"),
    ("Yahoo Finance", "markets",
     "https://finance.yahoo.com/news/rssindex"),

    # ── Reputable general desks (macro-relevant coverage) ──────────────────────
    ("NYT Economy",           "economy",
     "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml"),
    ("NYT Business",          "business",
     "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("The Guardian Business", "business",
     "https://www.theguardian.com/uk/business/rss"),
    ("BBC Business",          "business",
     "https://feeds.bbci.co.uk/news/business/rss.xml"),

    # ── Non-US macro (global sentiment) ────────────────────────────────────────
    # Nikkei Asia disabled — its feed publishes no timestamps, so items can't be
    # verified as within 24h. Asia macro is covered by CNA + the Asia wire search.
    # ("Nikkei Asia", "global", "https://asia.nikkei.com/rss/feed/nar"),
    ("CNA Business",  "global",
     "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936"),

    # ── Wire coverage via Google News (Reuters/AP + others, last 24h) ──────────
    #   Targeted at specific macro drivers, not generic "stock market" noise.
    ("Wires: Fed & rates",   "wire",
     "https://news.google.com/rss/search?q=%28Federal+Reserve+OR+FOMC+OR+%22interest+rates%22%29+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Wires: Inflation",     "economy",
     "https://news.google.com/rss/search?q=%28inflation+OR+CPI+OR+PCE%29+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Wires: Jobs & growth", "economy",
     "https://news.google.com/rss/search?q=%28%22jobs+report%22+OR+payrolls+OR+GDP+OR+recession%29+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Wires: Bonds & yields", "markets",
     "https://news.google.com/rss/search?q=%28%22Treasury+yields%22+OR+%22bond+market%22%29+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Wires: Trade & tariffs", "economy",
     "https://news.google.com/rss/search?q=%28tariffs+OR+%22trade+war%22+OR+sanctions%29+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Wires: Oil & energy",  "markets",
     "https://news.google.com/rss/search?q=%28%22oil+prices%22+OR+OPEC%29+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Wires: Asia & China economy", "global",
     "https://news.google.com/rss/search?q=%28%22China+economy%22+OR+%22Japan+economy%22+OR+%22Asia+markets%22%29+when:1d&hl=en-US&gl=US&ceid=US:en"),
]
