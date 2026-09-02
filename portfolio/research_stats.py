"""
Deflated Sharpe Ratio and Probabilistic Sharpe Ratio (Bailey & López de Prado).

Gate 7 of the validation standard: a Sharpe ratio is not evidence until it is
adjusted for (a) the number of trials attempted (multiple testing), (b) the
non-normality of returns (skew/kurtosis inflate or deflate the estimate), and
(c) the track length. These functions produce a probability in [0, 1] that the
true Sharpe exceeds a threshold — the deflated threshold accounts for how many
strategy variants were tried.

Pure Python (uses statistics.NormalDist; no scipy). Everything is computed in
PER-PERIOD (non-annualized) Sharpe units for internal consistency; annualized
values are exposed only for reporting.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

_N = NormalDist()
EULER = 0.5772156649015329   # Euler–Mascheroni constant


def _moments(returns) -> tuple | None:
    r = np.asarray(list(returns), dtype=float)
    r = r[~np.isnan(r)]
    T = len(r)
    if T < 3:
        return None
    mu = float(r.mean())
    sd = float(r.std(ddof=1))
    if sd <= 0:
        return None
    z = (r - mu) / sd
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4))     # standard kurtosis (normal = 3)
    return mu / sd, skew, kurt, T     # per-period Sharpe, skew, kurt, T


def per_period_sharpe(returns) -> float | None:
    m = _moments(returns)
    return None if m is None else m[0]


def probabilistic_sharpe_ratio(returns, sr_benchmark: float = 0.0) -> float | None:
    """P(true per-period Sharpe > sr_benchmark) given the sample, adjusted for
    skew/kurtosis and track length. `sr_benchmark` is per-period."""
    m = _moments(returns)
    if m is None:
        return None
    sr, skew, kurt, T = m
    denom = math.sqrt(max(1 - skew * sr + ((kurt - 1) / 4) * sr * sr, 1e-12))
    stat = (sr - sr_benchmark) * math.sqrt(T - 1) / denom
    return float(_N.cdf(stat))


def expected_max_sharpe(trial_sharpes) -> float:
    """Expected maximum per-period Sharpe under the null (all true Sharpes = 0)
    across N independent trials — the deflation threshold. Needs the variance of
    the trial Sharpe estimates."""
    s = [x for x in trial_sharpes if x is not None and not math.isnan(x)]
    n = len(s)
    if n < 2:
        return 0.0
    var = float(np.var(s, ddof=1))
    if var <= 1e-12:            # no meaningful spread across trials
        return 0.0
    sqrt_v = math.sqrt(var)
    a = _N.inv_cdf(1 - 1.0 / n)
    b = _N.inv_cdf(1 - 1.0 / (n * math.e))
    return sqrt_v * ((1 - EULER) * a + EULER * b)


def deflated_sharpe_ratio(returns, trial_sharpes) -> dict | None:
    """Deflated Sharpe: P(true Sharpe > the multiple-testing threshold).
    Returns a dict with the probability and the (annualized) threshold, or None."""
    sr0 = expected_max_sharpe(trial_sharpes)
    dsr = probabilistic_sharpe_ratio(returns, sr0)
    if dsr is None:
        return None
    psr0 = probabilistic_sharpe_ratio(returns, 0.0)
    n = len([x for x in trial_sharpes if x is not None and not math.isnan(x)])
    ann = math.sqrt(252)
    return {
        "dsr": round(dsr, 4),               # P(true Sharpe > deflated threshold)
        "psr_vs_zero": round(psr0, 4),      # P(true Sharpe > 0), no deflation
        "sr0_annual": round(sr0 * ann, 3),  # deflated threshold, annualized
        "n_trials": n,
    }
