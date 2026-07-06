from datetime import date

from data.options_data import OptionQuote, _parse_occ
from options.contracts import passes_liquidity, pick_contract


def quote(strike=500.0, bid=2.40, ask=2.50, delta=0.45, oi=500, opt_type="call"):
    return OptionQuote(
        symbol=f"SPY260706{'C' if opt_type == 'call' else 'P'}{int(strike*1000):08d}",
        underlying="SPY",
        option_type=opt_type,
        strike=strike,
        expiry=date(2026, 7, 6),
        bid=bid,
        ask=ask,
        delta=delta,
        implied_vol=0.2,
        open_interest=oi,
    )


def test_liquidity_pass():
    assert passes_liquidity(quote())


def test_liquidity_rejects_wide_spread():
    assert not passes_liquidity(quote(bid=2.00, ask=2.60))  # 26% spread


def test_liquidity_rejects_low_oi():
    assert not passes_liquidity(quote(oi=10))


def test_liquidity_rejects_no_bid():
    assert not passes_liquidity(quote(bid=0.0, ask=0.05))


def test_picks_closest_to_target_delta():
    chain = [
        quote(strike=498, delta=0.60),
        quote(strike=500, delta=0.45),
        quote(strike=502, delta=0.30),
    ]
    picked = pick_contract(chain, spot=500.0)
    assert picked.strike == 500


def test_falls_back_to_atm_without_greeks():
    chain = [
        quote(strike=495, delta=None),
        quote(strike=500, delta=None),
        quote(strike=505, delta=None),
    ]
    picked = pick_contract(chain, spot=501.0)
    assert picked.strike == 500


def test_returns_none_when_all_illiquid():
    chain = [quote(oi=0), quote(bid=0.0, ask=0.02)]
    assert pick_contract(chain, spot=500.0) is None


def test_parse_occ_symbol():
    strike, opt_type = _parse_occ("SPY260706C00620000")
    assert strike == 620.0
    assert opt_type == "call"
    strike, opt_type = _parse_occ("QQQ260706P00450500")
    assert strike == 450.5
    assert opt_type == "put"
