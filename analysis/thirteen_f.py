"""
SEC 13F Filing Tracker
========================
Monitors quarterly 13F-HR filings from top hedge funds to detect
new or increased institutional positions.  Uses as a BULLISH signal
when smart money is accumulating a stock.

Data source
-----------
SEC EDGAR public API (no key required):
  https://data.sec.gov/submissions/CIK{cik}.json  — filing list
  https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/  — filing files

Hedge funds tracked
-------------------
  Berkshire Hathaway, Bridgewater, Renaissance Tech, Citadel,
  Two Sigma, Tiger Global, Pershing Square, Appaloosa

Scoring
-------
  For each watchlist symbol, counts how many funds:
    - Newly opened a position this quarter    (+2 each)
    - Increased an existing position          (+1 each)
    - Decreased a position                   (-1 each)
    - Closed a position entirely             (-2 each)
  Result normalised to [-3, +3].

Caching
-------
  Results cached for 7 days (13F filings are quarterly).
  Falls back gracefully to 0 if SEC API is unreachable.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Hedge funds  (name → CIK, zero-padded to 10 digits)
# ---------------------------------------------------------------------------

HEDGE_FUNDS: dict[str, str] = {
    "Berkshire Hathaway":  "0001067983",
    "Renaissance Tech":    "0001037389",
    "Two Sigma":           "0001424212",
    "Citadel":             "0001423298",
    "Tiger Global":        "0001428287",
    "Pershing Square":     "0001336528",
    "Appaloosa":           "0001006438",
    "Viking Global":       "0001103804",
}

_HEADERS = {"User-Agent": "STOX-Bot research@stox.io"}  # SEC requires User-Agent

# ---------------------------------------------------------------------------
# Company name → ticker  (for parsing 13F issuer names)
# ---------------------------------------------------------------------------

_NAME_TO_TICKER: dict[str, str] = {
    "APPLE":         "AAPL",  "MICROSOFT":     "MSFT",
    "ALPHABET":      "GOOGL", "AMAZON":        "AMZN",
    "NVIDIA":        "NVDA",  "META PLATFORM": "META",
    "TESLA":         "TSLA",  "BERKSHIRE":     "BRK.B",
    "UNITEDHEALTH":  "UNH",   "JOHNSON":       "JNJ",
    "VISA INC":      "V",     "EXXON":         "XOM",
    "JPMORGAN":      "JPM",   "PROCTER":       "PG",
    "MASTERCARD":    "MA",    "HOME DEPOT":    "HD",
    "CHEVRON":       "CVX",   "ELI LILLY":     "LLY",
    "ABBVIE":        "ABBV",  "MERCK":         "MRK",
    "BROADCOM":      "AVGO",  "COSTCO":        "COST",
    "PEPSICO":       "PEP",   "COCA-COLA":     "KO",
    "THERMO FISHER": "TMO",   "BANK OF AMER":  "BAC",
    "WALMART":       "WMT",   "CISCO":         "CSCO",
    "ACCENTURE":     "ACN",   "MCDONALDS":     "MCD",
    "ABBOTT":        "ABT",   "DANAHER":       "DHR",
    "SALESFORCE":    "CRM",   "NEXTERA":       "NEE",
    "TEXAS INSTR":   "TXN",   "ADOBE":         "ADBE",
    "NIKE":          "NKE",   "PHILIP MORRIS": "PM",
    "LINDE":         "LIN",   "BRISTOL":       "BMY",
    "RAYTHEON":      "RTX",   "QUALCOMM":      "QCOM",
    "AMGEN":         "AMGN",  "UNITED PARCEL": "UPS",
    "STARBUCKS":     "SBUX",  "GOOGLE":        "GOOGL",
}


def _name_to_ticker(name: str) -> Optional[str]:
    """Fuzzy-match an issuer name from 13F to a ticker symbol."""
    name_up = name.upper()
    for key, ticker in _NAME_TO_TICKER.items():
        if key in name_up:
            return ticker
    return None


# ---------------------------------------------------------------------------
# SEC EDGAR fetching
# ---------------------------------------------------------------------------

def _get_recent_13f_accessions(cik: str, count: int = 2) -> list[str]:
    """Return the accession numbers of the most recent 13F-HR filings."""
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug(f"EDGAR submissions fetch failed for CIK {cik}: {exc}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms  = recent.get("form", [])
    accs   = recent.get("accessionNumber", [])

    results = []
    for form, acc in zip(forms, accs):
        if form == "13F-HR":
            results.append(acc.replace("-", ""))
            if len(results) >= count:
                break
    return results


def _fetch_infotable(cik: str, accession: str) -> list[dict]:
    """
    Fetch and parse the infotable.xml from a 13F filing.
    Returns list of {ticker, shares, value} dicts.
    """
    # Build index URL to find the actual xml filename
    acc_path = f"{accession[:10]}/{accession[10:12]}/{accession[12:]}"
    index_url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession}/infotable.xml"
    )
    try:
        resp = requests.get(index_url, headers=_HEADERS, timeout=15)
        if resp.status_code != 200:
            # Try alternate common filename
            index_url = index_url.replace("infotable.xml", "form13fInfoTable.xml")
            resp = requests.get(index_url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        xml_text = resp.text
    except Exception as exc:
        logger.debug(f"13F infotable fetch failed ({cik}/{accession}): {exc}")
        return []

    holdings = []
    try:
        # Strip namespace for simpler parsing
        xml_clean = re.sub(r' xmlns[^"]*"[^"]*"', "", xml_text)
        root = ET.fromstring(xml_clean)

        for info in root.iter("infoTable"):
            name_el   = info.find("nameOfIssuer")
            shares_el = info.find(".//sshPrnamt")
            value_el  = info.find("value")
            putcall_el = info.find("putCall")

            if name_el is None:
                continue

            name   = (name_el.text or "").strip()
            ticker = _name_to_ticker(name)
            if not ticker:
                continue

            # Skip put/call options
            if putcall_el is not None and putcall_el.text:
                continue

            try:
                shares = int((shares_el.text or "0").replace(",", ""))
                value  = int((value_el.text  or "0").replace(",", ""))
            except ValueError:
                continue

            holdings.append({"ticker": ticker, "shares": shares, "value": value})

    except ET.ParseError as exc:
        logger.debug(f"XML parse error: {exc}")

    return holdings


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

# Cache: {cik: ({ticker: shares}, fetched_epoch)}
_holdings_cache: dict[str, tuple[list[dict], float]] = {}
_CACHE_TTL = 7 * 24 * 3600  # 7 days


def _get_fund_holdings(cik: str) -> tuple[list[dict], list[dict]]:
    """
    Return (latest_holdings, previous_holdings) for a fund.
    Each is a list of {ticker, shares, value}.
    """
    now = time.time()
    if cik in _holdings_cache:
        cached, fetched_at = _holdings_cache[cik]
        if now - fetched_at < _CACHE_TTL:
            return cached  # type: ignore

    accessions = _get_recent_13f_accessions(cik, count=2)
    if not accessions:
        _holdings_cache[cik] = ([], [], now)
        return [], []

    latest = _fetch_infotable(cik, accessions[0])
    prev   = _fetch_infotable(cik, accessions[1]) if len(accessions) > 1 else []

    _holdings_cache[cik] = (latest, prev, now)
    return latest, prev


def get_smart_money_scores() -> dict[str, int]:
    """
    Scan recent 13F filings for all tracked funds and return a score
    per ticker:  +2 new position, +1 increased, -1 decreased, -2 closed.
    Capped at ±3.
    """
    scores: dict[str, int] = {}

    for fund_name, cik in HEDGE_FUNDS.items():
        try:
            latest, prev = _get_fund_holdings(cik)
            if not latest:
                continue

            prev_shares = {h["ticker"]: h["shares"] for h in prev}
            for holding in latest:
                ticker = holding["ticker"]
                cur    = holding["shares"]
                old    = prev_shares.get(ticker, 0)

                if old == 0:
                    delta = 2   # new position
                elif cur > old * 1.1:
                    delta = 1   # increased ≥10%
                elif cur < old * 0.9:
                    delta = -1  # decreased ≥10%
                else:
                    delta = 0   # roughly unchanged

                scores[ticker] = scores.get(ticker, 0) + delta

            # Closed positions (in prev but not latest)
            prev_tickers = {h["ticker"] for h in prev}
            latest_tickers = {h["ticker"] for h in latest}
            for t in prev_tickers - latest_tickers:
                scores[t] = scores.get(t, 0) - 2

            logger.debug(f"13F processed: {fund_name} ({len(latest)} holdings)")
            time.sleep(0.15)  # respect SEC rate limit

        except Exception as exc:
            logger.debug(f"13F error for {fund_name}: {exc}")

    # Cap scores at ±3
    return {t: max(-3, min(3, s)) for t, s in scores.items()}


# Simple per-process cache for the full score dict
_scores_cache: Optional[tuple[dict, float]] = None
_SCORES_TTL = 6 * 3600  # 6 hours


def get_thirteen_f_score(symbol: str) -> int:
    """
    Return the 13F smart money score for a symbol (-3 to +3).
    Positive = top funds are buying. Fetches and caches all fund data.
    """
    global _scores_cache
    now = time.time()

    if _scores_cache and (now - _scores_cache[1]) < _SCORES_TTL:
        scores = _scores_cache[0]
    else:
        logger.info("Refreshing 13F smart money scores from SEC EDGAR...")
        scores = get_smart_money_scores()
        _scores_cache = (scores, now)
        if scores:
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
            logger.info(f"13F top buys: {top}")

    score = scores.get(symbol, 0)
    if score != 0:
        logger.info(f"13F score for {symbol}: {score:+d}")
    return score
