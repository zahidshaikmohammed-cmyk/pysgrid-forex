"""Tests for M5Engine -- the same pending-anchor contiguity state machine as
Engine.on_candle in engine.py (see tests/test_engine.py for the exhaustive
version of these scenarios at the 60s period), parameterized at 300s.
These tests focus on proving the period itself is correctly 300s and that
M5Engine requires a properly-configured store; the state-machine logic
itself is the same design already proven at the M1 period."""
from datetime import datetime, timedelta, timezone

import pytest

from pysgrid_forex.m5_engine import M5Engine, M5_SECONDS
from pysgrid_forex.models import Candle
from pysgrid_forex.store import CandleStore


def _candle(ts: datetime, close: float = 100.0) -> Candle:
    return Candle(
        timestamp=ts.isoformat().replace("+00:00", "Z"),
        open=close - 0.2, high=close + 0.2, low=close - 0.5, close=close, volume=1,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def _engine(tmp_path) -> tuple[M5Engine, CandleStore]:
    store = CandleStore(str(tmp_path), period_seconds=M5_SECONDS)
    return M5Engine(store), store


def test_m5_engine_rejects_a_store_with_the_wrong_period(tmp_path):
    wrong_store = CandleStore(str(tmp_path), period_seconds=60)
    with pytest.raises(ValueError):
        M5Engine(wrong_store)


def test_first_candle_held_pending_second_confirms_both_stored(tmp_path):
    engine, store = _engine(tmp_path)
    now = _now()
    c0 = _candle(now - timedelta(minutes=5))
    c1 = _candle(now)

    import asyncio
    asyncio.run(engine.on_candle("XAUUSD", c0))
    state = store.load("XAUUSD")
    assert state.candles == []  # not stored yet, unconfirmed

    asyncio.run(engine.on_candle("XAUUSD", c1))
    state = store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [c0.timestamp, c1.timestamp]
    assert M5Engine.is_valid(state) is True


def test_120_second_gap_not_stored_until_confirmed(tmp_path):
    """300s is the valid M5 period; anything else (including a 2-minute
    gap, which would be a red flag for M1 too) must not be silently
    accepted."""
    engine, store = _engine(tmp_path)
    now = _now()
    c0 = _candle(now - timedelta(minutes=5))
    c1 = _candle(now)

    import asyncio
    asyncio.run(engine.on_candle("XAUUSD", c0))
    asyncio.run(engine.on_candle("XAUUSD", c1))

    bad_gap = _candle(now + timedelta(minutes=2))  # 120s, not 300s
    asyncio.run(engine.on_candle("XAUUSD", bad_gap))

    state = store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [c0.timestamp, c1.timestamp]
    assert "XAUUSD" in engine._pending


def test_confirmed_resync_after_a_larger_gap_appends_both_and_counts_recovery(tmp_path):
    engine, store = _engine(tmp_path)
    now = _now()
    c0 = _candle(now - timedelta(minutes=20))
    c1 = _candle(now - timedelta(minutes=15))
    anchor = _candle(now - timedelta(minutes=5))  # a 600s gap after c1, not a genuine period
    confirm = _candle(now)  # exactly 300s after anchor

    import asyncio
    for c in (c0, c1, anchor, confirm):
        asyncio.run(engine.on_candle("XAUUSD", c))

    state = store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [
        c0.timestamp, c1.timestamp, anchor.timestamp, confirm.timestamp,
    ]
    assert state.gap_recoveries == 1
    assert M5Engine.is_valid(state) is True


def test_invalid_timestamp_rejected_without_crashing(tmp_path):
    engine, store = _engine(tmp_path)
    bad = Candle(timestamp="not-a-timestamp", open=1, high=2, low=0.5, close=1.5, volume=1)

    import asyncio
    asyncio.run(engine.on_candle("XAUUSD", bad))

    state = store.load("XAUUSD")
    assert state.candles == []
    assert state.rejected_count == 1


def test_is_valid_false_when_stale_beyond_max_age(tmp_path):
    engine, store = _engine(tmp_path)
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    c0 = _candle(old)
    c1 = _candle(old + timedelta(minutes=5))

    import asyncio
    asyncio.run(engine.on_candle("XAUUSD", c0))
    asyncio.run(engine.on_candle("XAUUSD", c1))

    state = store.load("XAUUSD")
    assert M5Engine.is_valid(state) is False  # ancient timestamps, correctly stale
