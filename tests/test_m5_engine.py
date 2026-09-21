"""Tests for M5Engine -- the M1->M5 aggregator. It consumes a stream of
already-validated M1 candles (as Engine.on_candle would feed it, one at a
time, in acceptance order) and emits a completed M5 candle only once five
contiguous, 5-minute-aligned M1 candles have arrived. See m5_engine.py's
module docstring for why this replaced the earlier raw-cadence design."""
from datetime import datetime, timedelta, timezone

import pytest

from pysgrid_forex.m5_engine import M5Engine, M5_SECONDS
from pysgrid_forex.models import Candle
from pysgrid_forex.store import CandleStore


def _candle(ts: datetime, *, open_: float = 100.0, high: float | None = None,
            low: float | None = None, close: float | None = None, volume: float = 10.0) -> Candle:
    return Candle(
        timestamp=ts.isoformat().replace("+00:00", "Z"),
        open=open_,
        high=high if high is not None else open_ + 0.5,
        low=low if low is not None else open_ - 0.5,
        close=close if close is not None else open_ + 0.1,
        volume=volume,
    )


def _boundary() -> datetime:
    """A timestamp exactly on a 5-minute boundary, whole minute, whole second."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return now - timedelta(minutes=now.minute % 5)


def _engine(tmp_path) -> tuple[M5Engine, CandleStore]:
    store = CandleStore(str(tmp_path), period_seconds=M5_SECONDS)
    return M5Engine(store), store


def _feed_m1_run(engine: M5Engine, symbol: str, start: datetime, count: int) -> list[Candle]:
    candles = [_candle(start + timedelta(minutes=i), open_=100.0 + i) for i in range(count)]
    for c in candles:
        engine.on_m1_candle(symbol, c)
    return candles


def test_m5_engine_rejects_a_store_with_the_wrong_period(tmp_path):
    wrong_store = CandleStore(str(tmp_path), period_seconds=60)
    with pytest.raises(ValueError):
        M5Engine(wrong_store)


def test_five_contiguous_m1_candles_produce_one_valid_m5_candle(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    _feed_m1_run(engine, "XAUUSD", boundary, 5)

    state = store.load("XAUUSD")
    assert len(state.candles) == 1
    assert state.candles[0].timestamp == boundary.isoformat().replace("+00:00", "Z")


def test_ten_contiguous_m1_candles_produce_two_valid_m5_candles(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    _feed_m1_run(engine, "XAUUSD", boundary, 10)

    state = store.load("XAUUSD")
    assert len(state.candles) == 2
    assert state.candles[0].timestamp == boundary.isoformat().replace("+00:00", "Z")
    assert state.candles[1].timestamp == (boundary + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")


def test_missing_m1_candle_produces_no_valid_m5_candle(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    candles = [_candle(boundary + timedelta(minutes=i), open_=100.0 + i) for i in range(5)]
    # Skip the 4th M1 candle (index 3) entirely -- a genuine gap.
    for i, c in enumerate(candles):
        if i == 3:
            continue
        engine.on_m1_candle("XAUUSD", c)

    state = store.load("XAUUSD")
    assert state.candles == []


def test_duplicate_m1_candle_updates_slot_in_place_and_still_completes(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    candles = [_candle(boundary + timedelta(minutes=i), open_=100.0 + i) for i in range(5)]
    for c in candles:
        engine.on_m1_candle("XAUUSD", c)
    # Re-deliver the last M1 candle with a revised close (idempotent correction).
    revised_last = _candle(boundary + timedelta(minutes=4), open_=104.0, close=999.0)
    engine.on_m1_candle("XAUUSD", revised_last)

    # The bucket already completed on the first delivery of candle 5, so the
    # late "correction" arrives after completion and is simply ignored --
    # it must not corrupt or reopen an already-emitted M5 candle.
    state = store.load("XAUUSD")
    assert len(state.candles) == 1
    assert state.candles[0].close == candles[-1].close


def test_duplicate_of_most_recent_pending_slot_updates_before_completion(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    candles = [_candle(boundary + timedelta(minutes=i), open_=100.0 + i) for i in range(4)]
    for c in candles:
        engine.on_m1_candle("XAUUSD", c)
    # Correct the 4th (still in-progress, not yet bucket-completing) candle.
    corrected = _candle(boundary + timedelta(minutes=3), open_=999.0, close=999.5)
    engine.on_m1_candle("XAUUSD", corrected)
    # Now complete the bucket.
    engine.on_m1_candle("XAUUSD", _candle(boundary + timedelta(minutes=4), open_=104.0))

    state = store.load("XAUUSD")
    assert len(state.candles) == 1
    assert state.candles[0].high == max([c.high for c in candles[:3]] + [corrected.high, 104.5])


def test_out_of_order_m1_candle_is_ignored_without_corrupting_bucket(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    candles = [_candle(boundary + timedelta(minutes=i), open_=100.0 + i) for i in range(5)]
    for c in candles:
        engine.on_m1_candle("XAUUSD", c)

    # An old, already-superseded candle for an earlier slot arrives late.
    stale = _candle(boundary + timedelta(minutes=1), open_=-999.0)
    engine.on_m1_candle("XAUUSD", stale)

    state = store.load("XAUUSD")
    assert len(state.candles) == 1
    assert state.candles[0].open == candles[0].open  # unaffected by the stale candle


def test_delayed_m1_candle_arriving_after_gap_discard_does_not_start_wrong_bucket(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    c0, c1, c2 = (_candle(boundary + timedelta(minutes=i), open_=100.0 + i) for i in range(3))
    c4 = _candle(boundary + timedelta(minutes=4), open_=104.0)  # arrives before c3 -- out of order

    engine.on_m1_candle("XAUUSD", c0)
    engine.on_m1_candle("XAUUSD", c1)
    engine.on_m1_candle("XAUUSD", c2)
    engine.on_m1_candle("XAUUSD", c4)  # breaks contiguity, bucket discarded, c4 not aligned -> waits

    c3_delayed = _candle(boundary + timedelta(minutes=3), open_=103.0)  # arrives even later
    engine.on_m1_candle("XAUUSD", c3_delayed)

    state = store.load("XAUUSD")
    assert state.candles == []  # no M5 candle fabricated from this broken window


def test_reconnect_resync_discards_incomplete_bucket_and_starts_fresh_at_next_boundary(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    # First 3 of a bucket arrive, then a reconnect causes a big jump to the
    # NEXT 5-minute boundary (mirrors Engine's confirmed-resync forwarding).
    for i in range(3):
        engine.on_m1_candle("XAUUSD", _candle(boundary + timedelta(minutes=i), open_=100.0 + i))

    next_boundary = boundary + timedelta(minutes=5)
    _feed_m1_run(engine, "XAUUSD", next_boundary, 5)

    state = store.load("XAUUSD")
    assert len(state.candles) == 1
    assert state.candles[0].timestamp == next_boundary.isoformat().replace("+00:00", "Z")
    assert state.gap_recoveries == 0  # first M5 candle ever for this symbol -- no "recovery" to count


def test_partial_bucket_produces_no_candle_until_the_fifth_arrives(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    for i in range(4):
        engine.on_m1_candle("XAUUSD", _candle(boundary + timedelta(minutes=i), open_=100.0 + i))
        assert store.load("XAUUSD").candles == []

    engine.on_m1_candle("XAUUSD", _candle(boundary + timedelta(minutes=4), open_=104.0))
    assert len(store.load("XAUUSD").candles) == 1


def test_timestamp_alignment_matches_the_bucket_start_not_an_arbitrary_slot(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    _feed_m1_run(engine, "XAUUSD", boundary, 5)

    state = store.load("XAUUSD")
    assert state.candles[0].timestamp == boundary.isoformat().replace("+00:00", "Z")


def test_ohlc_aggregation_is_open_first_high_max_low_min_close_last(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    candles = [
        _candle(boundary, open_=100.0, high=100.5, low=99.5, close=100.2),
        _candle(boundary + timedelta(minutes=1), open_=100.2, high=101.0, low=100.0, close=100.8),
        _candle(boundary + timedelta(minutes=2), open_=100.8, high=100.9, low=98.0, close=99.0),
        _candle(boundary + timedelta(minutes=3), open_=99.0, high=99.5, low=98.5, close=99.2),
        _candle(boundary + timedelta(minutes=4), open_=99.2, high=99.6, low=99.0, close=99.4),
    ]
    for c in candles:
        engine.on_m1_candle("XAUUSD", c)

    m5 = store.load("XAUUSD").candles[0]
    assert m5.open == 100.0
    assert m5.high == 101.0
    assert m5.low == 98.0
    assert m5.close == 99.4


def test_volume_aggregation_is_the_sum_of_the_five_m1_volumes(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    candles = [_candle(boundary + timedelta(minutes=i), open_=100.0 + i, volume=10.0 + i) for i in range(5)]
    for c in candles:
        engine.on_m1_candle("XAUUSD", c)

    m5 = store.load("XAUUSD").candles[0]
    assert m5.volume == sum(c.volume for c in candles) == 60.0


def test_stale_detection_mirrors_m1_but_at_the_m5_period(tmp_path):
    engine, store = _engine(tmp_path)
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _feed_m1_run(engine, "XAUUSD", old, 10)  # two full, ancient M5 buckets

    state = store.load("XAUUSD")
    assert len(state.candles) == 2
    assert M5Engine.is_valid(state) is False  # ancient timestamps, correctly stale


def test_fresh_valid_m5_series_reports_is_valid_true(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    _feed_m1_run(engine, "XAUUSD", boundary, 10)

    state = store.load("XAUUSD")
    assert M5Engine.is_valid(state) is True


def test_transition_between_m5_buckets_produces_correctly_spaced_candles(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    _feed_m1_run(engine, "XAUUSD", boundary, 15)  # three buckets

    state = store.load("XAUUSD")
    assert len(state.candles) == 3
    timestamps = [c.timestamp for c in state.candles]
    assert timestamps == [
        boundary.isoformat().replace("+00:00", "Z"),
        (boundary + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        (boundary + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
    ]


def test_provider_timestamp_anomaly_unparseable_is_discarded_without_crashing(tmp_path):
    engine, store = _engine(tmp_path)
    bad = Candle(timestamp="not-a-timestamp", open=1, high=2, low=0.5, close=1.5, volume=1)

    engine.on_m1_candle("XAUUSD", bad)  # must not raise

    state = store.load("XAUUSD")
    assert state.candles == []


def test_no_future_leakage_a_bucket_never_completes_before_its_fifth_candle_arrives(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    # Feed candles 0, 1, 2 only -- candle indices 3 and 4 never arrive.
    for i in range(3):
        engine.on_m1_candle("XAUUSD", _candle(boundary + timedelta(minutes=i), open_=100.0 + i))
        assert store.load("XAUUSD").candles == [], "no M5 candle may exist before the bucket is complete"


def test_no_fabricated_candles_across_a_long_gap(tmp_path):
    engine, store = _engine(tmp_path)
    boundary = _boundary()
    _feed_m1_run(engine, "XAUUSD", boundary, 5)  # one clean M5 candle

    # A multi-hour gap, then data resumes on a clean 5-minute boundary far later.
    later_boundary = boundary + timedelta(hours=3)
    _feed_m1_run(engine, "XAUUSD", later_boundary, 5)

    state = store.load("XAUUSD")
    # Exactly two genuine candles -- nothing fabricated to bridge the gap.
    assert len(state.candles) == 2
    assert state.gap_recoveries == 1
