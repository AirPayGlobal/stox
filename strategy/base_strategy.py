"""
Abstract base class for all trading strategies.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import pandas as pd

from analysis.signals import Signal


class BaseStrategy(ABC):
    """All strategies must implement generate_signal."""

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> tuple[Signal, int]:
        """
        Given OHLCV DataFrame, return (Signal, confidence_score 0-100).
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""
        ...
