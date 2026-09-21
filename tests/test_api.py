import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from pysgrid_forex import api
from pysgrid_forex.config import Settings
from pysgrid_forex.engine import Engine
from pysgrid_forex.models import Candle


def _candle(ts: datetime, close: float = 100.0) -> Candle:
    return Candle(
        timestamp=ts.isoformat().replace("+00:00", "Z"),
        open=close - 0.2, high=close + 0.2, low=close - 0.5, close=close, volume=1,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def _wire_engine(monkeypatch, tmp_path: Path, symbols):
    settings = Settings(
        api_key="", symbols=symbols,
        data_dir=str(tmp_path / "m1"), m5_data_dir=str(tmp_path / "m5"),
    )
    engine = Engine(settings)
    monkeypatch.setattr(api, "engine", engine)
    monkeypatch.setattr(api, "settings", settings)
    return engine


def test_health_exposes_per_symbol_m1_status_and_all_m1_live_false(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD", "EURUSD"))
    now = _now()
    engine.store.append_candle("XAUUSD", _candle(now - timedelta(minutes=1)))
    engine.store.append_candle("XAUUSD", _candle(now))
    # EURUSD has no data at all.

    health = asyncio.run(api.health())

    assert health["symbol_count"] == 2
    assert health["m1_status"] == {"XAUUSD": True, "EURUSD": False}
    assert health["m1_live_symbols"] == 1
    assert health["all_m1_live"] is False


def test_health_all_m1_live_true_only_when_every_symbol_is_valid(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD", "EURUSD"))
    now = _now()
    for symbol in ("XAUUSD", "EURUSD"):
        engine.store.append_candle(symbol, _candle(now - timedelta(minutes=1)))
        engine.store.append_candle(symbol, _candle(now))

    health = asyncio.run(api.health())

    assert health["m1_status"] == {"XAUUSD": True, "EURUSD": True}
    assert health["m1_live_symbols"] == 2
    assert health["all_m1_live"] is True


def test_bulk_live_feed_exposes_m1_valid_per_symbol(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD", "EURUSD"))
    now = _now()
    engine.store.append_candle("XAUUSD", _candle(now - timedelta(minutes=1)))
    engine.store.append_candle("XAUUSD", _candle(now))

    response = asyncio.run(api.live())
    body = response.body.decode()
    import json
    payload = json.loads(body)

    assert payload["symbols"]["XAUUSD"]["m1_valid"] is True
    assert payload["symbols"]["EURUSD"]["m1_valid"] is False


def test_symbol_payload_exposes_m1_valid(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.store.append_candle("XAUUSD", _candle(now - timedelta(minutes=1)))
    engine.store.append_candle("XAUUSD", _candle(now))

    payload = api._symbol_payload("XAUUSD")
    assert payload["m1_valid"] is True
    assert len(payload["candles_1m"]) == 2


def test_health_all_m1_live_is_shell_gate_parseable(tmp_path, monkeypatch):
    """The deploy workflow greps `"all_m1_live":[a-z]*` out of the raw JSON
    body; this pins the serialized shape so a future refactor can't silently
    make the deploy gate always find a mismatch (and either always pass, or
    never pass)."""
    import json
    import re

    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.store.append_candle("XAUUSD", _candle(now - timedelta(minutes=1)))
    engine.store.append_candle("XAUUSD", _candle(now))

    # Match Starlette's actual JSONResponse wire format (no spaces), since
    # that's what the deploy workflow's curl + grep sees in production.
    body = json.dumps(asyncio.run(api.health()), separators=(",", ":"))
    match = re.search(r'"all_m1_live":([a-z]*)', body)
    assert match is not None
    assert match.group(1) == "true"


def test_health_exposes_per_symbol_m5_status_and_all_m5_live(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD", "EURUSD"))
    now = _now()
    engine.m5_store.append_candle("XAUUSD", _candle(now - timedelta(minutes=5)))
    engine.m5_store.append_candle("XAUUSD", _candle(now))
    # EURUSD has no M5 data at all.

    health = asyncio.run(api.health())

    assert health["m5_status"] == {"XAUUSD": True, "EURUSD": False}
    assert health["m5_live_symbols"] == 1
    assert health["all_m5_live"] is False


def test_metrics_exposes_symbols_m5_section(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.m5_store.append_candle("XAUUSD", _candle(now - timedelta(minutes=5)))
    engine.m5_store.append_candle("XAUUSD", _candle(now))

    metrics = asyncio.run(api.metrics())

    assert metrics["symbols_m5"]["XAUUSD"]["m5_valid"] is True
    assert metrics["symbols_m5"]["XAUUSD"]["candle_count"] == 2


def test_bulk_m5_live_feed_exposes_m5_valid_per_symbol(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD", "EURUSD"))
    now = _now()
    engine.m5_store.append_candle("XAUUSD", _candle(now - timedelta(minutes=5)))
    engine.m5_store.append_candle("XAUUSD", _candle(now))

    response = asyncio.run(api.m5_live())
    import json
    payload = json.loads(response.body.decode())

    assert payload["timeframe"] == "M5"
    assert payload["symbols"]["XAUUSD"]["m5_valid"] is True
    assert payload["symbols"]["XAUUSD"]["candles_5m"]
    assert payload["symbols"]["EURUSD"]["m5_valid"] is False


def test_m5_forex_excludes_metals_and_oil(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD", "EURUSD"))
    now = _now()
    for symbol in ("XAUUSD", "EURUSD"):
        engine.m5_store.append_candle(symbol, _candle(now - timedelta(minutes=5)))
        engine.m5_store.append_candle(symbol, _candle(now))

    response = asyncio.run(api.m5_forex())
    import json
    payload = json.loads(response.body.decode())

    assert "EURUSD" in payload["symbols"]
    assert "XAUUSD" not in payload["symbols"]


def test_symbol_m5_payload_exposes_m5_valid_and_candles(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.m5_store.append_candle("XAUUSD", _candle(now - timedelta(minutes=5)))
    engine.m5_store.append_candle("XAUUSD", _candle(now))

    payload = api._symbol_payload_m5("XAUUSD")
    assert payload["timeframe"] == "M5"
    assert payload["m5_valid"] is True
    assert len(payload["candles_5m"]) == 2


def test_m5_live_and_m5_forex_routes_are_reachable_over_real_http(tmp_path, monkeypatch):
    """Regression test for a real production bug: /public/{symbol}.json is a
    single-segment catch-all, so if the m5-live.json/m5-forex.json routes
    were declared AFTER it, FastAPI/Starlette (which matches routes in
    registration order) would swallow requests for them as symbol="m5-live"
    / "m5-forex" and return 404 "symbol not configured" -- exactly what
    happened when this shipped. Calling the handler functions directly (as
    the other tests in this file do) can never catch this class of bug,
    since it never goes through actual URL routing -- only a real HTTP
    request against the app does."""
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.m5_store.append_candle("XAUUSD", _candle(now - timedelta(minutes=5)))
    engine.m5_store.append_candle("XAUUSD", _candle(now))

    client = TestClient(api.app)

    live_response = client.get("/public/m5-live.json")
    assert live_response.status_code == 200
    assert live_response.json()["timeframe"] == "M5"
    assert live_response.json()["symbols"]["XAUUSD"]["m5_valid"] is True

    forex_response = client.get("/public/m5-forex.json")
    assert forex_response.status_code == 200
    assert forex_response.json()["timeframe"] == "M5"

    symbol_response = client.get("/public/m5/XAUUSD.json")
    assert symbol_response.status_code == 200
    assert symbol_response.json()["m5_valid"] is True

    # The M1 catch-all route must still resolve real symbols correctly too.
    m1_symbol_response = client.get("/public/XAUUSD.json")
    assert m1_symbol_response.status_code == 200
    assert m1_symbol_response.json()["symbol"] == "XAUUSD"


def test_m1_and_m5_pipelines_are_independent_in_the_api(tmp_path, monkeypatch):
    """The M1 endpoints must keep reporting exactly what they did before --
    M5 data landing in the parallel store must never leak into or influence
    the M1 response."""
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.m5_store.append_candle("XAUUSD", _candle(now - timedelta(minutes=5)))
    engine.m5_store.append_candle("XAUUSD", _candle(now))

    health = asyncio.run(api.health())
    assert health["m1_status"] == {"XAUUSD": False}
    assert health["m5_status"] == {"XAUUSD": True}


def test_m1_live_returns_200_with_m1_timeframe_and_provider_native_marker(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.store.append_candle("XAUUSD", _candle(now - timedelta(minutes=1)))
    engine.store.append_candle("XAUUSD", _candle(now))

    client = TestClient(api.app)
    response = client.get("/public/m1-live.json")

    assert response.status_code == 200
    body = response.json()
    assert body["timeframe"] == "M1"
    assert body["candle_source"] == "provider_native"
    assert body["synthetic_candles"] is False


def test_m1_live_includes_every_configured_instrument(tmp_path, monkeypatch):
    symbols = ("XAUUSD", "EURUSD", "GBPUSD")
    engine = _wire_engine(monkeypatch, tmp_path, symbols)
    now = _now()
    engine.store.append_candle("XAUUSD", _candle(now - timedelta(minutes=1)))
    engine.store.append_candle("XAUUSD", _candle(now))

    client = TestClient(api.app)
    body = client.get("/public/m1-live.json").json()

    assert set(body["symbols"].keys()) == set(symbols)
    assert body["universe_size"] == 3
    # EURUSD/GBPUSD have no data yet -- present, but correctly not valid.
    assert body["symbols"]["EURUSD"]["m1_valid"] is False
    assert body["symbols"]["EURUSD"]["candles_1m"] == []


def test_m1_live_preserves_ohlcv_values_exactly_from_the_provider(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    c0 = Candle(timestamp=(now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                open=1900.11, high=1901.22, low=1899.33, close=1900.44, volume=123.5)
    c1 = Candle(timestamp=now.isoformat().replace("+00:00", "Z"),
                open=1900.44, high=1902.0, low=1900.0, close=1901.5, volume=456.75)
    engine.store.append_candle("XAUUSD", c0)
    engine.store.append_candle("XAUUSD", c1)

    client = TestClient(api.app)
    candles = client.get("/public/m1-live.json").json()["symbols"]["XAUUSD"]["candles_1m"]

    assert candles == [c0.as_dict(), c1.as_dict()]


def test_m1_live_never_exposes_m5_candles_as_m1(tmp_path, monkeypatch):
    """No M5 candle may ever be converted into, or leak as, an M1 candle --
    /public/m1-live.json must only ever reflect engine.store, never
    engine.m5_store, regardless of what the M5 pipeline has accumulated."""
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.m5_store.append_candle("XAUUSD", _candle(now - timedelta(minutes=5)))
    engine.m5_store.append_candle("XAUUSD", _candle(now))
    # M1 store deliberately left empty.

    client = TestClient(api.app)
    body = client.get("/public/m1-live.json").json()

    assert body["symbols"]["XAUUSD"]["candles_1m"] == []
    assert body["symbols"]["XAUUSD"]["m1_valid"] is False


def test_m1_live_does_not_fabricate_missing_candles_across_a_gap(tmp_path, monkeypatch):
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    base = _now() - timedelta(minutes=10)
    engine.store.append_candle("XAUUSD", _candle(base))
    engine.store.append_candle("XAUUSD", _candle(base + timedelta(minutes=1)))  # normal 60s continuation
    # Minutes 2, 3, and 4 are never received -- a genuine gap follows,
    # appended with allow_gap exactly as the engine's own confirmed-resync
    # path would do. No candle is fabricated for the missing minutes.
    engine.store.append_candle("XAUUSD", _candle(base + timedelta(minutes=5)), allow_gap=True)

    client = TestClient(api.app)
    candles = client.get("/public/m1-live.json").json()["symbols"]["XAUUSD"]["candles_1m"]

    assert len(candles) == 3  # exactly the three real candles -- nothing fabricated in between
    timestamps = [c["timestamp"] for c in candles]
    assert timestamps == [
        base.isoformat().replace("+00:00", "Z"),
        (base + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        (base + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    ]


def test_m1_live_duplicate_and_out_of_order_candles_follow_existing_store_rules(tmp_path, monkeypatch):
    """The endpoint must reflect exactly what CandleStore.append_candle
    already enforces -- a duplicate timestamp corrects in place, and an
    out-of-order candle is rejected by the store, not silently accepted."""
    from pysgrid_forex.store import NonSequentialCandleError

    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.store.append_candle("XAUUSD", _candle(now - timedelta(minutes=1), close=100.0))
    engine.store.append_candle("XAUUSD", _candle(now, close=101.0))

    # Duplicate of the last stored candle, with a revised close: idempotent correction.
    engine.store.append_candle("XAUUSD", _candle(now, close=999.0))

    # Out-of-order (backwards) candle: must raise, never silently stored.
    import pytest
    with pytest.raises(NonSequentialCandleError):
        engine.store.append_candle("XAUUSD", _candle(now - timedelta(minutes=1), close=-1.0))

    client = TestClient(api.app)
    candles = client.get("/public/m1-live.json").json()["symbols"]["XAUUSD"]["candles_1m"]

    assert len(candles) == 2
    assert candles[-1]["close"] == 999.0  # correction applied
    assert candles[0]["close"] == 100.0  # unaffected by the rejected out-of-order attempt


def test_m1_live_is_compatible_with_the_existing_live_json_storage_path(tmp_path, monkeypatch):
    """/public/m1-live.json and /public/live.json must expose identical
    candle data for the same symbol -- both read engine.store, the same
    trusted M1 buffer, through the same to_dict() convention."""
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.store.append_candle("XAUUSD", _candle(now - timedelta(minutes=1)))
    engine.store.append_candle("XAUUSD", _candle(now))

    client = TestClient(api.app)
    live_body = client.get("/public/live.json").json()
    m1_live_body = client.get("/public/m1-live.json").json()

    assert live_body["symbols"]["XAUUSD"] == m1_live_body["symbols"]["XAUUSD"]


def test_m1_live_route_is_reachable_and_not_swallowed_by_the_symbol_catch_all(tmp_path, monkeypatch):
    """Same route-ordering hazard as m5-live.json: /public/{symbol}.json is
    a single-segment catch-all and must not intercept this exact path."""
    engine = _wire_engine(monkeypatch, tmp_path, ("XAUUSD",))
    now = _now()
    engine.store.append_candle("XAUUSD", _candle(now - timedelta(minutes=1)))
    engine.store.append_candle("XAUUSD", _candle(now))

    client = TestClient(api.app)
    response = client.get("/public/m1-live.json")

    assert response.status_code == 200
    assert "detail" not in response.json()
