from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .config import Settings
from .engine import Engine

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
        "api_key_configured": bool(settings.api_key),
    }


@app.get("/metrics")
async def metrics() -> dict:
    states = engine.states()
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
            symbol: state.to_dict(m1_valid=engine.is_valid_m1(state))
            for symbol, state in states.items()
        },
    })


@app.get("/public/forex.json")
async def forex() -> JSONResponse:
    states = engine.states()
    symbols = {
        s: states[s].to_dict(m1_valid=engine.is_valid_m1(states[s]))
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
