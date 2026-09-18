from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import websockets

from .config import Settings
from .models import Candle, parse_timestamp

log = logging.getLogger(__name__)
CandleHandler = Callable[[str, Candle], Awaitable[None]]
StatusHandler = Callable[[str, bool], Awaitable[None]]


class RealMarketAPI:
    def __init__(self, settings: Settings, on_candle: CandleHandler, on_status: StatusHandler | None = None):
        self.s = settings
        self.on_candle = on_candle
        self.on_status = on_status

    async def _status(self, symbol: str, connected: bool) -> None:
        if self.on_status:
            await self.on_status(symbol, connected)

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
            return self._extract_candles(response.json())

    @staticmethod
    def _completed_m1(candle: Candle) -> bool:
        now = datetime.now(timezone.utc)
        opened = parse_timestamp(candle.timestamp)
        current_minute = now.replace(second=0, microsecond=0)
        return opened <= current_minute - timedelta(minutes=1)

    @classmethod
    def _extract_candles(cls, body: object) -> list[Candle]:
        def normalize(items: list[object]) -> list[Candle]:
            result: list[Candle] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    candle = Candle.from_provider(item)
                except (KeyError, ValueError, TypeError):
                    continue
                if cls._completed_m1(candle):
                    result.append(candle)
            return result

        if isinstance(body, dict):
            for key in ("data", "Data", "candles", "Candles", "items", "Items"):
                value = body.get(key)
                if isinstance(value, list):
                    return normalize(value)
            return normalize([body])
        if isinstance(body, list):
            return normalize(body)
        return []

    async def recover(self, symbol: str) -> list[Candle]:
        try:
            candles = await self.fetch_recent(symbol)
            for candle in sorted(candles, key=lambda c: c.timestamp):
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
                    await self._status(symbol, False)
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
                    await self._status(symbol, True)
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
                            for key in ("message", "payload", "result", "data", "Data"):
                                nested = body.get(key)
                                if isinstance(nested, dict):
                                    candles = self._extract_candles(nested)
                                    if candles:
                                        break
                        for candle in candles:
                            await self.on_candle(symbol, candle)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._status(symbol, False)
                log.warning("WebSocket %s disconnected: %s", symbol, exc)
                await self.recover(symbol)
                await asyncio.sleep(delay + random.uniform(0, min(1.0, delay)))
                delay = min(self.s.reconnect_max_seconds, delay * 2)
        await self._status(symbol, False)

    async def run(self, stop: asyncio.Event) -> None:
        await asyncio.gather(*(self.stream_symbol(symbol, stop) for symbol in self.s.symbols))
