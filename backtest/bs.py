"""Black-Scholes pricing (no dividends) — used to simulate option marks."""
from __future__ import annotations

import math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(
    spot: float,
    strike: float,
    t_years: float,
    iv: float,
    option_type: str,
    rate: float = 0.05,
) -> float:
    """Theoretical option price. `t_years` is time to expiry in years."""
    if t_years <= 0:
        # At/after expiry: intrinsic value.
        if option_type == "call":
            return max(spot - strike, 0.0)
        return max(strike - spot, 0.0)

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + iv**2 / 2) * t_years) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t

    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * t_years) * _norm_cdf(d2)
    return strike * math.exp(-rate * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
