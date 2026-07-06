import math

from backtest.bs import bs_price


def test_call_intrinsic_at_expiry():
    assert bs_price(505, 500, 0.0, 0.2, "call") == 5.0
    assert bs_price(495, 500, 0.0, 0.2, "call") == 0.0


def test_put_intrinsic_at_expiry():
    assert bs_price(495, 500, 0.0, 0.2, "put") == 5.0
    assert bs_price(505, 500, 0.0, 0.2, "put") == 0.0


def test_atm_call_has_time_value():
    price = bs_price(500, 500, 1 / 365, 0.25, "call")
    assert 0.5 < price < 5.0


def test_put_call_parity():
    spot, strike, t, iv, r = 500.0, 502.0, 7 / 365, 0.3, 0.05
    call = bs_price(spot, strike, t, iv, "call", rate=r)
    put = bs_price(spot, strike, t, iv, "put", rate=r)
    parity = call - put - (spot - strike * math.exp(-r * t))
    assert abs(parity) < 1e-9


def test_deeper_itm_worth_more():
    otm = bs_price(500, 505, 1 / 365, 0.25, "call")
    itm = bs_price(500, 495, 1 / 365, 0.25, "call")
    assert itm > otm
