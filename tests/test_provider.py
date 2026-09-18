from pysgrid_forex.config import Settings
from pysgrid_forex.provider import RealMarketAPI


def test_ws_url():
    s = Settings(api_key="secret", symbols=("XAUUSD",))
    p = RealMarketAPI(s, lambda *_: None)
    url = p._ws_url("XAUUSD")
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
        "OpenTime": "2026-01-01T00:00:00Z",
    }
    candles = RealMarketAPI._extract_candles(body)
    assert len(candles) == 1
    assert candles[0].close == 1.2
