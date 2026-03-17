"""
Signal generation: combines indicator readings into BUY / SELL / HOLD decisions.

Signal scoring (0-100):
  - Each condition adds points; threshold determines action.
  - Requires multiple confirming signals to reduce false positives.
"""
from __future__ import annotations

from enum import Enum

import pandas as pd

from config import Config
from analysis.indicators import add_all_indicators, ema_crossover_up, ema_crossover_down, is_above_trend
from utils.logger import get_logger

logger = get_logger(__name__)


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


BUY_THRESHOLD = 60    # score out of 100 required to issue a BUY
SELL_THRESHOLD = 60   # score required to issue a SELL


def _score_buy(row: pd.Series, prev: pd.Series) -> int:
    """Score a BUY signal for the latest bar (0-100)."""
    score = 0

    # 1. Price above long-term trend EMA (20 pts)
    if row["close"] > row["ema_trend"]:
        score += 20

    # 2. Fast EMA above slow EMA — uptrend in progress (15 pts)
    if row["ema_fast"] > row["ema_slow"]:
        score += 15

    # 3. EMA golden cross on this bar (20 pts — strong signal)
    if row["ema_fast"] > row["ema_slow"] and prev["ema_fast"] <= prev["ema_slow"]:
        score += 20

    # 4. RSI in bullish zone — not overbought (15 pts)
    if Config.RSI_OVERSOLD <= row["rsi"] <= Config.RSI_OVERBOUGHT:
        score += 15

    # 5. MACD histogram turning positive (15 pts)
    if row["macd_hist"] > 0 and prev["macd_hist"] <= 0:
        score += 15
    elif row["macd_hist"] > 0:
        score += 7

    # 6. Price near/below Bollinger mid-band — room to run upward (10 pts)
    if row["bb_pct"] < 0.55:
        score += 10

    # 7. Above-average volume — institutional participation (5 pts)
    if row["volume"] > row["volume_sma"] * 1.1:
        score += 5

    return min(score, 100)


def _score_sell(row: pd.Series, prev: pd.Series) -> int:
    """Score a SELL signal for the latest bar (0-100)."""
    score = 0

    # 1. EMA death cross — fast crosses below slow (25 pts)
    if row["ema_fast"] < row["ema_slow"] and prev["ema_fast"] >= prev["ema_slow"]:
        score += 25

    # 2. Fast EMA below slow EMA — downtrend (15 pts)
    if row["ema_fast"] < row["ema_slow"]:
        score += 15

    # 3. RSI overbought (20 pts)
    if row["rsi"] > Config.RSI_OVERBOUGHT:
        score += 20

    # 4. MACD histogram turning negative (20 pts)
    if row["macd_hist"] < 0 and prev["macd_hist"] >= 0:
        score += 20
    elif row["macd_hist"] < 0:
        score += 8

    # 5. Price near upper Bollinger Band — stretched (15 pts)
    if row["bb_pct"] > 0.85:
        score += 15

    # 6. Price below trend EMA (5 pts)
    if row["close"] < row["ema_trend"]:
        score += 5

    return min(score, 100)


def generate_signal(df: pd.DataFrame) -> tuple[Signal, int]:
    """
    Given a DataFrame with OHLCV data, compute indicators and return
    the latest bar's signal plus its confidence score.

    Returns (Signal, score).
    """
    if len(df) < Config.EMA_TREND + 10:
        return Signal.HOLD, 0

    df = add_all_indicators(df)
    df = df.dropna(subset=["ema_fast", "ema_slow", "ema_trend", "rsi", "macd_hist", "bb_pct"])

    if len(df) < 2:
        return Signal.HOLD, 0

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    buy_score = _score_buy(latest, prev)
    sell_score = _score_sell(latest, prev)

    if buy_score >= BUY_THRESHOLD and buy_score > sell_score:
        return Signal.BUY, buy_score
    elif sell_score >= SELL_THRESHOLD and sell_score > buy_score:
        return Signal.SELL, sell_score
    else:
        return Signal.HOLD, max(buy_score, sell_score)


def screen_universe(
    data: dict[str, pd.DataFrame],
) -> list[tuple[str, Signal, int]]:
    """
    Run signal generation across the entire watchlist.
    Returns list of (symbol, signal, score) sorted by score descending.
    """
    results = []
    for symbol, df in data.items():
        try:
            signal, score = generate_signal(df)
            if signal != Signal.HOLD:
                results.append((symbol, signal, score))
                logger.info(f"{symbol}: {signal.value} (score={score})")
        except Exception as exc:
            logger.warning(f"Signal error for {symbol}: {exc}")

    results.sort(key=lambda x: x[2], reverse=True)
    return results
