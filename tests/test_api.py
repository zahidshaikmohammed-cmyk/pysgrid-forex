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
