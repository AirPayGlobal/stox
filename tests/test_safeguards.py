from trading.safeguards import DedupeStore, data_is_stale, entry_blocks


def _blocks(**over):
    kw = dict(
        data_age_seconds=10.0, max_age_seconds=120.0,
        unmanaged_shares={}, block_on_shares=True,
        recon_mismatch=False, block_on_recon=True,
        inflight_orders=set(),
    )
    kw.update(over)
    return entry_blocks(**kw)


def test_all_clear_allows_entry():
    assert _blocks() == {}


def test_stale_data_blocks():
    assert "stale_data" in _blocks(data_age_seconds=300.0)
    assert data_is_stale(None, 120.0) is False       # unknown age is not stale
    assert data_is_stale(200.0, 120.0) is True


def test_unmanaged_shares_block_respects_flag():
    shares = {"CVR": {"qty": 100}}
    assert "unmanaged_shares" in _blocks(unmanaged_shares=shares)
    assert _blocks(unmanaged_shares=shares, block_on_shares=False) == {}


def test_recon_mismatch_block_respects_flag():
    assert "reconciliation" in _blocks(recon_mismatch=True)
    assert _blocks(recon_mismatch=True, block_on_recon=False) == {}


def test_inflight_orders_block():
    assert "inflight_orders" in _blocks(inflight_orders={"SPY260101C00500000"})


def test_multiple_blocks_reported_together():
    b = _blocks(data_age_seconds=999.0, inflight_orders={"x"})
    assert {"stale_data", "inflight_orders"} <= set(b)


def test_dedupe_store_persists_and_reloads(tmp_path):
    path = str(tmp_path / "acted.json")
    store = DedupeStore(path)
    assert "SPY|3-9" not in store
    store.add("SPY|3-9")
    assert "SPY|3-9" in store
    # A fresh instance (e.g. after restart) reloads today's keys.
    reloaded = DedupeStore(path)
    assert "SPY|3-9" in reloaded


def test_dedupe_store_resets_on_new_day(tmp_path):
    import json
    path = str(tmp_path / "acted.json")
    with open(path, "w") as f:
        json.dump({"day": "2000-01-01", "keys": ["OLD|1-2"]}, f)
    store = DedupeStore(path)
    assert "OLD|1-2" not in store        # stale day discarded
