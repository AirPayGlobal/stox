"""
APEX v4.2 intraday universe — tech and tech-adjacent stocks.

Criteria: NYSE/NASDAQ/CBOE, GICS sector 45 + tech-adjacent, $500M+ market cap,
ADV > $50M, price $5-$2000. No Chinese ADRs, no SPACs, no leveraged ETFs.
"""
from __future__ import annotations

from utils.logger import get_logger

logger = get_logger("intraday.universe")

# QQQ is used for macro regime scoring — not traded directly
REGIME_REFERENCE = ["QQQ", "SPY"]

# Core APEX trading universe — tech and tech-adjacent, long-only
APEX_UNIVERSE: list[str] = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
    # Semiconductors
    "AMD", "INTC", "AVGO", "QCOM", "MU", "AMAT", "LRCX", "KLAC",
    "MRVL", "TXN", "ADI", "MCHP", "ON", "NXPI", "SWKS",
    # Software / Cloud / SaaS
    "CRM", "ORCL", "ADBE", "NOW", "WDAY", "SNOW", "PLTR", "DDOG",
    "ZS", "CRWD", "NET", "OKTA", "HUBS", "TEAM", "MDB",
    # AI / Infrastructure
    "SMCI", "ARM", "ANET", "DELL", "HPE",
    # Fintech
    "V", "MA", "PYPL", "SQ", "COIN", "FI", "FISV",
    # Cybersecurity
    "PANW", "FTNT", "CYBR",
    # Enterprise / Hardware
    "CSCO", "ACN", "IBM",
    # Consumer / Mobility tech
    "SNAP", "RBLX", "UBER", "LYFT",
]

# Backwards-compat alias used by existing bot imports
INTRADAY_UNIVERSE: list[str] = APEX_UNIVERSE


def get_gap_stocks(
    prev_closes: dict[str, float],
    current_opens: dict[str, float],
    min_gap_pct: float = 0.025,
) -> list[str]:
    """Return symbols that gapped by at least min_gap_pct (default 2.5% per APEX spec)."""
    gappers = []
    for sym in APEX_UNIVERSE:
        prev = prev_closes.get(sym, 0.0)
        curr = current_opens.get(sym, 0.0)
        if prev <= 0 or curr <= 0:
            continue
        gap = abs(curr - prev) / prev
        if gap >= min_gap_pct:
            gappers.append(sym)
            logger.debug("Gap stock: %s gap=%.2f%%", sym, gap * 100)
    return gappers


def get_high_volume_movers(bars_by_symbol: dict, volume_mult: float = 2.0) -> list[str]:
    """Return symbols whose most-recent bar volume exceeds volume_mult × their 20-bar average."""
    movers = []
    for sym, df in bars_by_symbol.items():
        if df is None or df.empty or len(df) < 5:
            continue
        try:
            avg_vol = float(df["volume"].tail(20).mean())
            if avg_vol <= 0:
                continue
            latest_vol = float(df["volume"].iloc[-1])
            if latest_vol >= avg_vol * volume_mult:
                movers.append(sym)
                logger.debug("High-vol mover: %s vol_ratio=%.1f", sym, latest_vol / avg_vol)
        except Exception:
            continue
    return movers
