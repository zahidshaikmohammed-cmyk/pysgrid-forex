from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .config import Settings
from .engine import Engine
from .m5_engine import M5Engine

settings = Settings.from_env()
engine = Engine(settings)
app = FastAPI(title="Pysgrid Forex", version="1.0.0")


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@app.on_event("startup")
async def startup() -> None:
    app.state.worker = asyncio.create_task(engine.start())


@app.on_event("shutdown")
async def shutdown() -> None:
    await engine.stop()
    worker = getattr(app.state, "worker", None)
    if worker:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@app.get("/health")
async def health() -> dict:
    states = engine.states()
    live = sum(1 for s in states.values() if s.status == "ok")
    m1_status = {symbol: engine.is_valid_m1(state) for symbol, state in states.items()}
    m1_live = sum(1 for ok in m1_status.values() if ok)

    m5_status = engine.m5_status()
    m5_live = sum(1 for ok in m5_status.values() if ok)

    return {
        "service": "pysgrid-forex",
        "status": "ok" if live else "degraded",
        "generated_at": _stamp(),
        "provider": "realmarketapi",
        "timeframe": settings.timeframe,
        "symbol_count": len(settings.symbols),
        "live_symbols": live,
        "m1_live_symbols": m1_live,
        # Explicit per-symbol M1 integrity, and the single boolean the
        # deployment gate relies on: production is healthy only when every
        # configured symbol -- not just one -- has a validated M1 feed.
        "m1_status": m1_status,
        "all_m1_live": bool(settings.symbols) and m1_live == len(settings.symbols),
        # Native M5 feed (see README's Data-integrity rules): the provider's
        # actual confirmed resolution, accepted honestly as M5, not
        # relabeled as M1. Purely additive -- nothing above changes.
        "m5_live_symbols": m5_live,
        "m5_status": m5_status,
        "all_m5_live": bool(settings.symbols) and m5_live == len(settings.symbols),
        "api_key_configured": bool(settings.api_key),
    }


@app.get("/metrics")
async def metrics() -> dict:
    states = engine.states()
    m5_states = engine.m5_states()
    return {
        "generated_at": _stamp(),
        "symbols": {
            symbol: {
                "status": state.status,
                "last_candle_timestamp": state.last_candle_timestamp,
                "websocket_connected": state.websocket_connected,
                "reconnect_count": state.reconnect_count,
                "gap_recoveries": state.gap_recoveries,
                "rejected_count": state.rejected_count,
                "candle_count": len(state.candles or []),
                "m1_valid": engine.is_valid_m1(state),
            }
            for symbol, state in states.items()
        },
        "symbols_m5": {
            symbol: {
                "status": state.status,
                "last_candle_timestamp": state.last_candle_timestamp,
                "websocket_connected": state.websocket_connected,
                "reconnect_count": state.reconnect_count,
                "gap_recoveries": state.gap_recoveries,
                "rejected_count": state.rejected_count,
                "candle_count": len(state.candles or []),
                "m5_valid": M5Engine.is_valid(state),
            }
            for symbol, state in m5_states.items()
        },
    }


def _symbol_payload(symbol: str) -> dict:
    symbol = symbol.upper()
    if symbol not in settings.symbols:
        raise HTTPException(status_code=404, detail="symbol not configured")
    state = engine.states()[symbol]
    return {
        "schema_version": "1.0",
        "service": "pysgrid-forex",
        "provider": "realmarketapi",
        "symbol": symbol,
        "timeframe": settings.timeframe,
        "status": state.status,
        "market_state": state.market_state,
        "generated_at": _stamp(),
        "last_candle_timestamp": state.last_candle_timestamp,
        "m1_valid": engine.is_valid_m1(state),
        "websocket_connected": state.websocket_connected,
        "candles_1m": [c.as_dict() for c in (state.candles or [])],
    }


