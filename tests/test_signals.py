import numpy as np
import pandas as pd

from analysis.indicators import ema, opening_range, rsi, session_vwap
from analysis.signals import Signal, generate_signal


def make_session(closes: list[float], start="2026-07-06 09:30", freq="5min") -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(closes), freq=freq, tz="America/New_York")
    closes = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": closes.shift(1).fillna(closes.iloc[0]),
            "high": closes + 0.10,
            "low": closes - 0.10,
            "close": closes,
            "volume": 10_000,
        }
    )


def test_strong_uptrend_gives_long():
    df = make_session(list(np.linspace(500, 506, 20)))
    result = generate_signal(df)
    assert result.signal == Signal.LONG
    assert result.score >= 70


def test_strong_downtrend_gives_short():
    df = make_session(list(np.linspace(506, 500, 20)))
    result = generate_signal(df)
    assert result.signal == Signal.SHORT


def test_chop_gives_flat():
    closes = [500 + (0.05 if i % 2 else -0.05) for i in range(20)]
    result = generate_signal(make_session(closes))
    assert result.signal == Signal.FLAT


def test_too_few_bars_gives_flat():
    result = generate_signal(make_session([500.0] * 3))
    assert result.signal == Signal.FLAT
    assert result.score == 0


def test_vwap_between_low_and_high():
    df = make_session(list(np.linspace(500, 505, 20)))
    vwap = session_vwap(df).iloc[-1]
    assert df["low"].min() <= vwap <= df["high"].max()


def test_opening_range():
    df = make_session([500, 501, 502, 499, 505, 506])
    hi, lo = opening_range(df, minutes=15)  # first three 5-min bars
    assert hi == 502.10
    assert lo == 499.90


def test_rsi_bounds():
    df = make_session(list(np.linspace(500, 510, 30)))
    val = rsi(df["close"]).iloc[-1]
    assert 50 < val <= 100


def test_ema_tracks_price():
    s = pd.Series(np.linspace(100, 110, 50))
    assert abs(ema(s, 9).iloc[-1] - 110) < 1.0
