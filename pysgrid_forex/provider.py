from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import websockets

from .config import Settings
from .models import Candle, parse_timestamp

log = logging.getLogger(__name__)
CandleHandler = Callable[[str, Candle], Awaitable[None]]
StatusHandler = Callable[[str, bool], Awaitable[None]]

M1_SECONDS = 60


@dataclass
class _M1Accumulator:
    """Turn repeated provider updates for one candle into completed M1 bars."""

    current: Candle | None = None

    def update(self, incoming: Candle) -> Candle | None:
        ts = parse_timestamp(incoming.timestamp).replace(second=0, microsecond=0)
        normalized = Candle(
            timestamp=ts.isoformat().replace("+00:00", "Z"),
            open=incoming.open,
            high=incoming.high,
            low=incoming.low,
            close=incoming.close,
            volume=incoming.volume,
            bid=incoming.bid,
            ask=incoming.ask,
        )

        if self.current is None:
            self.current = normalized
            return None

        current_ts = parse_timestamp(self.current.timestamp)
        if ts < current_ts:
            return None

        if ts == current_ts:
            # RealMarketAPI's WebSocket emits repeated updates for the
            # forming timeframe candle. OHLC and volume are cumulative
            # within that candle, so retain the first open and latest
            # close/quote while taking the widest observed range and latest
            # volume. (This bucketing only detects candle *boundaries* --
            # whether a boundary is actually 60 seconds away from the last
            # one it hands off is validated by the engine, not here.)
            self.current = Candle(
                timestamp=self.current.timestamp,
                open=self.current.open,
                high=max(self.current.high, normalized.high),
                low=min(self.current.low, normalized.low),
                close=normalized.close,
                volume=max(self.current.volume, normalized.volume),
                bid=normalized.bid,
                ask=normalized.ask,
            )
            return None

        completed = self.current
        gap = (ts - current_ts).total_seconds()
        if gap != M1_SECONDS:
            log.warning(
                "M1 provider timestamp gap: %.0f seconds (%s -> %s)",
                gap,
                completed.timestamp,
                normalized.timestamp,
            )

        self.current = normalized
        return completed


class RealMarketAPI:
    def __init__(
        self,
        settings: Settings,
        on_candle: CandleHandler,
        on_status: StatusHandler | None = None,
    ):
        self.s = settings
        self.on_candle = on_candle
        self.on_status = on_status

    async def _status(self, symbol: str, connected: bool) -> None:
        if self.on_status:
            await self.on_status(symbol, connected)

    def _ws_url(self, symbol: str) -> str:
        query = urlencode(
            {
                "apiKey": self.s.api_key,
                "symbolCode": symbol,
                "timeFrame": self.s.timeframe,
            }
        )
        return f"{self.s.ws_base}?{query}"

    async def fetch_recent(self, symbol: str) -> list[Candle]:
        """Fetch REST candles only when they form a genuine M1 series."""
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
            candles = self._extract_candles(
                response.json(),
                completed_only=True,
                validate_series=True,
            )
            if not candles:
                log.warning(
                    "REST recovery for %s produced no validated M1 candles; "
                    "upstream /candle data was not accepted",
                    symbol,
                )
            return candles

    @staticmethod
    def _completed_m1(candle: Candle) -> bool:
        now = datetime.now(timezone.utc)
        opened = parse_timestamp(candle.timestamp)
        current_minute = now.replace(second=0, microsecond=0)
        return opened <= current_minute - timedelta(minutes=1)

    @classmethod
    def _extract_candles(
        cls,
        body: object,
        *,
        completed_only: bool = True,
        validate_series: bool = False,
    ) -> list[Candle]:
        def normalize(items: list[object]) -> list[Candle]:
            result: list[Candle] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    candle = Candle.from_provider(item)
                except (KeyError, ValueError, TypeError):
                    continue
                if completed_only and not cls._completed_m1(candle):
                    continue
                result.append(candle)

            result.sort(key=lambda c: parse_timestamp(c.timestamp))
            deduped: dict[str, Candle] = {c.timestamp: c for c in result}
            result = list(sorted(deduped.values(), key=lambda c: parse_timestamp(c.timestamp)))

            if validate_series and len(result) > 1:
                for previous, current in zip(result, result[1:]):
                    delta = (
                        parse_timestamp(current.timestamp)
                        - parse_timestamp(previous.timestamp)
                    ).total_seconds()
                    if delta != M1_SECONDS:
                        log.warning(
                            "Rejecting REST candle series: expected 1-minute spacing, "
                            "got %.0f seconds (%s -> %s)",
                            delta,
                            previous.timestamp,
                            current.timestamp,
                        )
                        return []
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
            for candle in candles:
                await self.on_candle(symbol, candle)
            return candles
        except Exception:
            log.exception("REST recovery failed for %s", symbol)
            return []

    @classmethod
    def _iter_provider_updates(cls, body: object) -> list[Candle]:
        """Extract every raw price/candle update without timestamp deduplication."""
        items: list[object]
        if isinstance(body, dict):
            for key in ("message", "payload", "result", "data", "Data", "candle", "Candle"):
                nested = body.get(key)
                if isinstance(nested, (dict, list)):
                    return cls._iter_provider_updates(nested)
            items = [body]
        elif isinstance(body, list):
            items = body
        else:
            return []

        result: list[Candle] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                result.append(Candle.from_provider(item))
            except (KeyError, ValueError, TypeError):
                continue
        return result

    async def stream_symbol(self, symbol: str, stop: asyncio.Event) -> None:
        delay = 1.0

        while not stop.is_set():
            accumulator = _M1Accumulator()
            try:
                if not self.s.api_key:
                    await self._status(symbol, False)
                    await asyncio.sleep(5)
                    continue

                log.info("connecting M1 price WebSocket: %s", symbol)

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

                        try:
                            body = json.loads(raw)
                        except json.JSONDecodeError:
                            log.warning("Invalid WebSocket JSON for %s", symbol)
                            continue

                        for update in self._iter_provider_updates(body):
                            completed = accumulator.update(update)
                            if completed is not None:
                                await self.on_candle(symbol, completed)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._status(symbol, False)
                log.warning("M1 WebSocket %s disconnected: %s", symbol, exc)
                await asyncio.sleep(
                    delay + random.uniform(0, min(1.0, delay))
                )
                delay = min(self.s.reconnect_max_seconds, delay * 2)

        await self._status(symbol, False)

    async def run(self, stop: asyncio.Event) -> None:
        await asyncio.gather(
            *(self.stream_symbol(symbol, stop) for symbol in self.s.symbols)
        )
