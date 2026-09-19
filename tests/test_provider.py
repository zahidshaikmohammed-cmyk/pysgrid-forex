from datetime import datetime, timedelta, timezone

from pysgrid_forex.config import Settings
from pysgrid_forex.models import Candle
from pysgrid_forex.provider import RealMarketAPI, _M1Accumulator


def test_ws_url_uses_configured_candles_endpoint():
    """Pins the URL the app actually builds. This is a consistency check on
    the code, not evidence that the endpoint delivers genuine M1 data --
    that can only be established by tools/verify_m1_provider.py against a
    real API key (see config.py's ws_base comment)."""
    s = Settings(api_key="secret", symbols=("XAUUSD",))
    p = RealMarketAPI(s, lambda *_: None)
    url = p._ws_url("XAUUSD")
    assert url.startswith("wss://api.realmarketapi.com/candles?")
    assert "symbolCode=XAUUSD" in url
    assert "timeFrame=M1" in url
    assert "apiKey=secret" in url


def test_extract_single():
    body = {
        "SymbolCode": "EURUSD",
        "OpenPrice": 1.1,
        "ClosePrice": 1.2,
        "HighPrice": 1.3,
        "LowPrice": 1.0,
        "Volume": 5,
        "Bid": 1.1999,
        "Ask": 1.2001,
        "OpenTime": "2026-01-01T00:00:00Z",
    }
    candles = RealMarketAPI._extract_candles(body)
    assert len(candles) == 1
    assert candles[0].close == 1.2
    assert candles[0].bid == 1.1999
    assert candles[0].ask == 1.2001


def test_forming_current_m1_is_rejected():
    now = datetime.now(timezone.utc).replace(second=10, microsecond=0)
    current_open = now.replace(second=0, microsecond=0)
    body = {
        "SymbolCode": "XAUUSD",
        "OpenPrice": 1.1,
        "ClosePrice": 1.2,
        "HighPrice": 1.3,
        "LowPrice": 1.0,
        "Volume": 5,
        "OpenTime": current_open.isoformat().replace("+00:00", "Z"),
    }
    assert RealMarketAPI._extract_candles(body) == []


def test_rest_five_minute_series_is_rejected():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=10)
    items = []
    for i in range(3):
        ts = now + timedelta(minutes=i * 5)
        items.append(
            {
                "SymbolCode": "XAUUSD",
                "OpenPrice": 1.0,
                "ClosePrice": 1.1,
                "HighPrice": 1.2,
                "LowPrice": 0.9,
                "Volume": 5,
                "OpenTime": ts.isoformat().replace("+00:00", "Z"),
            }
        )

    assert RealMarketAPI._extract_candles(
        {"data": items},
        completed_only=True,
        validate_series=True,
    ) == []


def _candle(ts: datetime, close: float, high: float | None = None, low: float | None = None, volume: float = 1) -> Candle:
    return Candle(
        timestamp=ts.isoformat().replace("+00:00", "Z"),
        open=close - 0.2,
        high=high if high is not None else close,
        low=low if low is not None else close - 0.5,
        close=close,
        volume=volume,
        bid=close - 0.1,
        ask=close + 0.1,
    )


def test_ws_updates_are_aggregated_into_completed_m1():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    acc = _M1Accumulator()

    assert acc.update(_candle(start, 100.0, high=100.2, low=99.8, volume=2)) is None
    assert acc.update(_candle(start + timedelta(seconds=20), 100.5, high=100.7, low=99.7, volume=7)) is None

    completed = acc.update(_candle(start + timedelta(minutes=1), 101.0, high=101.2, low=100.4, volume=3))

    assert completed is not None
    assert completed.timestamp == "2026-01-01T00:00:00Z"
    assert completed.open == 99.8
    assert completed.high == 100.7
    assert completed.low == 99.7
    assert completed.close == 100.5
    assert completed.volume == 7


def test_ws_five_minute_jump_is_not_fabricated_into_missing_m1_bars():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    acc = _M1Accumulator()
    acc.update(_candle(start, 100.0))

    completed = acc.update(_candle(start + timedelta(minutes=5), 105.0))

    assert completed is not None
    assert completed.timestamp == "2026-01-01T00:00:00Z"
    assert acc.current is not None
    assert acc.current.timestamp == "2026-01-01T00:05:00Z"
