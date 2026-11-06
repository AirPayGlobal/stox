import numpy as np

from portfolio.research_stats import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    per_period_sharpe,
    probabilistic_sharpe_ratio,
)


def _returns(mu, sd, n=1000, seed=0):
    return list(np.random.default_rng(seed).normal(mu, sd, n))


def test_psr_high_for_strong_positive_sharpe():
    r = _returns(0.001, 0.005, n=2000, seed=1)   # per-period SR ~0.2, long T
    psr = probabilistic_sharpe_ratio(r, 0.0)
    assert psr > 0.99


def test_psr_half_at_own_sharpe():
    r = _returns(0.0005, 0.01, n=1500, seed=2)
    sr = per_period_sharpe(r)
    assert abs(probabilistic_sharpe_ratio(r, sr) - 0.5) < 0.02


def test_psr_low_when_benchmark_above_observed():
    r = _returns(0.0002, 0.01, n=1000, seed=3)
    sr = per_period_sharpe(r)
    assert probabilistic_sharpe_ratio(r, sr + 0.1) < 0.05


def test_expected_max_sharpe_increases_with_trials():
    rng = np.random.default_rng(4)
    few = list(rng.normal(0, 0.05, 3))
    many = list(rng.normal(0, 0.05, 50))
    assert expected_max_sharpe(many) >= expected_max_sharpe(few)


def test_expected_max_zero_for_degenerate():
    assert expected_max_sharpe([0.1]) == 0.0        # need >= 2 trials
    assert expected_max_sharpe([0.1, 0.1, 0.1]) == 0.0  # zero variance


def test_deflation_never_exceeds_undeflated_psr():
    r = _returns(0.0008, 0.008, n=1500, seed=5)
    trials = _returns(0.0, 0.05, n=20, seed=6)      # 20 trial Sharpes, positive variance
    d = deflated_sharpe_ratio(r, trials)
    assert 0.0 <= d["dsr"] <= d["psr_vs_zero"] <= 1.0
    assert d["n_trials"] == 20


def test_more_trials_lowers_dsr():
    r = _returns(0.0007, 0.008, n=1500, seed=7)
    v = _returns(0.0, 0.05, n=100, seed=8)
    d_few = deflated_sharpe_ratio(r, v[:3])
    d_many = deflated_sharpe_ratio(r, v)
    assert d_many["dsr"] <= d_few["dsr"] + 1e-9     # more trials -> harder bar


def test_short_track_returns_none():
    assert probabilistic_sharpe_ratio([0.01, 0.02]) is None
    assert deflated_sharpe_ratio([0.01, 0.02], [0.1, 0.2, 0.3]) is None
