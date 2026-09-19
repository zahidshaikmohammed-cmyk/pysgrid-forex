from pathlib import Path

import pytest

from pysgrid_forex.models import Candle
from pysgrid_forex.store import CandleStore, NonSequentialCandleError


def _c(ts: str, base: float = 1.0) -> Candle:
    return Candle(ts, base, base + 1, base - 0.5, base + 0.5, 10)


def test_append_candle_accepts_contiguous_sequence(tmp_path: Path):
    store = CandleStore(str(tmp_path), max_candles=10)
    assert store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
    assert store.append_candle("XAUUSD", _c("2026-01-01T00:01:00Z"))
    assert store.append_candle("XAUUSD", _c("2026-01-01T00:02:00Z"))
    state = store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [
        "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", "2026-01-01T00:02:00Z",
    ]


def test_append_candle_max_candles_trims_oldest(tmp_path: Path):
    store = CandleStore(str(tmp_path), max_candles=2)
    c1, c2, c3 = _c("2026-01-01T00:00:00Z"), _c("2026-01-01T00:01:00Z"), _c("2026-01-01T00:02:00Z")
    assert store.append_candle("XAUUSD", c1)
    assert not store.append_candle("XAUUSD", c1)
    assert store.append_candle("XAUUSD", c2)
    assert store.append_candle("XAUUSD", c3)
    state = store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [c2.timestamp, c3.timestamp]


def test_append_candle_rejects_duplicate_older_timestamp(tmp_path: Path):
    store = CandleStore(str(tmp_path))
    store.append_candle("XAUUSD", _c("2026-01-01T00:01:00Z"))
    with pytest.raises(NonSequentialCandleError):
        store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
    assert [c.timestamp for c in store.load("XAUUSD").candles] == ["2026-01-01T00:01:00Z"]


def test_append_candle_idempotent_same_timestamp_update(tmp_path: Path):
    store = CandleStore(str(tmp_path))
    store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z", base=1.0))
    changed = store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z", base=2.0))
    assert changed
    state = store.load("XAUUSD")
    assert len(state.candles) == 1
    assert state.candles[0].open == 2.0


def test_append_candle_rejects_2_minute_gap_without_allow_gap(tmp_path: Path):
    store = CandleStore(str(tmp_path))
    store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
    with pytest.raises(NonSequentialCandleError):
        store.append_candle("XAUUSD", _c("2026-01-01T00:02:00Z"))
    assert len(store.load("XAUUSD").candles) == 1


def test_append_candle_rejects_5_minute_gap_without_allow_gap(tmp_path: Path):
    store = CandleStore(str(tmp_path))
    store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
    with pytest.raises(NonSequentialCandleError):
        store.append_candle("XAUUSD", _c("2026-01-01T00:05:00Z"))
    assert len(store.load("XAUUSD").candles) == 1


def test_append_candle_rejects_large_gap_without_allow_gap(tmp_path: Path):
    store = CandleStore(str(tmp_path))
    store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
    with pytest.raises(NonSequentialCandleError):
        store.append_candle("XAUUSD", _c("2026-01-01T05:00:00Z"))
    assert len(store.load("XAUUSD").candles) == 1


def test_append_candle_allows_gap_when_explicitly_confirmed(tmp_path: Path):
    store = CandleStore(str(tmp_path))
    store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
    assert store.append_candle("XAUUSD", _c("2026-01-01T00:05:00Z"), allow_gap=True)
    state = store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [
        "2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z",
    ]


def test_rejected_bad_candle_does_not_destroy_existing_history(tmp_path: Path):
    """A single invalid incoming candle must not wipe healthy prior history."""
    store = CandleStore(str(tmp_path))
    for i in range(5):
        store.append_candle("XAUUSD", _c(f"2026-01-01T00:0{i}:00Z"))
    before = [c.timestamp for c in store.load("XAUUSD").candles]

    with pytest.raises(NonSequentialCandleError):
        store.append_candle("XAUUSD", _c("2026-01-01T00:37:00Z"))

    after = [c.timestamp for c in store.load("XAUUSD").candles]
    assert after == before
    assert len(after) == 5


def test_old_schema_is_reset(tmp_path: Path):
    path = tmp_path / "XAUUSD.json"
    path.write_text(
        '{"status":"ok","candles_1m":[{"timestamp":"2026-01-01T00:00:00Z","open":1,"high":2,"low":1,"close":1.5,"volume":1}]}',
        encoding="utf-8",
    )
    store = CandleStore(str(tmp_path))
    state = store.load("XAUUSD")
    assert state.candles == []


def test_pre_v4_schema_series_is_reset_even_if_internally_consistent(tmp_path: Path):
    """Data written before write-time contiguity enforcement existed is never
    trusted, even if it looks fine, because nothing validated it when it was
    written."""
    path = tmp_path / "XAUUSD.json"
    path.write_text(
        '{"data_schema_version":3,"status":"ok","candles_1m":['
        '{"timestamp":"2026-01-01T00:00:00Z","open":1,"high":2,"low":1,"close":1.5,"volume":1},'
        '{"timestamp":"2026-01-01T00:05:00Z","open":1.5,"high":2.5,"low":1,"close":2,"volume":2}'
        ']}',
        encoding="utf-8",
    )
    store = CandleStore(str(tmp_path))
    state = store.load("XAUUSD")
    assert state.candles == []


def test_load_tolerates_legitimate_gaps_in_v4_history(tmp_path: Path):
    """A real gap (e.g. weekend closure) recorded by the current schema must
    not be wiped just because it isn't spaced at exactly 60 seconds -- only
    genuinely impossible (non-monotonic) series get discarded."""
    store = CandleStore(str(tmp_path))
    store.append_candle("XAUUSD", _c("2026-01-02T00:00:00Z"))
    store.append_candle("XAUUSD", _c("2026-01-05T00:00:00Z"), allow_gap=True)
    store.append_candle("XAUUSD", _c("2026-01-05T00:01:00Z"))

    reloaded = CandleStore(str(tmp_path))
    state = reloaded.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [
        "2026-01-02T00:00:00Z", "2026-01-05T00:00:00Z", "2026-01-05T00:01:00Z",
    ]


def test_load_discards_non_monotonic_v4_series(tmp_path: Path):
    path = tmp_path / "XAUUSD.json"
    path.write_text(
        '{"data_schema_version":4,"status":"ok","candles_1m":['
        '{"timestamp":"2026-01-01T00:01:00Z","open":1,"high":2,"low":1,"close":1.5,"volume":1},'
        '{"timestamp":"2026-01-01T00:00:00Z","open":1.5,"high":2.5,"low":1,"close":2,"volume":2}'
        ']}',
        encoding="utf-8",
    )
    store = CandleStore(str(tmp_path))
    state = store.load("XAUUSD")
    assert state.candles == []
