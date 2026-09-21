"""Tests for CandleStore's period_seconds generalization (used by the M5
pipeline). test_store.py already covers the default (period_seconds=60,
i.e. M1) behavior exhaustively; these tests focus specifically on what
changes when a different period is used."""
from pathlib import Path

import pytest

from pysgrid_forex.models import Candle
from pysgrid_forex.store import CandleStore, NonSequentialCandleError


def _c(ts: str, base: float = 1.0) -> Candle:
    return Candle(ts, base, base + 1, base - 0.5, base + 0.5, 10)


def test_default_period_is_60_and_key_is_candles_1m(tmp_path: Path):
    store = CandleStore(str(tmp_path))
    assert store.period_seconds == 60
    assert store.candles_key == "candles_1m"


def test_m5_store_uses_300_second_period_and_candles_5m_key(tmp_path: Path):
    store = CandleStore(str(tmp_path), period_seconds=300)
    assert store.period_seconds == 300
    assert store.candles_key == "candles_5m"


def test_m5_store_accepts_300_second_spacing(tmp_path: Path):
    store = CandleStore(str(tmp_path), period_seconds=300)
    assert store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
    assert store.append_candle("XAUUSD", _c("2026-01-01T00:05:00Z"))
    assert store.append_candle("XAUUSD", _c("2026-01-01T00:10:00Z"))
    state = store.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [
        "2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z", "2026-01-01T00:10:00Z",
    ]


def test_m5_store_rejects_60_second_spacing():
    """The exact spacing that's valid for the M1 store must be rejected by
    an M5 store -- proves the period is actually enforced, not just labeled."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = CandleStore(tmp, period_seconds=300)
        store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
        with pytest.raises(NonSequentialCandleError):
            store.append_candle("XAUUSD", _c("2026-01-01T00:01:00Z"))  # 60s, not 300s


def test_m5_store_persists_under_candles_5m_key_on_disk(tmp_path: Path):
    store = CandleStore(str(tmp_path), period_seconds=300)
    store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))

    raw = (tmp_path / "XAUUSD.json").read_text()
    assert '"candles_5m":' in raw
    assert '"candles_1m":' not in raw


def test_m5_store_reload_round_trips_correctly(tmp_path: Path):
    store = CandleStore(str(tmp_path), period_seconds=300)
    store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
    store.append_candle("XAUUSD", _c("2026-01-01T00:05:00Z"))

    reloaded = CandleStore(str(tmp_path), period_seconds=300)
    state = reloaded.load("XAUUSD")
    assert [c.timestamp for c in state.candles] == [
        "2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z",
    ]


def test_m1_and_m5_stores_in_different_directories_do_not_collide(tmp_path: Path):
    m1_store = CandleStore(str(tmp_path / "m1"), period_seconds=60)
    m5_store = CandleStore(str(tmp_path / "m5"), period_seconds=300)

    m1_store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
    m1_store.append_candle("XAUUSD", _c("2026-01-01T00:01:00Z"))
    m5_store.append_candle("XAUUSD", _c("2026-01-01T00:00:00Z"))
    m5_store.append_candle("XAUUSD", _c("2026-01-01T00:05:00Z"))

    assert len(m1_store.load("XAUUSD").candles) == 2
    assert len(m5_store.load("XAUUSD").candles) == 2
