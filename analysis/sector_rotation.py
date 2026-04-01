"""
Sector Rotation Layer
=====================
Ranks the 11 SPDR sector ETFs by 3-month price momentum and only permits
new BUY entries in the top N performing sectors.

Why it works
------------
Sectors rotate in predictable cycles tied to the economic calendar:
  - Early expansion : XLY (consumer disc), XLK (tech)
  - Late expansion  : XLE (energy), XLB (materials)
  - Contraction     : XLP (staples), XLU (utilities), XLV (health)
  - Recovery        : XLF (finance), XLI (industrial)

Buying only in the top 4 out of 11 sectors means you ride the sectors
with institutional momentum behind them and avoid laggards.

Usage
-----
  from analysis.sector_rotation import is_in_top_sectors, get_sector_rankings

  # Block a BUY candidate
  if not is_in_top_sectors("NVDA"):
      continue

  # Print full sector rankings
  for etf, ret, rank in get_sector_rankings():
      print(f"{rank}. {etf}  {ret:+.1%}")
"""
from __future__ import annotations

import time
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Sector → ETF map  (SPDR Select Sector ETFs)
# ---------------------------------------------------------------------------

SECTOR_ETF_NAMES = {
    "XLK":  "Technology",
    "XLV":  "Health Care",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLC":  "Communication",
    "XLP":  "Consumer Staples",
    "XLY":  "Consumer Discretionary",
    "XLI":  "Industrials",
    "XLRE": "Real Estate",
    "XLB":  "Materials",
    "XLU":  "Utilities",
}

# Symbol → sector ETF (covers the default WATCHLIST + common large-caps)
SYMBOL_TO_SECTOR: dict[str, str] = {
    # Technology (XLK)
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AVGO": "XLK",
    "CSCO": "XLK", "ACN":  "XLK", "CRM":  "XLK", "TXN":  "XLK",
    "ADBE": "XLK", "QCOM": "XLK", "AMD":  "XLK", "INTC": "XLK",
    "NOW":  "XLK", "INTU": "XLK",
    "AMAT": "XLK", "KLAC": "XLK", "MRVL": "XLK", "NET":  "XLK",
    "MU":   "XLK", "LRCX": "XLK", "PANW": "XLK", "SNPS": "XLK",
    "CDNS": "XLK", "FTNT": "XLK", "ANSS": "XLK", "KEYS": "XLK",
    "GLW":  "XLK", "MPWR": "XLK", "ON":   "XLK", "TER":  "XLK",
    # Communication (XLC)
    "GOOGL": "XLC", "GOOG": "XLC", "META": "XLC", "NFLX": "XLC",
    "DIS":  "XLC", "CMCSA": "XLC", "VZ": "XLC", "T": "XLC",
    "TTWO": "XLC", "EA":   "XLC", "PARA": "XLC", "WBD":  "XLC",
    "LUMN": "XLC", "TMUS": "XLC",
    # Consumer Discretionary (XLY)
    "AMZN": "XLY", "TSLA": "XLY", "HD":   "XLY", "NKE":  "XLY",
    "MCD":  "XLY", "SBUX": "XLY", "LOW":  "XLY", "TGT":  "XLY",
    "BKNG": "XLY", "MAR":  "XLY",
    "MGM":  "XLY", "ROST": "XLY", "TJX":  "XLY", "LVS":  "XLY",
    "WYNN": "XLY", "F":    "XLY", "GM":   "XLY", "EBAY": "XLY",
    "BBY":  "XLY", "POOL": "XLY", "PHM":  "XLY", "DHI":  "XLY",
    "LEN":  "XLY", "NVR":  "XLY", "HLT":  "XLY", "RCL":  "XLY",
    # Health Care (XLV)
    "UNH":  "XLV", "JNJ":  "XLV", "LLY":  "XLV", "ABBV": "XLV",
    "MRK":  "XLV", "TMO":  "XLV", "ABT":  "XLV", "DHR":  "XLV",
    "AMGN": "XLV", "BMY":  "XLV", "PFE":  "XLV", "ISRG": "XLV",
    "GILD": "XLV", "MDT":  "XLV",
    "BIIB": "XLV", "HOLX": "XLV", "REGN": "XLV", "VRTX": "XLV",
    "ZBH":  "XLV", "BAX":  "XLV", "BSX":  "XLV", "EW":   "XLV",
    "IQV":  "XLV", "MCK":  "XLV", "CAH":  "XLV", "CNC":  "XLV",
    "HCA":  "XLV", "CI":   "XLV", "CVS":  "XLV", "HUM":  "XLV",
    # Financials (XLF)
    "BRK.B": "XLF", "JPM": "XLF", "V":    "XLF", "MA":   "XLF",
    "BAC":   "XLF", "WFC": "XLF", "GS":   "XLF", "MS":   "XLF",
    "AXP":   "XLF", "BLK": "XLF", "SCHW": "XLF",
    "C":    "XLF", "USB":  "XLF", "PNC":  "XLF", "TFC":  "XLF",
    "COF":  "XLF", "CB":   "XLF", "MMC":  "XLF", "AON":  "XLF",
    "ICE":  "XLF", "CME":  "XLF", "SPGI": "XLF", "MCO":  "XLF",
    "FIS":  "XLF", "FISV": "XLF", "AFL":  "XLF", "MET":  "XLF",
    # Consumer Staples (XLP)
    "PG":  "XLP", "KO":  "XLP", "PEP": "XLP", "COST": "XLP",
    "WMT": "XLP", "PM":  "XLP", "MO":  "XLP", "CL":   "XLP",
    "MDLZ": "XLP", "KHC": "XLP", "GIS": "XLP", "HSY":  "XLP",
    "STZ":  "XLP", "KMB": "XLP", "SYY": "XLP", "CAG":  "XLP",
    # Energy (XLE)
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "EOG":  "XLE",
    "SLB": "XLE", "PSX": "XLE", "MPC": "XLE",
    "DVN": "XLE", "HAL": "XLE", "OXY": "XLE", "PXD":  "XLE",
    "FANG": "XLE", "BKR": "XLE", "VLO": "XLE", "HES":  "XLE",
    "KMI": "XLE", "WMB": "XLE", "OKE": "XLE",
    # Industrials (XLI)
    "RTX": "XLI", "UPS": "XLI", "HON": "XLI", "CAT":  "XLI",
    "GE":  "XLI", "BA":  "XLI", "MMM": "XLI", "DE":   "XLI",
    "LMT": "XLI", "NOC": "XLI", "GD":  "XLI", "FDX":  "XLI",
    "CSX": "XLI", "NSC": "XLI", "EMR": "XLI", "ETN":  "XLI",
    "ROK": "XLI", "DOV": "XLI", "PH":  "XLI", "ITW":  "XLI",
    # Materials (XLB)
    "LIN": "XLB", "APD": "XLB", "ECL": "XLB", "NEM":  "XLB",
    "FCX": "XLB", "NUE": "XLB", "VMC": "XLB", "MLM":  "XLB",
    "ALB": "XLB", "CF":  "XLB", "MOS": "XLB", "IP":   "XLB",
    # Real Estate (XLRE)
    "AMT": "XLRE", "PLD": "XLRE", "CCI": "XLRE",
    "EQIX": "XLRE", "PSA": "XLRE", "O":   "XLRE", "WELL": "XLRE",
    "DLR": "XLRE", "EXR": "XLRE", "AVB": "XLRE", "EQR":  "XLRE",
    "VTR": "XLRE", "SPG": "XLRE", "ARE": "XLRE",
    # Utilities (XLU)
    "NEE": "XLU", "DUK": "XLU", "SO":  "XLU", "D":    "XLU",
    "AEP": "XLU", "EXC": "XLU", "XEL": "XLU", "SRE":  "XLU",
    "ED":  "XLU", "ETR": "XLU", "PCG": "XLU", "EIX":  "XLU",
}

# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

# Cache: (rankings_list, fetched_epoch)
_cache: Optional[tuple[list, float]] = None
_CACHE_TTL = 3600  # refresh once per hour


def get_sector_rankings() -> list[tuple[str, float, int]]:
    """
    Fetch 3-month returns for all 11 sector ETFs and rank them.

    Returns list of (etf_symbol, 3m_return, rank) sorted best → worst.
    Cached for 1 hour.
    """
    global _cache
    if _cache and (time.time() - _cache[1]) < _CACHE_TTL:
        return _cache[0]

    try:
        import yfinance as yf
        etfs = list(SECTOR_ETF_NAMES.keys())

        tickers = yf.download(
            etfs,
            period="4mo",      # 4 months to get clean 3-month window
            interval="1d",
            progress=False,
            auto_adjust=True,
        )["Close"]

        returns = {}
        for etf in etfs:
            if etf not in tickers.columns:
                continue
            series = tickers[etf].dropna()
            if len(series) < 20:
                continue
            # 3-month return: last close vs close 63 trading days ago
            lookback = min(63, len(series) - 1)
            ret = (series.iloc[-1] / series.iloc[-lookback]) - 1
            returns[etf] = float(ret)

        ranked = sorted(returns.items(), key=lambda x: x[1], reverse=True)
        result = [(etf, ret, rank + 1) for rank, (etf, ret) in enumerate(ranked)]

        logger.info(
            "Sector rankings (3-month momentum): "
            + " | ".join(f"{e}={r:+.1%}" for e, r, _ in result[:5])
        )

        _cache = (result, time.time())
        return result

    except Exception as exc:
        logger.warning(f"Sector ranking failed: {exc} — no sector filter applied")
        return []


def get_top_sector_etfs(top_n: int = None) -> set[str]:
    """Return the set of top N sector ETF symbols by momentum."""
    from config import Config
    n = top_n if top_n is not None else Config.SECTOR_TOP_N
    rankings = get_sector_rankings()
    if not rankings:
        return set()  # empty = no filter (fail open)
    return {etf for etf, _, rank in rankings if rank <= n}


def get_symbol_sector(symbol: str) -> Optional[str]:
    """Return the sector ETF for a symbol, or None if unknown."""
    return SYMBOL_TO_SECTOR.get(symbol)


def is_in_top_sectors(symbol: str, top_n: int = None) -> bool:
    """
    Return True if the symbol's sector is in the top N by 3-month momentum.
    Returns True (no filter) for unknown symbols or if ranking fetch fails.
    """
    from config import Config
    n = top_n if top_n is not None else Config.SECTOR_TOP_N

    sector = get_symbol_sector(symbol)
    if sector is None:
        logger.debug(f"{symbol} has no sector mapping — skipping sector filter")
        return True  # unknown symbol = don't block

    top = get_top_sector_etfs(n)
    if not top:
        return True  # ranking failed = don't block

    in_top = sector in top
    if not in_top:
        rankings = get_sector_rankings()
        rank = next((r for e, _, r in rankings if e == sector), "?")
        logger.info(
            f"Sector filter: {symbol} ({sector} rank={rank}/{len(rankings)}) "
            f"not in top {n} — skipping"
        )
    return in_top
