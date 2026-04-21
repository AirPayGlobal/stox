"""
Intraday trading universe — liquid, high-volume names suited for day trading.
"""
from __future__ import annotations

from utils.logger import get_logger

logger = get_logger("intraday.universe")

# Core intraday universe: ETFs + mega/large-caps with tight spreads
INTRADAY_UNIVERSE: list[str] = [
    # Index ETFs
    "SPY", "QQQ", "IWM", "DIA",
    # Sector ETFs
    "XLK", "XLF", "XLE", "XLV", "XLY",
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
    # Semis
    "AMD", "INTC", "AVGO", "QCOM", "MU",
    # Financials
    "JPM", "BAC", "GS", "MS", "C",
    # Consumer / Retail
    "AMZN", "HD", "WMT", "COST", "NKE",
    # Energy
    "XOM", "CVX", "OXY",
    # Biotech / Healthcare
    "UNH", "JNJ", "PFE", "MRNA",
    # Leveraged ETFs for range expansion
    "SQQQ", "TQQQ",
]
# Remove any duplicates while preserving order
_seen: set = set()
_deduped = []
for _s in INTRADAY_UNIVERSE:
    if _s not in _seen:
        _seen.add(_s)
        _deduped.append(_s)
INTRADAY_UNIVERSE = _deduped


def get_gap_stocks(prev_closes: dict[str, float], current_opens: dict[str, float], min_gap_pct: float = 0.02) -> list[str]:
    """
    Return symbols from INTRADAY_UNIVERSE that gapped by at least min_gap_pct.

    prev_closes / current_opens: {symbol: price} dicts.
    """
    gappers = []
    for sym in INTRADAY_UNIVERSE:
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
    """
    Return symbols whose most-recent bar volume exceeds volume_mult × their 20-bar average.

    bars_by_symbol: {symbol: pd.DataFrame} where each df has a 'volume' column.
    """
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
