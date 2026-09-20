import httpx

from signal_engine.config import EngineConfig
from signal_engine.feed_client import FeedClient


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_symbol_parses_candles_and_m1_valid_flag():
    body = {
        "symbol": "XAUUSD",
        "status": "ok",
        "m1_valid": True,
        "candles_1m": [
            {"timestamp": "2026-01-01T00:01:00Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"timestamp": "2026-01-01T00:00:00Z", "open": 1, "high": 1.5, "low": 0.5, "close": 1, "volume": 5},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public/XAUUSD.json"
        return httpx.Response(200, json=body)

    config = EngineConfig(api_base="http://testserver")
    with FeedClient(config, client=_client(handler)) as feed:
        snapshot = feed.fetch_symbol("XAUUSD")

    assert snapshot is not None
    assert snapshot.m1_valid is True
    assert snapshot.status == "ok"
    # out-of-order response body must come back time-sorted
    assert [c.timestamp for c in snapshot.candles] == [
        "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z",
    ]


def test_fetch_symbol_returns_none_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    config = EngineConfig(api_base="http://testserver")
    with FeedClient(config, client=_client(handler)) as feed:
        assert feed.fetch_symbol("XAUUSD") is None


def test_fetch_symbol_skips_malformed_candle_entries():
    body = {"status": "ok", "m1_valid": True, "candles_1m": [{"timestamp": "bad", "open": "x"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    config = EngineConfig(api_base="http://testserver")
    with FeedClient(config, client=_client(handler)) as feed:
        snapshot = feed.fetch_symbol("XAUUSD")

    assert snapshot is not None
    assert snapshot.candles == []


def test_fetch_all_only_includes_symbols_that_responded():
    def handler(request: httpx.Request) -> httpx.Response:
        if "EURUSD" in request.url.path:
            return httpx.Response(500, text="down")
        return httpx.Response(200, json={"status": "ok", "m1_valid": False, "candles_1m": []})

    config = EngineConfig(api_base="http://testserver", symbols=("XAUUSD", "EURUSD"))
    with FeedClient(config, client=_client(handler)) as feed:
        snapshots = feed.fetch_all()

    assert set(snapshots) == {"XAUUSD"}
