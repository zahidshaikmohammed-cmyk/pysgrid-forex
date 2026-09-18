from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import websockets

from .config import Settings
from .models import Candle

log = logging.getLogger(__name__)
CandleHandler = Callable[[str, Candle], Awaitable[None]]


class RealMarketAPI:
    def __init__(self, settings: Settings, on_candle: CandleHandler):
        self.s = settings
        self.on_candle = on_candle

    def _ws_url(self, symbol: str) -> str:
        query = urlencode({
            "apiKey": self.s.api_key,
            "symbolCode": symbol,
            "timeFrame": self.s.timeframe,
        })
        return f"{self.s.ws_base}?{query}"

    async def fetch_recent(self, symbol: str) -> list[Candle]:
        if not self.s.api_key:
            return []
        url = f"{self.s.rest_base}/candle"
        params = {
            "apiKey": self.s.api_key,
            "symbolCode": symbol,
            "timeFrame": self.s.timeframe,
        }
        async with httpx.AsyncClient(timeout=self.s.rest_timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            body = response.json()
        return self._extract_candles(body)

    @staticmethod
    def _extract_candles(body: object) -> list[Candle]:
        if isinstance(body, dict):
            for key in ("data", "Data", "candles", "Candles", "items", "Items"):
                value = body.get(key)
                if isinstance(value, list):
                    return [Candle.from_provider(x) for x in value if isinstance(x, dict)]
            try:
                return [Candle.from_provider(body)]
            except (KeyError, ValueError, TypeError):
                return []
        if isinstance(body, list):
            return [Candle.from_provider(x) for x in body if isinstance(x, dict)]
        return []

    async def recover(self, symbol: str) -> list[Candle]:
        try:
            candles = await self.fetch_recent(symbol)
            for candle in candles:
                await self.on_candle(symbol, candle)
            return candles
        except Exception:
            log.exception("REST recovery failed for %s", symbol)
            return []

    async def stream_symbol(self, symbol: str, stop: asyncio.Event) -> None:
        delay = 1.0
        while not stop.is_set():
            try:
                if not self.s.api_key:
                    await asyncio.sleep(5)
                    continue
                await self.recover(symbol)
                log.info("connecting WebSocket: %s", symbol)
                async with websockets.connect(
                    self._ws_url(symbol),
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2_000_000,
                ) as ws:
                    delay = 1.0
                    yield_connected = getattr(self.on_candle, "__self__", None)
                    _ = yield_connected
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            await ws.ping()
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        body = json.loads(raw)
                        candles = self._extract_candles(body)
                        if not candles and isinstance(body, dict):
                            # Some providers wrap one frame under a message/payload/data object.
                            for key in ("message", "payload", "result", "data", "Data"):
                                nested = body.get(key)
                                if isinstance(nested, dict):
                                    try:
                                        candles = [Candle.from_provider(nested)]
                                        break
                                    except (KeyError, ValueError, TypeError):
                                        pass
                        for candle in candles:
                            await self.on_candle(symbol, candle)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("WebSocket %s disconnected: %s", symbol, exc)
                await self.recover(symbol)
                await asyncio.sleep(delay + random.uniform(0, min(1.0, delay)))
                delay = min(self.s.reconnect_max_seconds, delay * 2)

    async def run(self, stop: asyncio.Event) -> None:
        await asyncio.gather(*(self.stream_symbol(symbol, stop) for symbol in self.s.symbols))
