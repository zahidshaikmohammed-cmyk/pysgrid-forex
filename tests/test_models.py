from pysgrid_forex.models import Candle


def test_provider_candle_normalizes():
    c = Candle.from_provider({
        "SymbolCode": "XAUUSD",
        "OpenPrice": 4378.953,
        "ClosePrice": 4378.821,
        "HighPrice": 4379.636,
        "LowPrice": 4378.249,
        "Volume": 185,
        "OpenTime": "2026-09-18T10:10:00.000Z",
    })
    assert c.timestamp == "2026-09-18T10:10:00Z"
    assert c.open == 4378.953
    assert c.volume == 185


def test_invalid_ohlc_rejected():
    try:
        Candle.from_provider({
            "OpenPrice": 10, "ClosePrice": 9, "HighPrice": 8,
            "LowPrice": 7, "Volume": 1, "OpenTime": "2026-01-01T00:00:00Z",
        })
    except ValueError:
        return
    assert False
