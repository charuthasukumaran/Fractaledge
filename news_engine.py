"""
News Engine — Aggregates stock and market news from free sources.
----------------------------------------------------------------
Uses yfinance for stock-specific news and Google News RSS for
general stock market headlines. No API key required.

Categories: latest, stocks, crypto, commodities, economy, global
"""
import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

# ── In-memory cache ─────────────────────────────────────────────
_cache = {}
CACHE_TTL = 300  # 5 minutes

# ── Google News RSS feed URLs by category ─────────────────────
CATEGORY_RSS_FEEDS = {
    "stocks": [
        "https://news.google.com/rss/search?q=stock+market+India+NSE+BSE&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=NIFTY+sensex+share+market&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=stock+market+today+trading&hl=en&gl=US&ceid=US:en",
    ],
    "crypto": [
        "https://news.google.com/rss/search?q=cryptocurrency+bitcoin+ethereum&hl=en&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=crypto+market+blockchain+altcoin&hl=en&gl=US&ceid=US:en",
    ],
    "commodities": [
        "https://news.google.com/rss/search?q=gold+silver+crude+oil+commodity+market&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=commodity+prices+metals+energy&hl=en&gl=US&ceid=US:en",
    ],
    "economy": [
        "https://news.google.com/rss/search?q=RBI+economy+India+GDP+inflation&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Federal+Reserve+economy+interest+rate&hl=en&gl=US&ceid=US:en",
    ],
    "global": [
        "https://news.google.com/rss/search?q=global+markets+world+economy+trade&hl=en&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=asia+europe+market+financial+news&hl=en&gl=US&ceid=US:en",
    ],
}

# Backward compat: flat list for fetch_market_news()
MARKET_RSS_FEEDS = CATEGORY_RSS_FEEDS["stocks"]


def _parse_rfc2822(date_str: str) -> int:
    """Parse RFC 2822 date string to unix timestamp."""
    try:
        dt = parsedate_to_datetime(date_str)
        return int(dt.timestamp())
    except Exception:
        return 0


def _fetch_rss(rss_url: str, category: str) -> list:
    """Fetch and parse a single RSS feed URL."""
    articles = []
    try:
        req = urllib.request.Request(rss_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=8) as response:
            xml_data = response.read()

        root = ET.fromstring(xml_data)
        channel = root.find("channel")
        if channel is None:
            return articles

        for item in channel.findall("item"):
            title = item.findtext("title", "").strip()
            if not title:
                continue

            pub_date_str = item.findtext("pubDate", "")
            pub_timestamp = _parse_rfc2822(pub_date_str)

            source = "Google News"
            if " - " in title:
                parts = title.rsplit(" - ", 1)
                if len(parts) == 2:
                    title = parts[0].strip()
                    source = parts[1].strip()

            link = item.findtext("link", "")

            articles.append({
                "title": title,
                "link": link,
                "source": source,
                "published": pub_timestamp,
                "published_iso": datetime.fromtimestamp(
                    pub_timestamp, tz=timezone.utc
                ).isoformat() if pub_timestamp else None,
                "type": "market",
                "category": category,
                "symbol": None,
                "thumbnail": None,
                "related_tickers": [],
            })

    except Exception as e:
        logger.error(f"Failed to fetch RSS ({category}): {e}")

    return articles


