"""HTTP client for the pysgrid-forex validated M1 feed.

This is the ONLY place price data enters the signal engine, and it enforces
a hard rule: a symbol's candles are only used if pysgrid-forex itself
reports them as m1_valid. This engine trusts the M1 integrity gate that was
built into pysgrid-forex; it does not re-derive it, and it does not fall
back to treating unvalidated data as good enough.
"""
from __future__ import annotations

import logging

import httpx

from pysgrid_forex.models import Candle, parse_timestamp

from .config import EngineConfig

log = logging.getLogger(__name__)


class SymbolSnapshot:
    def __init__(self, symbol: str, m1_valid: bool, status: str, candles: list[Candle]):
        self.symbol = symbol
        self.m1_valid = m1_valid
        self.status = status
        self.candles = candles

    @property
    def last_timestamp(self) -> str | None:
        return self.candles[-1].timestamp if self.candles else None


class FeedClient:
    def __init__(self, config: EngineConfig, client: httpx.Client | None = None):
        self.config = config
        self._client = client or httpx.Client(timeout=10.0)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "FeedClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch_symbol(self, symbol: str) -> SymbolSnapshot | None:
        url = f"{self.config.api_base}/public/{symbol}.json"
        try:
            response = self._client.get(url)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Feed request failed for %s (%s): %s", symbol, url, exc)
            return None

        candles: list[Candle] = []
        for item in body.get("candles_1m", []):
            try:
                candles.append(
                    Candle(
                        timestamp=item["timestamp"],
                        open=float(item["open"]),
                        high=float(item["high"]),
                        low=float(item["low"]),
                        close=float(item["close"]),
                        volume=float(item["volume"]),
                        bid=item.get("bid"),
                        ask=item.get("ask"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

        candles.sort(key=lambda c: parse_timestamp(c.timestamp))

        return SymbolSnapshot(
            symbol=symbol,
            m1_valid=bool(body.get("m1_valid", False)),
            status=str(body.get("status", "unknown")),
            candles=candles,
        )

    def fetch_all(self) -> dict[str, SymbolSnapshot]:
        snapshots: dict[str, SymbolSnapshot] = {}
        for symbol in self.config.symbols:
            snapshot = self.fetch_symbol(symbol)
            if snapshot is not None:
                snapshots[symbol] = snapshot
        return snapshots
