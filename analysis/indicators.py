"""
Technical indicator calculations using pandas-ta.
All functions accept and return DataFrames to keep pipelines clean.
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta

from config import Config


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all indicators used by the strategy and append them as columns.

    Added columns:
        ema_fast, ema_slow, ema_trend
        rsi
        macd, macd_signal, macd_hist
        bb_upper, bb_mid, bb_lower, bb_pct (price position within bands, 0-1)
        atr            — Average True Range (for position sizing / stop placement)
        volume_sma     — 20-period SMA of volume (detect abnormal volume)
    """
    df = df.copy()

    # Exponential moving averages
    df["ema_fast"] = ta.ema(df["close"], length=Config.EMA_FAST)
    df["ema_slow"] = ta.ema(df["close"], length=Config.EMA_SLOW)
    df["ema_trend"] = ta.ema(df["close"], length=Config.EMA_TREND)

    # RSI
    df["rsi"] = ta.rsi(df["close"], length=Config.RSI_PERIOD)

    # MACD
    macd = ta.macd(
        df["close"],
        fast=Config.MACD_FAST,
        slow=Config.MACD_SLOW,
        signal=Config.MACD_SIGNAL,
    )
    if macd is not None:
        df["macd"] = macd[f"MACD_{Config.MACD_FAST}_{Config.MACD_SLOW}_{Config.MACD_SIGNAL}"]
        df["macd_signal"] = macd[f"MACDs_{Config.MACD_FAST}_{Config.MACD_SLOW}_{Config.MACD_SIGNAL}"]
        df["macd_hist"] = macd[f"MACDh_{Config.MACD_FAST}_{Config.MACD_SLOW}_{Config.MACD_SIGNAL}"]

    # Bollinger Bands
    bb = ta.bbands(df["close"], length=Config.BB_PERIOD, std=Config.BB_STD)
    if bb is not None:
        df["bb_lower"] = bb[f"BBL_{Config.BB_PERIOD}_{Config.BB_STD}"]
        df["bb_mid"] = bb[f"BBM_{Config.BB_PERIOD}_{Config.BB_STD}"]
        df["bb_upper"] = bb[f"BBU_{Config.BB_PERIOD}_{Config.BB_STD}"]
        bb_range = df["bb_upper"] - df["bb_lower"]
        df["bb_pct"] = (df["close"] - df["bb_lower"]) / bb_range.replace(0, float("nan"))

    # ATR (14-period) — used for volatility-adjusted stop-loss
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # Volume SMA
    df["volume_sma"] = ta.sma(df["volume"], length=20)

    return df


def is_above_trend(df: pd.DataFrame) -> pd.Series:
    """True where price is above the long-term EMA trend line."""
    return df["close"] > df["ema_trend"]


def ema_crossover_up(df: pd.DataFrame) -> pd.Series:
    """True on bars where fast EMA crosses above slow EMA."""
    cross = (df["ema_fast"] > df["ema_slow"]) & (
        df["ema_fast"].shift(1) <= df["ema_slow"].shift(1)
    )
    return cross


def ema_crossover_down(df: pd.DataFrame) -> pd.Series:
    """True on bars where fast EMA crosses below slow EMA."""
    cross = (df["ema_fast"] < df["ema_slow"]) & (
        df["ema_fast"].shift(1) >= df["ema_slow"].shift(1)
    )
    return cross