def fetch_stock_news(symbol: str) -> list:
    """Fetch news articles for a specific stock using yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        raw_news = ticker.news

        if raw_news is None:
            return []

        # Handle both old format (list) and new format (dict with 'news' key)
        if isinstance(raw_news, dict):
            raw_news = raw_news.get("news", [])

        if not isinstance(raw_news, list):
            return []

        articles = []
        for item in raw_news:
            if not isinstance(item, dict):
                continue

            pub_time = item.get("providerPublishTime", 0)
            if not pub_time:
                pub_time = item.get("publish_time", 0)

            thumbnail = None
            thumb_data = item.get("thumbnail")
            if isinstance(thumb_data, dict):
                resolutions = thumb_data.get("resolutions", [])
                if resolutions and isinstance(resolutions, list):
                    thumbnail = resolutions[0].get("url") if isinstance(resolutions[0], dict) else None

            articles.append({
                "title": item.get("title", ""),
                "link": item.get("link", item.get("url", "")),
                "source": item.get("publisher", item.get("source", "Yahoo Finance")),
                "published": pub_time,
                "published_iso": datetime.fromtimestamp(
                    pub_time, tz=timezone.utc
                ).isoformat() if pub_time else None,
                "type": "stock",
                "category": "stocks",
                "symbol": symbol,
                "thumbnail": thumbnail,
                "related_tickers": item.get("relatedTickers", []),
            })

        logger.info(f"Fetched {len(articles)} news articles for {symbol}")
        return articles

    except Exception as e:
        logger.error(f"Failed to fetch stock news for {symbol}: {e}")
        return []


def _fetch_all_rss_parallel() -> dict:
    """Fetch ALL category RSS feeds in parallel using ThreadPoolExecutor."""
    results = {cat: [] for cat in CATEGORY_RSS_FEEDS}
    tasks = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        for cat_name, feeds in CATEGORY_RSS_FEEDS.items():
            for url in feeds:
                future = executor.submit(_fetch_rss, url, cat_name)
                tasks.append((future, cat_name))

        for future, cat_name in tasks:
            try:
                articles = future.result(timeout=12)
                results[cat_name].extend(articles)
            except Exception as e:
                logger.error(f"Parallel RSS fetch failed ({cat_name}): {e}")

    # Deduplicate within each category
    for cat_name in results:
        seen = set()
        unique = []
        for a in results[cat_name]:
            if a["title"] not in seen:
                seen.add(a["title"])
                unique.append(a)
        unique.sort(key=lambda x: x.get("published", 0), reverse=True)
        results[cat_name] = unique

    return results


def fetch_market_news() -> list:
    """Fetch general stock market news from Google News RSS feeds."""
    articles = []
    seen_titles = set()

    for rss_url in MARKET_RSS_FEEDS:
        for a in _fetch_rss(rss_url, "stocks"):
            if a["title"] not in seen_titles:
                seen_titles.add(a["title"])
                articles.append(a)

    logger.info(f"Fetched {len(articles)} market news from RSS feeds")
    return articles


def fetch_category_news(category: str) -> list:
    """Fetch news for a specific category."""
    feeds = CATEGORY_RSS_FEEDS.get(category, [])
    if not feeds:
        return []

    articles = []
    seen_titles = set()

    for rss_url in feeds:
        for a in _fetch_rss(rss_url, category):
            if a["title"] not in seen_titles:
                seen_titles.add(a["title"])
                articles.append(a)

    articles.sort(key=lambda x: x.get("published", 0), reverse=True)
    logger.info(f"Fetched {len(articles)} news for category '{category}'")
    return articles


def get_all_news(symbol: str = "^NSEI") -> dict:
    """
    Get combined news (stock-specific + market headlines) with caching.
    """
    cache_key = f"news_{symbol}"
    now = time.time()

    if cache_key in _cache:
        cached = _cache[cache_key]
        if now - cached["fetched_at"] < CACHE_TTL:
            return cached["data"]

    stock_news = fetch_stock_news(symbol)
    market_news = fetch_market_news()

    all_articles = stock_news + market_news
    seen = set()
    unique = []
    for a in all_articles:
        key = a["title"].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(a)

    unique.sort(key=lambda x: x.get("published", 0), reverse=True)

    result = {
        "articles": unique,
        "stock_count": len(stock_news),
        "market_count": len(market_news),
        "total_count": len(unique),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
    }

    _cache[cache_key] = {"data": result, "fetched_at": now}
    return result


def get_categorized_news(symbol: Optional[str] = None) -> dict:
    """
    Get global news organized by categories for the newspaper-style layout.
    Fetches all RSS feeds in parallel for speed.
    If symbol is provided, stock-specific news is also included as a bonus.
    """
    cache_key = f"news_categorized_{symbol or 'global'}"
    now = time.time()

    if cache_key in _cache:
        cached = _cache[cache_key]
        if now - cached["fetched_at"] < CACHE_TTL:
            return cached["data"]

    # Fetch all RSS feeds in parallel; optionally include stock-specific news
    with ThreadPoolExecutor(max_workers=14) as executor:
        stock_future = None
        if symbol:
            stock_future = executor.submit(fetch_stock_news, symbol)
        rss_future = executor.submit(_fetch_all_rss_parallel)

        stock_news = []
        if stock_future:
            try:
                stock_news = stock_future.result(timeout=15)
            except Exception as e:
                logger.error(f"Stock news fetch failed: {e}")

        try:
            categories = rss_future.result(timeout=15)
        except Exception as e:
            logger.error(f"RSS parallel fetch failed: {e}")
            categories = {cat: [] for cat in CATEGORY_RSS_FEEDS}

    for a in stock_news:
        a["category"] = "stocks"

    # Merge stock news into stocks category
    all_articles = list(stock_news)
    seen_titles = {a["title"].lower().strip() for a in all_articles}

    for cat_name in categories:
        unique_cat = []
        for a in categories[cat_name]:
            key = a["title"].lower().strip()
            if key and key not in seen_titles:
                seen_titles.add(key)
                unique_cat.append(a)
                all_articles.append(a)
        categories[cat_name] = unique_cat

    # Prepend stock news to stocks category (if any)
    if stock_news:
        categories["stocks"] = stock_news + categories.get("stocks", [])
        categories["stocks"].sort(key=lambda x: x.get("published", 0), reverse=True)

    # Build "latest" — top articles from all categories combined
    all_articles.sort(key=lambda x: x.get("published", 0), reverse=True)
    categories["latest"] = all_articles[:50]

    cat_counts = {k: len(v) for k, v in categories.items()}

    result = {
        "categories": categories,
        "counts": cat_counts,
        "total_count": len(all_articles),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
    }

    _cache[cache_key] = {"data": result, "fetched_at": now}
    return result


def get_market_news_only() -> dict:
    """Get only general market news (no symbol needed)."""
    cache_key = "news_market_only"
    now = time.time()

    if cache_key in _cache:
        cached = _cache[cache_key]
        if now - cached["fetched_at"] < CACHE_TTL:
            return cached["data"]

    market_news = fetch_market_news()
    market_news.sort(key=lambda x: x.get("published", 0), reverse=True)

    result = {
        "articles": market_news,
        "stock_count": 0,
        "market_count": len(market_news),
        "total_count": len(market_news),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": None,
    }

    _cache[cache_key] = {"data": result, "fetched_at": now}
    return result
