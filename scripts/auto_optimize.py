"""
Auto-Optimization Script
========================
Runs every Sunday via GitHub Actions. Follows the CLAUDE.md weekly workflow:
1. Review live trade performance
2. Baseline backtest
3. Apply top 1-2 high-priority recommendations
4. Verify via backtest (must improve Sharpe + profit factor)
5. Exit 0 if improved (workflow will commit), exit 1 if not (no commit)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def run(cmd: str) -> int:
    print(f"\n$ {cmd}")
    return subprocess.call(cmd, shell=True, cwd=ROOT)


def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def replace_in_file(filepath: str, pattern: str, replacement: str) -> bool:
    with open(filepath) as f:
        content = f.read()
    new_content = re.sub(pattern, replacement, content)
    if new_content == content:
        return False
    with open(filepath, "w") as f:
        f.write(new_content)
    return True


# ------------------------------------------------------------------ Guardrails

LIMITS = {
    "BUY_THRESHOLD":    (40,  65),
    "SELL_THRESHOLD":   (40,  70),
    "STOP_LOSS_PCT":    (0.01, 0.05),
    "MAX_POSITION_PCT": (0.02, 0.10),
    "RSI_OVERSOLD":     (20,  40),
    "RSI_OVERBOUGHT":   (60,  85),
}


def clamp(param: str, value: float) -> float:
    if param in LIMITS:
        lo, hi = LIMITS[param]
        return max(lo, min(hi, value))
    return value


# ------------------------------------------------------------------ Apply changes

def apply_recommendation(rec: dict) -> bool:
    """Apply a single recommendation to source files. Returns True if changed."""
    param = rec.get("parameter", "")
    action = rec.get("action", "")
    recommended = rec.get("recommended")

    if recommended is None or param == "N/A" or action in ("wait", "hold"):
        return False

    recommended = clamp(param, float(recommended))

    # signals.py — BUY_THRESHOLD, SELL_THRESHOLD
    if param in ("BUY_THRESHOLD", "SELL_THRESHOLD"):
        filepath = os.path.join(ROOT, "analysis", "signals.py")
        pattern = rf"^({param}\s*=\s*)\d+"
        replacement = rf"\g<1>{int(recommended)}"
        changed = replace_in_file(filepath, pattern, replacement)
        if changed:
            print(f"  ✓ {param}: {rec.get('current')} → {int(recommended)}")
        return changed

    # config.py — STOP_LOSS_PCT, MAX_POSITION_PCT, RSI_OVERSOLD, RSI_OVERBOUGHT
    if param in ("STOP_LOSS_PCT", "MAX_POSITION_PCT", "RSI_OVERSOLD", "RSI_OVERBOUGHT"):
        filepath = os.path.join(ROOT, "config.py")
        if param in ("RSI_OVERSOLD", "RSI_OVERBOUGHT"):
            pattern = rf"({param}\s*:\s*float\s*=\s*)[\d.]+"
            replacement = rf"\g<1>{recommended:.1f}"
        else:
            pattern = rf'({param}\s*:\s*float\s*=\s*float\(os\.getenv\("[^"]+",\s*")[^"]+(")'
            replacement = rf'\g<1>{recommended:.3f}\2'
        changed = replace_in_file(filepath, pattern, replacement)
        if changed:
            print(f"  ✓ {param}: {rec.get('current')} → {recommended:.4f}")
        return changed

    # WATCHLIST — remove bad symbol
    if param == "WATCHLIST" and action == "remove":
        symbol = rec.get("symbol", "")
        if not symbol:
            return False
        filepath = os.path.join(ROOT, "config.py")
        # Remove the symbol from the watchlist string
        with open(filepath) as f:
            content = f.read()
        # Remove quoted symbol with optional trailing comma/space
        new_content = re.sub(rf'["\s]*"{re.escape(symbol)}",?\s*', ' ', content)
        if new_content != content:
            with open(filepath, "w") as f:
                f.write(new_content)
            print(f"  ✓ Removed {symbol} from WATCHLIST")
            return True

    return False


def revert_changes() -> None:
    print("\nReverting changes...")
    subprocess.call("git checkout -- analysis/signals.py config.py", shell=True, cwd=ROOT)


# ------------------------------------------------------------------ Main

def main() -> int:
    print(f"\n{'='*60}")
    print(f"  STOX Weekly Auto-Optimization")
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # Step 1: Performance review
    print("\n[1/4] Running performance review...")
    review_path = "/tmp/stox_review.json"
    ret = run(f"python analysis/review.py --days 7 --output {review_path}")
    if ret != 0 or not os.path.exists(review_path):
        print("Review failed — skipping optimization this week.")
        return 1

    review = load_json(review_path)
    recs = review.get("recommendations", [])
    all_time = review.get("stats", {}).get("all_time", {})

    print(f"\n  Trades: {all_time.get('count', 0)} | "
          f"Win rate: {all_time.get('win_rate', 0):.0%} | "
          f"Profit factor: {all_time.get('profit_factor', 0):.2f}x")

    # Need at least 10 trades for meaningful optimization
    if all_time.get("count", 0) < 20:
        print(f"\n  Only {all_time.get('count', 0)} trades — need 20+ for optimization. Skipping.")
        return 0

    # Step 2: Baseline backtest
    print("\n[2/4] Running baseline backtest...")
    before_path = "/tmp/stox_before.json"
    ret = run(f"python backtest/portfolio_backtest.py --days 365 --symbols 20 --output {before_path}")
    if ret != 0 or not os.path.exists(before_path):
        print("Baseline backtest failed — skipping optimization.")
        return 1

    before = load_json(before_path)
    sharpe_before = before.get("sharpe_ratio", 0)
    pf_before = before.get("profit_factor", 0)
    print(f"\n  Baseline — Sharpe: {sharpe_before:.2f} | Profit factor: {pf_before:.2f}x")

    # Step 3: Apply top high-priority recommendations (max 2)
    print("\n[3/4] Applying recommendations...")
    high_priority = [r for r in recs if r.get("priority") == "high"]
    medium_priority = [r for r in recs if r.get("priority") == "medium"]
    candidates = (high_priority + medium_priority)[:2]

    applied = []
    for rec in candidates:
        print(f"\n  [{rec.get('priority','?').upper()}] {rec.get('parameter')}: {rec.get('reason','')}")
        if apply_recommendation(rec):
            applied.append(rec)

    if not applied:
        print("\n  No applicable changes found — strategy looks healthy.")
        return 0

    # Step 4: Verify via backtest
    print("\n[4/4] Verifying improvements via backtest...")
    after_path = "/tmp/stox_after.json"
    ret = run(f"python backtest/portfolio_backtest.py --days 365 --symbols 20 --output {after_path}")
    if ret != 0 or not os.path.exists(after_path):
        print("Verification backtest failed — reverting.")
        revert_changes()
        return 1

    after = load_json(after_path)
    sharpe_after = after.get("sharpe_ratio", 0)
    pf_after = after.get("profit_factor", 0)

    print(f"\n  Before — Sharpe: {sharpe_before:.2f} | PF: {pf_before:.2f}x")
    print(f"  After  — Sharpe: {sharpe_after:.2f} | PF: {pf_after:.2f}x")

    if sharpe_after > sharpe_before and pf_after > pf_before:
        print(f"\n  ✅ Both metrics improved — deploying changes.")
        change_desc = "; ".join(
            f"{r['parameter']} {r['action']} ({r.get('current','?')}→{r.get('recommended','?')})"
            for r in applied
        )
        print(f"\n  Changes: {change_desc}")
        return 0  # GitHub Actions will commit
    else:
        if sharpe_after <= sharpe_before:
            print(f"  ✗ Sharpe did not improve ({sharpe_before:.2f} → {sharpe_after:.2f})")
        if pf_after <= pf_before:
            print(f"  ✗ Profit factor did not improve ({pf_before:.2f} → {pf_after:.2f})")
        print("\n  ❌ Metrics did not improve — reverting changes.")
        revert_changes()
        return 1


if __name__ == "__main__":
    sys.exit(main())
