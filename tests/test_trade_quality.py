from datetime import datetime
from zoneinfo import ZoneInfo

from reporting import period_report
from trading.positions import PositionBook

ET = ZoneInfo("America/New_York")


def book(tmp_path, rows):
    """rows: (pnl, entry, mfe) — builds closed trades dated today."""
    b = PositionBook(path=str(tmp_path / "trades.json"))
    for i, (pnl, entry, mfe) in enumerate(rows):
        sym = f"SPY2607{i:02d}C00500000"
        b.open(sym, "SPY", "LONG", 10, entry)
        t = b.close(sym, entry + pnl / 1000, "TP" if pnl > 0 else "SL")
        t.pnl = pnl
        t.mfe_premium = mfe
        t.closed_at = datetime.now(ET).isoformat()
    return b


def test_trade_quality_metrics(tmp_path):
    # 4 trades: 2 immediate reversals (mfe~entry), 1 recoverable loser
    # (mfe +20%), 1 winner.
    b = book(tmp_path, [
        (-500, 1.00, 1.02),   # loser, +2% MFE  -> immediate reversal
        (-500, 1.00, 1.00),   # loser, 0% MFE   -> immediate reversal
        (-500, 1.00, 1.20),   # loser, +20% MFE -> recoverable
        (+800, 1.00, 1.50),   # winner
    ])
    tq = period_report(b, 7)["trade_quality"]
    assert tq["sample"] == 4
    assert tq["immediate_reversal_rate"] == 0.5      # 2 of 4 under 5% MFE
    assert tq["recoverable_loss_rate"] == round(1 / 3, 3)  # 1 of 3 losers >= 15%
    assert tq["avg_loser_mfe_pct"] == round((0.02 + 0.0 + 0.20) / 3, 3)


def test_trade_quality_empty_without_mfe(tmp_path):
    # Trades with mfe=0 (older, untracked) are excluded -> no metrics.
    b = book(tmp_path, [(-500, 1.00, 0.0), (800, 1.00, 0.0)])
    assert period_report(b, 7)["trade_quality"] == {}
