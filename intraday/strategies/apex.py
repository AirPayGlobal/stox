"""
APEX v4.2 — Composite Alpha Score (CAS) strategy.
Long-only tech momentum, intraday.

5-Factor model (adapted to available OHLCV data):
  Factor A: Catalyst proxy       (30%) — gap magnitude, volume spike vs 20-bar avg
  Factor B: Pre-market momentum  (25%) — VWAP position, open strength, consecutive up bars
  Factor C: Technical structure  (35%) — RSI 55-75 zone, EMA trend, SMA breakout, ATR filter
  Factor D: Macro regime         (10%) — QQQ trend (above VWAP + EMA9)

CAS execution thresholds:
  85-100 → STRONG BUY — full position (4% NAV)
  70-84  → BUY — standard position (2.5% NAV)
  <70    → NO TRADE

Entry trigger: price > VWAP, volume > 1.5x avg, ATR% > 2% (sufficient daily range).
Hard stop: -2% from entry. Target 1: +3%. Target 2: +5%.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone, timedelta
from typing import Optional

import pandas as pd

from intraday.indicators import add_intraday_indicators
from config import Config
from utils.logger import get_logger

logger = get_logger("intraday.strategies.apex")

_ET = timezone(timedelta(hours=-4))


@dataclass
class APEXSignal:
    symbol: str
    side: str               # always "buy" — long-only mandate
    entry_price: float
    stop_loss: float
    take_profit: float      # target 1 (+3%)
    target_2: float         # target 2 (+5%)
    cas_score: float        # 0-100 composite alpha score
    score: float            # alias for cas_score (bot sorter compatibility)
    factor_catalyst: float
    factor_momentum: float
    factor_technical: float
    factor_regime: float


def generate_signal(
    symbol: str,
    df: pd.DataFrame,
    prev_close: float = 0.0,
    qqq_df: Optional[pd.DataFrame] = None,
) -> Optional[APEXSignal]:
    """
    Generate an APEX long signal with Composite Alpha Score.

    symbol:     ticker being evaluated
    df:         intraday 5-min bars (open, high, low, close, volume)
    prev_close: previous session close price (for gap calculation)
    qqq_df:     QQQ 5-min bars for macro regime scoring
    """
    if df is None or df.empty or len(df) < 5:
        return None

    hard_stop_pct = Config.APEX_HARD_STOP_PCT
    target1_pct = Config.APEX_TARGET1_PCT
    target2_pct = Config.APEX_TARGET2_PCT
    min_cas = Config.APEX_MIN_CAS
    min_atr_pct = Config.APEX_MIN_ATR_PCT

    try:
        df = add_intraday_indicators(df)
        latest = df.iloc[-1]

        close = float(latest["close"])
        vwap = float(latest["vwap"])
        rsi_val = float(latest["rsi"]) if pd.notna(latest.get("rsi")) else 50.0
        ema9 = float(latest["ema9"]) if pd.notna(latest.get("ema9")) else close
        sma20 = float(latest["sma20"]) if pd.notna(latest.get("sma20")) else close
        sma50 = float(latest["sma50"]) if pd.notna(latest.get("sma50")) else close
        atr_pct = float(latest["atr_pct"]) if pd.notna(latest.get("atr_pct")) else 0.0
        volume = float(latest["volume"])
        session_high = float(latest.get("session_high", close))

        if close <= 0 or vwap <= 0:
            return None

        # Hard filter: sufficient ATR% — stock must have >2% daily range potential
        if atr_pct < min_atr_pct:
            logger.debug("APEX %s: ATR%%=%.3f below min %.3f — skipping", symbol, atr_pct, min_atr_pct)
            return None

        # Volume metrics (20-bar average)
        avg_volume = float(df["volume"].tail(20).mean()) if len(df) >= 20 else float(df["volume"].mean())
        if avg_volume <= 0:
            avg_volume = 1.0
        volume_ratio = volume / avg_volume

        # Entry trigger: 15-min close above VWAP with volume > 1.5x avg
        if close < vwap:
            return None
        if volume_ratio < 1.5:
            return None

        # ------------------------------------------------------------------ Factor A: Catalyst proxy (30%)
        # Best available proxy without options/news data: gap size + volume spike
        factor_a = 0.0
        gap_pct = 0.0
        if prev_close > 0:
            gap_pct = (float(df.iloc[0]["open"]) - prev_close) / prev_close
            if gap_pct >= 0.025:                            # target ≥2.5% gap
                factor_a = min(70.0, gap_pct * 1600.0)     # 2.5%→40pts, 5%→80pts (capped)
            elif gap_pct >= 0.01:
                factor_a = gap_pct * 1200.0                 # smaller gaps score lower
        # Volume spike amplifies catalyst signal (institutional accumulation proxy)
        if volume_ratio >= 3.0:
            factor_a = min(100.0, factor_a + 25.0)
        elif volume_ratio >= 2.0:
            factor_a = min(100.0, factor_a + 12.0)
        elif volume_ratio >= 1.5:
            factor_a = min(100.0, factor_a + 5.0)

        # ------------------------------------------------------------------ Factor B: Pre-market momentum (25%)
        vwap_dev = (close - vwap) / vwap  # positive = above VWAP
        factor_b = 0.0
        if vwap_dev > 0:
            factor_b += min(40.0, vwap_dev * 2500.0)       # 1% above VWAP → 25pts
        if close > ema9:
            factor_b += 20.0                                # price above short-term EMA
        # Consecutive bullish candles in last 4 bars = directional momentum
        recent = df.tail(4)
        up_bars = sum(1 for _, row in recent.iterrows() if float(row["close"]) > float(row["open"]))
        factor_b += up_bars * 10.0
        factor_b = min(100.0, factor_b)

        # ------------------------------------------------------------------ Factor C: Technical structure (35%)
        factor_c = 0.0
        # RSI 55-75: momentum zone, not overbought — peak score at RSI=65
        if 55.0 <= rsi_val <= 75.0:
            rsi_score = 35.0 * (1.0 - abs(rsi_val - 65.0) / 10.0)
            factor_c += rsi_score
        elif 45.0 <= rsi_val < 55.0:
            factor_c += 8.0                                 # sub-optimal but tradeable
        # SMA breakout: above 20-bar and 50-bar SMA
        if close > sma20:
            factor_c += 12.0
        if close > sma50:
            factor_c += 8.0
        # Volume confirmation (beyond the entry filter threshold)
        factor_c += min(18.0, (volume_ratio - 1.5) * 9.0)
        # ATR% bonus — bigger range = better trade opportunity
        factor_c += min(10.0, max(0.0, (atr_pct - min_atr_pct) * 300.0))
        # Closing near session high = price strength
        if session_high > 0 and close >= session_high * 0.97:
            factor_c += 10.0
        factor_c = min(100.0, factor_c)

        # ------------------------------------------------------------------ Factor D: Macro regime (10%)
        factor_d = 50.0  # neutral default when QQQ data unavailable
        if qqq_df is not None and not qqq_df.empty and len(qqq_df) >= 5:
            try:
                qqq = add_intraday_indicators(qqq_df)
                ql = qqq.iloc[-1]
                qqq_close = float(ql["close"])
                qqq_vwap = float(ql["vwap"])
                qqq_ema9 = float(ql["ema9"]) if pd.notna(ql.get("ema9")) else qqq_close
                qqq_rsi = float(ql["rsi"]) if pd.notna(ql.get("rsi")) else 50.0
                above_vwap = qqq_close > qqq_vwap
                above_ema = qqq_close > qqq_ema9
                if above_vwap and above_ema:
                    factor_d = 90.0                         # strong bullish regime
                elif above_vwap or above_ema:
                    factor_d = 65.0                         # mild bullish
                elif qqq_rsi < 40.0:
                    factor_d = 15.0                         # weak market — high risk
                else:
                    factor_d = 35.0                         # neutral/weak
            except Exception:
                pass

        # ------------------------------------------------------------------ Composite Alpha Score
        cas = (
            factor_a * 0.30
            + factor_b * 0.25
            + factor_c * 0.35
            + factor_d * 0.10
        )

        if cas < min_cas:
            logger.debug(
                "APEX %s: CAS=%.1f below threshold=%.1f (A=%.1f B=%.1f C=%.1f D=%.1f)",
                symbol, cas, min_cas, factor_a, factor_b, factor_c, factor_d,
            )
            return None

        entry = close
        stop_loss = round(entry * (1.0 - hard_stop_pct), 2)
        take_profit = round(entry * (1.0 + target1_pct), 2)
        target_2 = round(entry * (1.0 + target2_pct), 2)

        logger.info(
            "APEX LONG %s: CAS=%.1f entry=%.2f SL=%.2f TP1=%.2f TP2=%.2f gap=%.1f%% "
            "vol=%.1fx RSI=%.1f (A=%.1f B=%.1f C=%.1f D=%.1f)",
            symbol, cas, entry, stop_loss, take_profit, target_2,
            gap_pct * 100, volume_ratio, rsi_val,
            factor_a, factor_b, factor_c, factor_d,
        )

        return APEXSignal(
            symbol=symbol,
            side="buy",
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_2=target_2,
            cas_score=round(cas, 1),
            score=round(cas, 1),
            factor_catalyst=round(factor_a, 1),
            factor_momentum=round(factor_b, 1),
            factor_technical=round(factor_c, 1),
            factor_regime=round(factor_d, 1),
        )

    except Exception as exc:
        logger.warning("APEX generate_signal(%s) error: %s", symbol, exc)

    return None
