"""
Volatility Regime Detector
===========================
Classifies the current market into one of four regimes based on SPY trend,
VIX fear level, and ADX trend strength:

  BULL     — SPY > 200-SMA, VIX < 20, ADX > 20 → full sizing, prefer longs
  RANGING  — ADX < 20 (sideways chop)          → 60% sizing, pairs preferred
  HIGH_VOL — VIX > 30 (extreme fear)            → no new long entries
  BEAR     — SPY < 200-SMA and VIX > 20         → 50% longs, short bias

Results cached for 1 hour to avoid redundant downloads.
"""
from __future__ import annotations

import logging
import time
from enum import Enum

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger("regime")

_cache: dict = {"regime": None, "ts": 0.0, "detail": {}}
_CACHE_TTL = 3600  # 1 hour


class Regime(str, Enum):
    BULL     = "BULL"
    RANGING  = "RANGING"
    HIGH_VOL = "HIGH_VOL"
    BEAR     = "BEAR"


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """Compute Average Directional Index (ADX) — measures trend strength."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low  - close.shift(1)).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()

    up   = high - high.shift(1)
    down = low.shift(1) - low

    pos_dm = up.where((up > down) & (up > 0), 0.0)
    neg_dm = down.where((down > up) & (down > 0), 0.0)

    pos_di = 100 * pos_dm.rolling(period).mean() / atr.replace(0, np.nan)
    neg_di = 100 * neg_dm.rolling(period).mean() / atr.replace(0, np.nan)

    di_diff = (pos_di - neg_di).abs()
    di_sum  = (pos_di + neg_di).replace(0, np.nan)
    dx      = 100 * di_diff / di_sum
    adx     = dx.rolling(period).mean()

    val = adx.iloc[-1]
    return float(val) if not np.isnan(val) else 20.0


def detect_regime() -> Regime:
    """
    Detect current market regime. Returns cached value within TTL.
    Priority: HIGH_VOL > BEAR > RANGING > BULL
    """
    global _cache
    now = time.time()
    if _cache["regime"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["regime"]

    try:
        spy = yf.download("SPY", period="1y", interval="1d",
                          progress=False, auto_adjust=True)
        vix = yf.download("^VIX", period="5d", interval="1d",
                          progress=False, auto_adjust=True)

        if spy.empty or vix.empty:
            logger.warning("Regime: could not fetch SPY/VIX — defaulting BULL")
            return Regime.BULL

        close = spy["Close"].squeeze()
        high  = spy["High"].squeeze()
        low   = spy["Low"].squeeze()

        vix_level   = float(vix["Close"].iloc[-1])
        spy_price   = float(close.iloc[-1])
        sma200      = float(close.rolling(200).mean().iloc[-1])
        adx         = _compute_adx(high, low, close)
        above_200   = spy_price > sma200

        if vix_level > 30:
            regime = Regime.HIGH_VOL
        elif not above_200 and vix_level > 20:
            regime = Regime.BEAR
        elif adx < 20:
            regime = Regime.RANGING
        else:
            regime = Regime.BULL

        detail = {
            "spy_price": round(spy_price, 2),
            "sma200":    round(sma200, 2),
            "vix":       round(vix_level, 1),
            "adx":       round(adx, 1),
            "above_200": above_200,
        }
        _cache = {"regime": regime, "ts": now, "detail": detail}
        logger.info(
            f"Market regime: {regime.value} "
            f"(SPY={spy_price:.2f} vs 200SMA={sma200:.2f} "
            f"VIX={vix_level:.1f} ADX={adx:.1f})"
        )
        return regime

    except Exception as exc:
        logger.warning(f"Regime detection failed: {exc} — defaulting BULL")
        return Regime.BULL


def get_regime_detail() -> dict:
    """Return the latest regime + underlying metrics (for API/dashboard)."""
    regime = detect_regime()
    return {"regime": regime.value, **_cache.get("detail", {})}


def get_sizing_multiplier() -> float:
    """
    Returns a position-size multiplier based on current regime:
      BULL     → 1.0   full size
      RANGING  → 0.6   reduce to avoid whipsaws in choppy markets
      HIGH_VOL → 0.0   no new entries (belt+suspenders with VIX filter)
      BEAR     → 0.5   half size for long positions
    """
    return {
        Regime.BULL:     1.0,
        Regime.RANGING:  0.6,
        Regime.HIGH_VOL: 0.0,
        Regime.BEAR:     0.5,
    }[detect_regime()]


def regime_allows_longs() -> bool:
    """Return False only in HIGH_VOL regime; all others allow long entries."""
    return detect_regime() != Regime.HIGH_VOL


def regime_favors_shorts() -> bool:
    """BEAR regime actively favors short positions over longs."""
    return detect_regime() == Regime.BEAR