def _symbol_payload_m5(symbol: str) -> dict:
    symbol = symbol.upper()
    if symbol not in settings.symbols:
        raise HTTPException(status_code=404, detail="symbol not configured")
    state = engine.m5_states()[symbol]
    return {
        "schema_version": "1.0",
        "service": "pysgrid-forex",
        "provider": "realmarketapi",
        "symbol": symbol,
        "timeframe": "M5",
        "status": state.status,
        "market_state": state.market_state,
        "generated_at": _stamp(),
        "last_candle_timestamp": state.last_candle_timestamp,
        "m5_valid": M5Engine.is_valid(state),
        "websocket_connected": state.websocket_connected,
        "candles_5m": [c.as_dict() for c in (state.candles or [])],
    }


@app.get("/public/live.json")
async def live() -> JSONResponse:
    states = engine.states()
    return JSONResponse({
        "schema_version": "1.0",
        "service": "pysgrid-forex",
        "provider": "realmarketapi",
        "timeframe": settings.timeframe,
        "generated_at": _stamp(),
        "status": "ok" if any(s.status == "ok" for s in states.values()) else "degraded",
        "universe_size": len(settings.symbols),
        "symbols": {
            symbol: state.to_dict(valid=engine.is_valid_m1(state))
            for symbol, state in states.items()
        },
    })


@app.get("/public/forex.json")
async def forex() -> JSONResponse:
    states = engine.states()
    symbols = {
        s: states[s].to_dict(valid=engine.is_valid_m1(states[s]))
        for s in settings.symbols
        if s not in {"XAUUSD", "XAGUSD", "USOIL"}
    }
    return JSONResponse({
        "schema_version": "1.0",
        "service": "pysgrid-forex",
        "provider": "realmarketapi",
        "timeframe": settings.timeframe,
        "generated_at": _stamp(),
        "status": "ok" if any(x["status"] == "ok" for x in symbols.values()) else "degraded",
        "universe_size": len(symbols),
        "symbols": symbols,
    })


@app.get("/public/{symbol}.json")
async def symbol(symbol: str) -> JSONResponse:
    return JSONResponse(_symbol_payload(symbol))


@app.get("/public/m5-live.json")
async def m5_live() -> JSONResponse:
    """Native M5 feed -- see README's Data-integrity rules for why this
    exists: RealMarketAPI's WebSockets deliver genuine 5-minute candles,
    confirmed against live data, and this exposes that honestly as M5
    rather than continuing to reject it as invalid M1."""
    states = engine.m5_states()
    return JSONResponse({
        "schema_version": "1.0",
        "service": "pysgrid-forex",
        "provider": "realmarketapi",
        "timeframe": "M5",
        "generated_at": _stamp(),
        "status": "ok" if any(s.status == "ok" for s in states.values()) else "degraded",
        "universe_size": len(settings.symbols),
        "symbols": {
            symbol: state.to_dict(
                valid=M5Engine.is_valid(state), candles_key="candles_5m", valid_key="m5_valid"
            )
            for symbol, state in states.items()
        },
    })


@app.get("/public/m5-forex.json")
async def m5_forex() -> JSONResponse:
    states = engine.m5_states()
    symbols = {
        s: states[s].to_dict(
            valid=M5Engine.is_valid(states[s]), candles_key="candles_5m", valid_key="m5_valid"
        )
        for s in settings.symbols
        if s not in {"XAUUSD", "XAGUSD", "USOIL"}
    }
    return JSONResponse({
        "schema_version": "1.0",
        "service": "pysgrid-forex",
        "provider": "realmarketapi",
        "timeframe": "M5",
        "generated_at": _stamp(),
        "status": "ok" if any(x["status"] == "ok" for x in symbols.values()) else "degraded",
        "universe_size": len(symbols),
        "symbols": symbols,
    })


@app.get("/public/m5/{symbol}.json")
async def symbol_m5(symbol: str) -> JSONResponse:
    return JSONResponse(_symbol_payload_m5(symbol))
