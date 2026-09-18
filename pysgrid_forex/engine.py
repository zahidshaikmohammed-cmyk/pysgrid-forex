from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .config import Settings
from .models import Candle, SymbolState
from .provider import RealMarketAPI
from .store import CandleStore

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = CandleStore(settings.data_dir, settings.max_candles)
        self.stop_event = asyncio.Event()
        self.provider = RealMarketAPI(settings, self.on_candle)
        self.started_at = datetime.now(timezone.utc)

    async def on_candle(self, symbol: str, candle: Candle) -> None:
        self.store.upsert(symbol, candle)

    def states(self) -> dict[str, SymbolState]:
        states = self.store.all_states(self.settings.symbols)
        now = datetime.now(timezone.utc)
        for state in states.values():
            if state.updated_at:
                try:
                    age = (now - datetime.fromisoformat(state.updated_at.replace("Z", "+00:00"))).total_seconds()
                except ValueError:
                    age = 10**9
                if age > self.settings.stale_seconds:
                    state.status = "stale"
                    state.market_state = "closed" if now.weekday() >= 5 else "stale"
            elif not state.candles:
                state.status = "no_data"
        return states

    async def start(self) -> None:
        if not self.settings.api_key:
            log.warning("REALMARKET_API_KEY is empty; service will remain in no-data mode.")
        await self.provider.run(self.stop_event)

    async def stop(self) -> None:
        self.stop_event.set()
