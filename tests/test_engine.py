import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pysgrid_forex.config import Settings
from pysgrid_forex.engine import Engine, M1_SECONDS
from pysgrid_forex.models import Candle, parse_timestamp


def _engine(tmp_path: Path, symbols=("XAUUSD",)) -> Engine:
    settings = Settings(api_key="", symbols=symbols, data_dir=str(tmp_path))
    return Engine(settings)


def _candle(ts: datetime, close: float = 100.0) -> Candle:
    return Candle(
        timestamp=ts.isoformat().replace("+00:00", "Z"),
        open=close - 0.2,
        high=close + 0.2,
        low=close - 0.5,
        close=close,
        volume=1,
    )


def _run(coro):
    return asyncio.run(coro)


async def _feed(engine: Engine, symbol: str, *candles: Candle) -> None:
    for c in candles:
        await engine.on_candle(symbol, c)


def _now() -> datetime:
    """Truncated to the minute so tests exercising is_valid_m1's freshness
    check (max_age_seconds=120) stay comfortably within tolerance."""
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def test_valid_1_minute_sequence_is_stored_and_marked_m1_valid(tmp_path):
    engine = _engine(tmp_path)
    now = _now()
    candles = [_candle(now - timedelta(minutes=3 - i)) for i in range(4)]
    _run(_feed(engine, "XAUUSD", *candles))

    state = engine.store.load("XAUUSD")
    # The seed candle is retroactively stored once the next one confirms it.
    assert [c.timestamp for c in state.candles] == [c.timestamp for c in candles]
    assert engine.is_valid_m1(state) is True


def test_duplicate_timestamp_is_idempotent(tmp_path):
    engine = _engine(tmp_path)
    now = _now()
    c0, c1 = _candle(now - timedelta(minutes=1)), _candle(now, close=101.0)
    _run(_feed(engine, "XAUUSD", c0, c1))

    resend = _candle(now, close=999.0)
    _run(engine.on_candle("XAUUSD", resend))

    state = engine.store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [c0.timestamp, c1.timestamp]
    assert state.candles[-1].close == 999.0
    assert state.rejected_count == 0


def test_out_of_order_candle_is_rejected_not_stored(tmp_path):
    engine = _engine(tmp_path)
    now = _now()
    c0, c1 = _candle(now - timedelta(minutes=1)), _candle(now)
    _run(_feed(engine, "XAUUSD", c0, c1))

    stale = _candle(now - timedelta(minutes=10))
    _run(engine.on_candle("XAUUSD", stale))

    state = engine.store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [c0.timestamp, c1.timestamp]
    assert state.rejected_count == 1


def test_2_minute_gap_is_not_silently_stored(tmp_path):
    engine = _engine(tmp_path)
    now = _now()
    c0, c1 = _candle(now - timedelta(minutes=1)), _candle(now)
    _run(_feed(engine, "XAUUSD", c0, c1))

    jump = _candle(now + timedelta(minutes=2))  # 2 minutes after c1
    _run(engine.on_candle("XAUUSD", jump))

    state = engine.store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [c0.timestamp, c1.timestamp]
    assert engine.is_valid_m1(state) is True
    assert engine._pending["XAUUSD"].timestamp == jump.timestamp


def test_5_minute_gap_is_not_silently_stored(tmp_path):
    engine = _engine(tmp_path)
    now = _now()
    c0, c1 = _candle(now - timedelta(minutes=1)), _candle(now)
    _run(_feed(engine, "XAUUSD", c0, c1))

    jump = _candle(now + timedelta(minutes=5))
    _run(engine.on_candle("XAUUSD", jump))

    state = engine.store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [c0.timestamp, c1.timestamp]


def test_larger_gap_is_not_silently_stored(tmp_path):
    engine = _engine(tmp_path)
    now = _now()
    c0, c1 = _candle(now - timedelta(minutes=1)), _candle(now)
    _run(_feed(engine, "XAUUSD", c0, c1))

    jump = _candle(now + timedelta(hours=3))
    _run(engine.on_candle("XAUUSD", jump))

    state = engine.store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [c0.timestamp, c1.timestamp]


def test_confirmed_resync_after_gap_appends_both_candles_and_counts_recovery(tmp_path):
    engine = _engine(tmp_path)
    now = _now()
    c0 = _candle(now - timedelta(minutes=7))
    c1 = _candle(now - timedelta(minutes=6))
    anchor = _candle(now - timedelta(minutes=1))   # 5-minute gap after c1
    confirm = _candle(now)                          # exactly 60s after anchor

    _run(_feed(engine, "XAUUSD", c0, c1, anchor, confirm))

    state = engine.store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [
        c0.timestamp, c1.timestamp, anchor.timestamp, confirm.timestamp,
    ]
    assert state.gap_recoveries == 1
    assert engine.is_valid_m1(state) is True


