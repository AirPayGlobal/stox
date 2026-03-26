"""
Alpaca News API — thin REST wrapper
=====================================
`StockHistoricalDataClient.get_news()` does not exist in all versions of
alpaca-py. This module calls the Alpaca News REST endpoint directly using
`requests` (already a project dependency), so it works regardless of SDK
version.

Endpoint: GET https://data.alpaca.markets/v1beta1/news
Docs: https://docs.alpaca.markets/reference/news-1
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from config import Config

logger = logging.getLogger("data.news")

_BASE = "https://data.alpaca.markets/v1beta1/news"


class _Article:
    """Lightweight stand-in for the alpaca-py news article object."""
    __slots__ = ("headline", "summary", "symbols", "created_at", "source")

    def __init__(self, d: dict) -> None:
        self.headline   = d.get("headline", "")
        self.summary    = d.get("summary", "")
        self.symbols    = d.get("symbols", [])
        self.created_at = d.get("created_at", "")
        self.source     = d.get("source", "")


def fetch_news(
    symbols: Optional[list[str]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    hours: Optional[int] = None,
    limit: int = 50,
) -> list[_Article]:
    """
    Fetch news articles from the Alpaca News API.

    Args:
        symbols : list of ticker symbols to filter by (None = market-wide)
        start   : start datetime (UTC); overridden by `hours` if provided
        end     : end datetime (UTC); defaults to now
        hours   : convenience shortcut — sets start = now - hours
        limit   : max articles per request (Alpaca caps at 50)

    Returns list of _Article objects. Returns [] on any error.
    """
    if not Config.ALPACA_API_KEY or not Config.ALPACA_API_SECRET:
        logger.debug("News: API keys not configured")
        return []

    now = datetime.now(timezone.utc)
    if end is None:
        end = now
    if hours is not None:
        start = now - timedelta(hours=hours)
    elif start is None:
        start = now - timedelta(hours=24)

    params: dict = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end":   end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": min(limit, 50),
        "sort":  "desc",
    }
    if symbols:
        params["symbols"] = ",".join(symbols)

    headers = {
        "APCA-API-KEY-ID":     Config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": Config.ALPACA_API_SECRET,
    }

    articles: list[_Article] = []
    try:
        resp = requests.get(_BASE, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        for item in resp.json().get("news", []):
            articles.append(_Article(item))
    except Exception as exc:
        logger.warning(f"News API request failed: {exc}")

    return articles
