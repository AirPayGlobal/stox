"""
EMA + RSI + MACD Strategy
--------------------------
Entry conditions (BUY):
  - Price above 50-period EMA (macro uptrend)
  - Fast EMA (9) crossed above or is above slow EMA (21)
  - RSI between 40 and 65 — bullish momentum, not overbought
  - MACD histogram positive or turning positive
  - Price below upper Bollinger Band (room to grow)

Exit conditions (SELL):
  - Fast EMA crosses below slow EMA
  - RSI > 65 (overbought)
  - MACD histogram turns negative
  - Price hits upper Bollinger Band
  - Stop-loss or take-profit hit (handled by broker bracket order)
"""
from __future__ import annotations

import pandas as pd

from analysis.signals import Signal, generate_signal
from strategy.base_strategy import BaseStrategy


class EmaRsiMacdStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "EMA9/21/50 + RSI14 + MACD + Bollinger Bands"

    def generate_signal(self, df: pd.DataFrame) -> tuple[Signal, int]:
        return generate_signal(df)
