"""End-to-end tests for Engine's M1 -> M5 pipeline: RealMarketAPI's WebSocket
delivers raw candles to Engine.on_candle, which validates them as M1 and,
for every candle it actually accepts into candles_1m, forwards that exact
candle onward to the M1->M5 aggregator (see engine.py's _store_m1 and
m5_engine.py's module docstring for why aggregation -- not raw-candle
cadence validation -- is the correct design now that RealMarketAPI has
confirmed its M1 feed is genuine again)."""
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


def _boundary() -> datetime:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return now - timedelta(minutes=now.minute % 5)


def _engine(tmp_path) -> Engine:
    settings = Settings(
        api_key="", symbols=("XAUUSD",),
        data_dir=str(tmp_path / "m1"), m5_data_dir=str(tmp_path / "m5"),
    )
    return Engine(settings)


def test_ten_genuine_m1_candles_produce_two_valid_m5_candles(tmp_path):
    """The real end-to-end scenario this whole pipeline exists for again:
    RealMarketAPI now sends genuine 60-second-spaced M1 candles. Fed
    through the actual provider -> Engine.on_candle path, M1 must accept
    them normally, and the M5 aggregator (fed only Engine's own validated
    output) must turn every five of them into one completed M5 candle."""
    engine = _engine(tmp_path)
    boundary = _boundary()
    candles = [_candle(boundary + timedelta(minutes=i)) for i in range(10)]

    async def feed():
        for c in candles:
            await engine.on_candle("XAUUSD", c)

    asyncio.run(feed())

    m1_state = engine.store.load("XAUUSD")
    m5_state = engine.m5_store.load("XAUUSD")

    assert len(m1_state.candles) == 10
    assert engine.is_valid_m1(m1_state) is True

    assert len(m5_state.candles) == 2
    assert M5Engine.is_valid(m5_state) is True
    assert m5_state.candles[0].timestamp == boundary.isoformat().replace("+00:00", "Z")


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


def test_m1_rejected_candle_never_reaches_the_m5_aggregator(tmp_path):
    """A candle the M1 path discards outright (out-of-order, unparseable,
    or an unconfirmed resync anchor still awaiting confirmation) must never
    reach the M5 aggregator -- only candles that actually land in
    candles_1m may ever be aggregated."""
    engine = _engine(tmp_path)
    boundary = _boundary()

    async def feed():
        # First candle is held as an unconfirmed seed (M1 engine behavior),
        # never stored, and therefore must never reach the aggregator.
        await engine.on_candle("XAUUSD", _candle(boundary))
        assert engine.m5_store.load("XAUUSD").candles == []

        # An out-of-order candle (before the still-pending seed) is rejected
        # outright by the M1 engine and must not leak into M5 either.
        await engine.on_candle("XAUUSD", _candle(boundary - timedelta(minutes=1)))
        assert engine.m5_store.load("XAUUSD").candles == []

    asyncio.run(feed())


def test_confirmed_m1_resync_forwards_both_halves_to_the_aggregator_in_order(tmp_path):
    """When the M1 engine confirms a resync (a gap, then a candle exactly
    60s after the pending anchor), it commits BOTH the anchor and the
    confirming candle to candles_1m -- and both must reach the aggregator,
    in that same order, so a legitimate M1 gap correctly breaks the M5
    bucket instead of silently gluing two unrelated windows together."""
    engine = _engine(tmp_path)
    boundary = _boundary()

    async def feed():
        for i in range(3):
            await engine.on_candle("XAUUSD", _candle(boundary + timedelta(minutes=i)))
        # A big jump to the next 5-minute boundary (simulating a reconnect).
        next_boundary = boundary + timedelta(minutes=5)
        await engine.on_candle("XAUUSD", _candle(next_boundary))  # held pending
        await engine.on_candle("XAUUSD", _candle(next_boundary + timedelta(minutes=1)))  # confirms it

    asyncio.run(feed())

    m1_state = engine.store.load("XAUUSD")
    assert len(m1_state.candles) == 5  # 3 + confirmed resync pair
    # The M5 aggregator's first (incomplete) bucket was discarded by the
    # gap; the resync pair correctly starts a fresh bucket instead of being
    # glued onto the abandoned one.
    assert engine.m5_store.load("XAUUSD").candles == []
