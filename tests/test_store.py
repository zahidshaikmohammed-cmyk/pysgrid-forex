from pathlib import Path

from pysgrid_forex.models import Candle
from pysgrid_forex.store import CandleStore


def test_store_deduplicates(tmp_path: Path):
    store = CandleStore(str(tmp_path), max_candles=2)
    c1 = Candle("2026-01-01T00:00:00Z", 1, 2, 0.5, 1.5, 10)
    c2 = Candle("2026-01-01T00:01:00Z", 1.5, 2.5, 1, 2, 20)
    c3 = Candle("2026-01-01T00:02:00Z", 2, 3, 1.5, 2.5, 30)
    assert store.upsert("XAUUSD", c1)
    assert not store.upsert("XAUUSD", c1)
    store.upsert("XAUUSD", c2)
    store.upsert("XAUUSD", c3)
    state = store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [c2.timestamp, c3.timestamp]


def test_old_schema_is_reset(tmp_path: Path):
    path = tmp_path / "XAUUSD.json"
    path.write_text(
        '{"status":"ok","candles_1m":[{"timestamp":"2026-01-01T00:00:00Z","open":1,"high":2,"low":1,"close":1.5,"volume":1}]}',
        encoding="utf-8",
    )
    store = CandleStore(str(tmp_path))
    state = store.load("XAUUSD")
    assert state.candles == []


def test_non_m1_schema3_series_is_reset(tmp_path: Path):
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
