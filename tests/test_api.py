import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    settings = Settings(api_key="", symbols=symbols, data_dir=str(tmp_path))
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
