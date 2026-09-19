from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .config import Settings
from .models import Candle, SymbolState, parse_timestamp
from .provider import RealMarketAPI
from .store import CandleStore

log = logging.getLogger(__name__)

M1_SECONDS = 60


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = CandleStore(settings.data_dir, settings.max_candles)
        self.stop_event = asyncio.Event()
        self.provider = RealMarketAPI(settings, self.on_candle, self.on_status)
        self.started_at = datetime.now(timezone.utc)
        # A completed candle that does not yet continue the persisted series
        # (either because the store is empty, or because a gap was
        # observed). It is held here, in memory only, and is never exposed
        # as data until a *following* candle confirms it by landing exactly
        # 60 seconds after it. This guarantees candles_1m never receives a
        # candle whose relationship to its neighbour hasn't been verified,
        # without ever fabricating the missing minutes in between.
        self._pending: dict[str, Candle] = {}

    async def on_status(self, symbol: str, connected: bool) -> None:
        self.store.mark_socket(symbol, connected)
        if not connected:
            self.store.increment_reconnect(symbol)

    async def on_candle(self, symbol: str, candle: Candle) -> None:
        try:
            parse_timestamp(candle.timestamp)
        except (ValueError, TypeError):
            log.warning("Discarding candle for %s with unparseable timestamp: %r", symbol, candle.timestamp)
            self.store.increment_rejected(symbol)
            return

        state = self.store.load(symbol)
        last = state.candles[-1] if state.candles else None
        pending = self._pending.get(symbol)

        if pending is None:
            if last is None:
                # Nothing to compare against yet: hold as an unconfirmed
                # seed rather than trusting a single, isolated candle.
                self._pending[symbol] = candle
                return

            gap = (parse_timestamp(candle.timestamp) - parse_timestamp(last.timestamp)).total_seconds()

            if gap < 0:
                log.warning(
                    "Discarding out-of-order candle for %s: %s (last stored %s)",
                    symbol, candle.timestamp, last.timestamp,
                )
                self.store.increment_rejected(symbol)
                return

            if gap == 0:
                # Same-minute re-delivery: an idempotent correction of the
                # last stored bar, not a new one. The store handles this.
                self.store.append_candle(symbol, candle)
                return

            if gap == M1_SECONDS:
                self.store.append_candle(symbol, candle)
                return

            log.warning(
                "M1 gap detected for %s: %.0f seconds (%s -> %s); holding as "
                "unconfirmed resync anchor, not storing yet",
                symbol, gap, last.timestamp, candle.timestamp,
            )
            self._pending[symbol] = candle
            return

        # A pending anchor exists: only a candle exactly 60s after it proves
        # the anchor itself sits on a genuine 1-minute cadence.
        gap = (parse_timestamp(candle.timestamp) - parse_timestamp(pending.timestamp)).total_seconds()

        if gap == M1_SECONDS:
            self.store.append_candle(symbol, pending, allow_gap=True)
            self.store.append_candle(symbol, candle)
            if last is not None:
                self.store.increment_recovery(symbol)
            self._pending.pop(symbol, None)
            return

        if gap < 0:
            log.warning(
                "Discarding out-of-order candle for %s while awaiting resync "
                "confirmation: %s (pending anchor %s)",
                symbol, candle.timestamp, pending.timestamp,
            )
            self.store.increment_rejected(symbol)
            return

        if gap == 0:
            # Duplicate of the still-unconfirmed anchor itself: refresh it,
            # it isn't a rejection.
            self._pending[symbol] = candle
            return

        log.warning(
            "Resync anchor for %s not confirmed (next candle %.0fs later, "
            "expected 60s); replacing anchor with %s",
            symbol, gap, candle.timestamp,
        )
        self.store.increment_rejected(symbol)
        self._pending[symbol] = candle

    @staticmethod
    def is_valid_m1(state: SymbolState, *, max_age_seconds: int = 120) -> bool:
        """True only if the symbol is currently delivering a live, genuine
        M1 cadence: recent, and the tail of the stored series is an
        uninterrupted 60-second-spaced run. The whole series is also
        defensively scanned for impossible (non-positive) spacing, so this
        does not rely on trusting just the final two entries."""
        candles = state.candles or []
        if len(candles) < 2 or not state.last_candle_timestamp:
            return False
        try:
            last = parse_timestamp(state.last_candle_timestamp)
            age = (datetime.now(timezone.utc) - last).total_seconds()
            if age > max_age_seconds:
                return False

            previous, current = candles[-2], candles[-1]
            if (
                parse_timestamp(current.timestamp) - parse_timestamp(previous.timestamp)
            ).total_seconds() != M1_SECONDS:
                return False

            for a, b in zip(candles, candles[1:]):
                if (parse_timestamp(b.timestamp) - parse_timestamp(a.timestamp)).total_seconds() <= 0:
                    return False

            return True
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

    def m1_status(self) -> dict[str, bool]:
        """Per-symbol M1 integrity, explicitly, for every configured symbol."""
        return {symbol: self.is_valid_m1(state) for symbol, state in self.states().items()}

    async def start(self) -> None:
        if not self.settings.api_key:
            log.warning(
                "REALMARKET_API_KEY is empty; service will remain in no-data mode."
            )
        await self.provider.run(self.stop_event)

    async def stop(self) -> None:
        self.stop_event.set()
