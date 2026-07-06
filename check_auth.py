"""
Credential diagnostic — verifies trading API, market data API, and options
permissions in one go.

    python check_auth.py
"""
from config import Config


def main() -> None:
    print(f"Mode: {Config.ALPACA_MODE}")
    key = Config.ALPACA_API_KEY
    print(f"Key : {key[:4]}…{key[-4:]}" if len(key) > 8 else "Key : NOT SET")

    # 1. Trading API
    try:
        from trading.broker import get_account

        acct = get_account()
        print(
            f"✓ Trading API OK — equity ${acct['equity']:,.2f}, "
            f"options level {acct['options_level']}"
        )
        if acct["options_level"] < 1:
            print("  ⚠ Options trading NOT enabled — enable it in the Alpaca dashboard")
    except Exception as exc:
        print(f"✗ Trading API failed: {exc}")
        return

    # 2. Stock data API
    try:
        from data.market_data import get_latest_price

        price = get_latest_price("SPY")
        print(f"✓ Stock data OK — SPY last ${price:,.2f}")
    except Exception as exc:
        print(f"✗ Stock data failed: {exc}")

    # 3. Options data API
    try:
        from data.options_data import nearest_expiry

        expiry = nearest_expiry("SPY", max_dte=5)
        print(f"✓ Options data OK — nearest SPY expiry {expiry}")
    except Exception as exc:
        print(f"✗ Options data failed: {exc}")


if __name__ == "__main__":
    main()
