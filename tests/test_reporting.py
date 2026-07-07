from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from reporting import daily_report, period_report, trades_csv
from trading.positions import PositionBook

ET = ZoneInfo("America/New_York")


def seeded_book(tmp_path) -> PositionBook:
    """3 trading days: day1 +100/-50 (orb SPY), day2 +300 (sweep QQQ),
    day3 -200 (sweep SPY)."""
    book = PositionBook(path=str(tmp_path / "trades.json"))
    rows = [
        (2, "SPY", "orb", 1.00, 1.10, "TP"),       # +100, 2 days ago
        (2, "SPY", "orb", 1.00, 0.95, "SL"),       # -50
        (1, "QQQ", "sweep", 1.00, 1.30, "UL_TP"),  # +300, 1 day ago
        (0, "SPY", "sweep", 1.00, 0.80, "UL_SL"),  # -200, today
    ]
    for days_ago, und, strat, entry, exit_p, status in rows:
        sym = f"{und}260706C00500000"
        book.open(sym, und, "LONG", 10, entry, strategy=strat)
        t = book.close(sym, exit_p, status)
        when = datetime.now(ET) - timedelta(days=days_ago)
        t.closed_at = when.isoformat()
        t.opened_at = (when - timedelta(hours=1)).isoformat()
    return book


def test_period_totals_and_daily_rows(tmp_path):
    r = period_report(seeded_book(tmp_path), days=7)
    assert r["totals"]["trades"] == 4
    assert r["totals"]["pnl"] == 150.0          # 100 - 50 + 300 - 200
    assert r["totals"]["win_rate"] == 0.5
    assert r["trading_days"] == 3
    assert r["green_days"] == 2
    assert [d["pnl"] for d in r["daily"]] == [50.0, 300.0, -200.0]
    assert r["daily"][-1]["cumulative"] == 150.0
    assert r["max_drawdown"] == 200.0           # peak 350 -> 150


def test_period_group_breakdowns(tmp_path):
    r = period_report(seeded_book(tmp_path), days=7)
    assert r["per_strategy"]["orb"]["pnl"] == 50.0
    assert r["per_strategy"]["sweep"]["pnl"] == 100.0
    assert r["per_underlying"]["QQQ"]["pnl"] == 300.0
    assert r["exit_reasons"]["UL_SL"]["count"] == 1
    assert r["exit_reasons"]["UL_SL"]["pnl"] == -200.0


def test_period_window_excludes_old_trades(tmp_path):
    r = period_report(seeded_book(tmp_path), days=1)  # today + yesterday
    assert r["totals"]["trades"] == 2
    assert r["totals"]["pnl"] == 100.0


def test_daily_report_today(tmp_path):
    r = daily_report(seeded_book(tmp_path))
    assert r["date"] == date.today().isoformat()
    assert r["totals"]["trades"] == 1
    assert r["trades"][0]["pnl"] == -200.0
    assert r["trades"][0]["strategy"] == "sweep"


def test_empty_periods(tmp_path):
    book = PositionBook(path=str(tmp_path / "trades.json"))
    assert period_report(book, 30)["trades"] == 0
    assert daily_report(book, "2020-01-01")["trades"] == 0


def test_csv_export(tmp_path):
    csv_text = trades_csv(seeded_book(tmp_path), days=7)
    lines = csv_text.strip().splitlines()
    assert len(lines) == 5  # header + 4 trades
    assert lines[0].startswith("closed_at,opened_at,symbol")
    assert "sweep" in csv_text and "UL_TP" in csv_text
