"""
Technical indicator calculations using the `ta` library.
All functions accept and return DataFrames to keep pipelines clean.
"""
from __future__ import annotations

import pandas as pd
import ta.momentum
import ta.trend
import ta.volatility

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
    df["ema_fast"] = ta.trend.EMAIndicator(df["close"], window=Config.EMA_FAST).ema_indicator()
    df["ema_slow"] = ta.trend.EMAIndicator(df["close"], window=Config.EMA_SLOW).ema_indicator()
    df["ema_trend"] = ta.trend.EMAIndicator(df["close"], window=Config.EMA_TREND).ema_indicator()

    # RSI
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=Config.RSI_PERIOD).rsi()

    # MACD
    macd_ind = ta.trend.MACD(
        df["close"],
        window_fast=Config.MACD_FAST,
        window_slow=Config.MACD_SLOW,
        window_sign=Config.MACD_SIGNAL,
    )
    df["macd"] = macd_ind.macd()
    df["macd_signal"] = macd_ind.macd_signal()
    df["macd_hist"] = macd_ind.macd_diff()

    # Bollinger Bands
    bb_ind = ta.volatility.BollingerBands(
        df["close"], window=Config.BB_PERIOD, window_dev=Config.BB_STD
    )
    df["bb_upper"] = bb_ind.bollinger_hband()
    df["bb_mid"] = bb_ind.bollinger_mavg()
    df["bb_lower"] = bb_ind.bollinger_lband()
    bb_range = df["bb_upper"] - df["bb_lower"]
    df["bb_pct"] = (df["close"] - df["bb_lower"]) / bb_range.replace(0, float("nan"))

    # ATR (14-period) — used for volatility-adjusted stop-loss
    df["atr"] = ta.volatility.AverageTrueRange(
        df["high"], df["low"], df["close"], window=14
    ).average_true_range()

    # Volume SMA
    df["volume_sma"] = df["volume"].rolling(window=20).mean()

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
