from datetime import datetime, timedelta, timezone

from pysgrid_forex.config import Settings
from pysgrid_forex.provider import RealMarketAPI


def test_ws_url_uses_documented_price_stream():
    s = Settings(api_key="secret", symbols=("XAUUSD",))
    p = RealMarketAPI(s, lambda *_: None)
    url = p._ws_url("XAUUSD")
    assert url.startswith("wss://api.realmarketapi.com/price?")
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
