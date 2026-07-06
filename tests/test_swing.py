import numpy as np
import pandas as pd

from backtest.swing import simulate_swing
from config import Config

ET = "America/New_York"


def rth_bars(closes_by_day: dict, lows_override: dict | None = None) -> pd.DataFrame:
    """Build 30-min RTH bars: closes_by_day maps date -> list of 13 closes."""
    frames = []
    for day, closes in closes_by_day.items():
        idx = pd.date_range(f"{day} 09:30", periods=len(closes), freq="30min", tz=ET)
        df = pd.DataFrame(
            {
                "open": closes,
                "high": [c + 0.4 for c in closes],
                "low": [c - 0.4 for c in closes],
                "close": closes,
                "volume": 50_000,
            },
            index=idx,
        )
        frames.append(df)
    out = pd.concat(frames)
    for ts, low in (lows_override or {}).items():
        out.loc[pd.Timestamp(ts, tz=ET), "low"] = low
    return out


def test_swing_sweep_entry_and_multi_day_target():
    # Day 1: drift down; 4H candle (13:30-16:00) sweeps below the 09:30-13:30
    # candle's low then closes back above it -> LONG at day 1 close region.
    # Days 2-3: rally through the 2R target.
    d1 = [500 - 0.1 * i for i in range(8)] + [498.2, 499.6, 499.8, 500.1, 500.3]
    d2 = list(np.linspace(500.5, 504.0, 13))
    d3 = list(np.linspace(504.0, 508.0, 13))
    bars = rth_bars(
        {"2026-06-29": d1, "2026-06-30": d2, "2026-07-01": d3},
        lows_override={"2026-06-29 13:30": 497.0},  # the sweep wick
    )
    trades = simulate_swing("SPY", bars, equity=250_000)
    assert len(trades) >= 1
    t = trades[0]
    assert t["direction"] == "LONG"
    assert t["exit_reason"] in ("UL_TP", "TIME")
    assert t["hold_days"] >= 0.5  # held past the entry session
    assert t["pnl"] > 0


def test_swing_stop_loss_on_breakdown():
    # Same sweep setup, but the market then breaks below the wick stop.
    d1 = [500 - 0.1 * i for i in range(8)] + [498.2, 499.6, 499.8, 500.1, 500.3]
    d2 = list(np.linspace(500.0, 495.0, 13))  # falls through 497 stop
    bars = rth_bars(
        {"2026-06-29": d1, "2026-06-30": d2},
        lows_override={"2026-06-29 13:30": 497.0},
    )
    trades = simulate_swing("SPY", bars, equity=250_000)
    assert len(trades) >= 1
    t = trades[0]
    assert t["exit_reason"] == "UL_SL"
    assert t["pnl"] < 0


def test_swing_no_trade_in_steady_trend():
    days = {}
    price = 500.0
    for d in ["2026-06-29", "2026-06-30", "2026-07-01"]:
        days[d] = list(np.linspace(price, price + 3, 13))
        price += 3
    trades = simulate_swing("SPY", rth_bars(days), equity=250_000)
    assert trades == []  # no sweep-reclaim ever forms
