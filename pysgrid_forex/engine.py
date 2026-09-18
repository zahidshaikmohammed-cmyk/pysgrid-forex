from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .config import Settings
from .models import Candle, SymbolState, parse_timestamp
from .provider import RealMarketAPI
from .store import CandleStore

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = CandleStore(settings.data_dir, settings.max_candles)
        self.stop_event = asyncio.Event()
        self.provider = RealMarketAPI(settings, self.on_candle, self.on_status)
        self.started_at = datetime.now(timezone.utc)

    async def on_status(self, symbol: str, connected: bool) -> None:
        self.store.mark_socket(symbol, connected)
        if not connected:
            self.store.increment_reconnect(symbol)

    async def on_candle(self, symbol: str, candle: Candle) -> None:
        state = self.store.load(symbol)

        if state.last_candle_timestamp:
            previous = parse_timestamp(state.last_candle_timestamp)
            current = parse_timestamp(candle.timestamp)
            gap_seconds = (current - previous).total_seconds()

            # Never move backwards or duplicate an older completed bar.
            if gap_seconds <= 0:
                return

            # A legitimate M1 outage can create a larger gap. Count it, but
            # keep the newly received completed M1 candle rather than asking
            # /candle for data that may be a different resolution.
            if gap_seconds > 60:
                self.store.increment_recovery(symbol)
                log.warning(
                    "M1 gap detected for %s: %.0f seconds (%s -> %s)",
                    symbol,
                    gap_seconds,
                    state.last_candle_timestamp,
                    candle.timestamp,
                )

        self.store.upsert(symbol, candle)

    @staticmethod
    def is_valid_m1(state: SymbolState, *, max_age_seconds: int = 120) -> bool:
        candles = state.candles or []
        if len(candles) < 2 or not state.last_candle_timestamp:
            return False
        try:
            last = parse_timestamp(state.last_candle_timestamp)
            age = (datetime.now(timezone.utc) - last).total_seconds()
            if age > max_age_seconds:
                return False
            previous = candles[-2]
            current = candles[-1]
            return (
                (parse_timestamp(current.timestamp) - parse_timestamp(previous.timestamp)).total_seconds()
                == 60
            )
        except (ValueError, TypeError):
            return False

    def states(self) -> dict[str, SymbolState]:
        states = self.store.all_states(self.settings.symbols)
        now = datetime.now(timezone.utc)

        for state in states.values():
            if state.updated_at:
                try:
                    age = (
                        now
                        - datetime.fromisoformat(
                            state.updated_at.replace("Z", "+00:00")
                        )
                    ).total_seconds()
                except ValueError:
                    age = 10**9

                if age > self.settings.stale_seconds:
                    state.status = "stale"
                    state.market_state = (
                        "closed" if now.weekday() >= 5 else "stale"
                    )
            elif not state.candles:
                state.status = "no_data"

        return states

    async def start(self) -> None:
        if not self.settings.api_key:
            log.warning(
                "REALMARKET_API_KEY is empty; service will remain in no-data mode."
            )
        await self.provider.run(self.stop_event)

    async def stop(self) -> None:
        self.stop_event.set()