def test_unconfirmed_anchor_is_replaced_and_never_stored(tmp_path):
    """A pure non-M1 cadence (e.g. every update 5 minutes apart, matching the
    already-observed REST /candle mislabeling bug) must never accumulate into
    the store: each candle keeps replacing the anchor without ever being
    confirmed, so nothing beyond the original valid pair is ever persisted."""
    engine = _engine(tmp_path)
    now = _now()
    c0, c1 = _candle(now - timedelta(minutes=1)), _candle(now)
    _run(_feed(engine, "XAUUSD", c0, c1))

    five_minute_feed = [_candle(now + timedelta(minutes=5 * i)) for i in range(1, 6)]
    _run(_feed(engine, "XAUUSD", *five_minute_feed))

    state = engine.store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [c0.timestamp, c1.timestamp]
    assert state.rejected_count == len(five_minute_feed) - 1  # first just seeds the anchor


def test_first_ever_candle_is_held_pending_not_stored(tmp_path):
    engine = _engine(tmp_path)
    now = _now()
    only = _candle(now)
    _run(engine.on_candle("XAUUSD", only))

    state = engine.store.load("XAUUSD")
    assert state.candles == []
    assert engine._pending["XAUUSD"].timestamp == only.timestamp


def test_invalid_timestamp_is_rejected_without_crashing(tmp_path):
    engine = _engine(tmp_path)
    bad = Candle(timestamp="not-a-timestamp", open=1, high=2, low=0.5, close=1.5, volume=1)
    _run(engine.on_candle("XAUUSD", bad))

    state = engine.store.load("XAUUSD")
    assert state.candles == []
    assert state.rejected_count == 1
    assert "XAUUSD" not in engine._pending


def test_mixed_valid_and_invalid_history_only_tail_run_counts_as_live(tmp_path):
    engine = _engine(tmp_path)
    now = _now()
    c0 = _candle(now - timedelta(minutes=125))
    c1 = _candle(now - timedelta(minutes=124))
    anchor = _candle(now - timedelta(minutes=1))
    confirm = _candle(now)

    _run(_feed(engine, "XAUUSD", c0, c1, anchor, confirm))

    state = engine.store.load("XAUUSD")
    assert len(state.candles) == 4
    # The old (c0, c1) run and the new (anchor, confirm) run are both kept,
    # separated by a recorded, non-fabricated gap.
    gap = (parse_timestamp(anchor.timestamp) - parse_timestamp(c1.timestamp)).total_seconds()
    assert gap > M1_SECONDS
    assert engine.is_valid_m1(state) is True  # current tail run is fresh and valid


def test_runtime_engine_never_produces_corrupt_adjacent_pair_in_store(tmp_path):
    """End-to-end guarantee under a chaotic arrival order: every adjacent
    pair actually persisted in candles_1m is either exactly 60s apart (a
    normal continuation) or a gap that was explicitly confirmed and counted
    via gap_recoveries -- never a duplicate, a backwards step, or a silent,
    unrecorded jump."""
    engine = _engine(tmp_path)
    now = _now()
    arrivals = [
        _candle(now - timedelta(minutes=10)),
        _candle(now - timedelta(minutes=9)),
        _candle(now - timedelta(minutes=9)),              # duplicate
        _candle(now - timedelta(minutes=7)),               # 2-minute gap: held
        _candle(now - timedelta(minutes=2)),               # doesn't confirm: anchor replaced
        _candle(now - timedelta(minutes=1)),               # confirms the new anchor (a real gap)
        _candle(now - timedelta(minutes=1, seconds=30)),   # out of order vs tail: rejected
        _candle(now),
    ]
    _run(_feed(engine, "XAUUSD", *arrivals))

    state = engine.store.load("XAUUSD")
    assert len(state.candles) >= 2

    timestamps = [parse_timestamp(c.timestamp) for c in state.candles]
    assert timestamps == sorted(set(timestamps))  # strictly increasing, no duplicates

    non_contiguous_gaps = sum(
        1
        for a, b in zip(timestamps, timestamps[1:])
        if (b - a).total_seconds() != M1_SECONDS
    )
    assert non_contiguous_gaps == state.gap_recoveries


def test_health_reflects_per_symbol_m1_status(tmp_path):
    engine = _engine(tmp_path, symbols=("XAUUSD", "EURUSD"))
    now = _now()
    _run(_feed(engine, "XAUUSD", _candle(now - timedelta(minutes=1)), _candle(now)))

    status = engine.m1_status()
    assert status == {"XAUUSD": True, "EURUSD": False}
