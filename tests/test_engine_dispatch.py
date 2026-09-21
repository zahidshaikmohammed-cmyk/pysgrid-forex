"""Tests for Engine's dispatch to both the M1 and M5 acceptance paths from
a single stream of raw candles -- the actual point of the M5 pipeline: the
same provider connection and the same raw candles feed both, and each
independently decides whether to trust them for its own period."""
import asyncio
from datetime import datetime, timedelta, timezone

from pysgrid_forex.config import Settings
from pysgrid_forex.engine import Engine
from pysgrid_forex.m5_engine import M5Engine
from pysgrid_forex.models import Candle


def _candle(ts: datetime, close: float = 100.0) -> Candle:
    return Candle(
        timestamp=ts.isoformat().replace("+00:00", "Z"),
        open=close - 0.2, high=close + 0.2, low=close - 0.5, close=close, volume=1,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def _engine(tmp_path) -> Engine:
    settings = Settings(
        api_key="", symbols=("XAUUSD",),
        data_dir=str(tmp_path / "m1"), m5_data_dir=str(tmp_path / "m5"),
    )
    return Engine(settings)


def test_dispatch_feeds_both_pipelines_from_the_same_raw_candle_stream(tmp_path):
    """Reproduces the real-world finding this whole pipeline exists for:
    the provider actually sends candles 300s apart. Fed through _dispatch,
    the M1 path must keep correctly rejecting them (m1_valid stays False)
    while the M5 path correctly accepts and validates them."""
    engine = _engine(tmp_path)
    now = _now()
    candles = [_candle(now - timedelta(minutes=5 * i)) for i in range(3, -1, -1)]  # 300s apart, oldest first

    async def feed():
        for c in candles:
            await engine._dispatch("XAUUSD", c)

    asyncio.run(feed())

    m1_state = engine.store.load("XAUUSD")
    m5_state = engine.m5_store.load("XAUUSD")

    # M1 store: nothing is ever confirmed, since no two candles are 60s apart.
    assert m1_state.candles == []
    assert engine.is_valid_m1(m1_state) is False

    # M5 store: same raw candles, correctly recognized as a genuine 300s cadence.
    assert len(m5_state.candles) >= 2
    assert M5Engine.is_valid(m5_state) is True


def test_on_status_mirrors_connection_state_to_both_stores(tmp_path):
    engine = _engine(tmp_path)

    asyncio.run(engine.on_status("XAUUSD", True))
    assert engine.store.load("XAUUSD").websocket_connected is True
    assert engine.m5_store.load("XAUUSD").websocket_connected is True

    asyncio.run(engine.on_status("XAUUSD", False))
    assert engine.store.load("XAUUSD").websocket_connected is False
    assert engine.m5_store.load("XAUUSD").websocket_connected is False
    assert engine.store.load("XAUUSD").reconnect_count == 1
    assert engine.m5_store.load("XAUUSD").reconnect_count == 1


def test_m1_rejection_does_not_block_m5_acceptance_of_the_same_candle(tmp_path):
    """A candle the M1 path discards outright (e.g. a 300s jump treated as
    an unconfirmed anchor) must still reach the M5 path independently --
    _dispatch must not let one path's decision short-circuit the other."""
    engine = _engine(tmp_path)
    now = _now()

    async def feed():
        await engine._dispatch("XAUUSD", _candle(now - timedelta(minutes=10)))
        await engine._dispatch("XAUUSD", _candle(now - timedelta(minutes=5)))
        await engine._dispatch("XAUUSD", _candle(now))

    asyncio.run(feed())

    m5_state = engine.m5_store.load("XAUUSD")
    assert len(m5_state.candles) == 3
    assert M5Engine.is_valid(m5_state) is True
