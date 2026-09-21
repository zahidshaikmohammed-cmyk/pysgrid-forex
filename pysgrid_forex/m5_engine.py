"""Native M5 (5-minute) candle acceptance.

RealMarketAPI's `/candles` and `/price` WebSockets were both independently
confirmed, against live open-market data, to deliver candles spaced exactly
300 seconds apart under `timeFrame=M1` (see README.md's Data-integrity
rules for the evidence: 69 consecutive candles over 5h40m on /candles with
zero exceptions to the 300s gap, and a separate 700s observation on /price
showing the same). Rather than keep rejecting that data as invalid M1
forever, this module accepts it honestly for what it actually is: a
genuine, native 5-minute feed. It reuses the exact same connections the M1
pipeline already holds open (no new WebSocket connections, which would
compete for this account's limited concurrent-connection pool) and applies
the identical write-time integrity discipline as the M1 path, just at a
300-second period instead of 60: no candle is ever trusted as valid M5
until a *following* candle confirms it sits on a genuine 300-second
cadence, and nothing is ever fabricated to fill a gap.

This does not touch, replace, or weaken the M1 pipeline in engine.py, which
keeps correctly reporting m1_valid=false for as long as the provider keeps
sending 5-minute-spaced data under a M1 request -- that is still the
truthful answer to "is this genuine M1," and this module does not change
it. It only adds a second, honestly-labeled acceptance path for the
resolution the provider actually delivers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .models import Candle, SymbolState, parse_timestamp
from .store import CandleStore

log = logging.getLogger(__name__)

M5_SECONDS = 300


class M5Engine:
    """Same pending-anchor contiguity state machine as Engine.on_candle in
    engine.py, parameterized for a 300-second period instead of 60. See that
    module for the detailed rationale of each branch; this is a deliberate,
    focused duplication (not a shared base class) so the extensively-tested
    M1 path in engine.py is never at risk of being disturbed by M5 changes,
    or vice versa."""

    def __init__(self, store: CandleStore):
        if store.period_seconds != M5_SECONDS:
            raise ValueError(f"M5Engine requires a store with period_seconds=300, got {store.period_seconds}")
        self.store = store
        self._pending: dict[str, Candle] = {}

    async def on_candle(self, symbol: str, candle: Candle) -> None:
        try:
            parse_timestamp(candle.timestamp)
        except (ValueError, TypeError):
            log.warning("Discarding M5 candle for %s with unparseable timestamp: %r", symbol, candle.timestamp)
            self.store.increment_rejected(symbol)
            return

        state = self.store.load(symbol)
        last = state.candles[-1] if state.candles else None
        pending = self._pending.get(symbol)

        if pending is None:
            if last is None:
                self._pending[symbol] = candle
                return

            gap = (parse_timestamp(candle.timestamp) - parse_timestamp(last.timestamp)).total_seconds()

            if gap < 0:
                log.warning(
                    "Discarding out-of-order M5 candle for %s: %s (last stored %s)",
                    symbol, candle.timestamp, last.timestamp,
                )
                self.store.increment_rejected(symbol)
                return

            if gap == 0:
                self.store.append_candle(symbol, candle)
                return

            if gap == M5_SECONDS:
                self.store.append_candle(symbol, candle)
                return

            log.warning(
                "M5 gap detected for %s: %.0f seconds (%s -> %s); holding as "
                "unconfirmed resync anchor, not storing yet",
                symbol, gap, last.timestamp, candle.timestamp,
            )
            self._pending[symbol] = candle
            return

        gap = (parse_timestamp(candle.timestamp) - parse_timestamp(pending.timestamp)).total_seconds()

        if gap == M5_SECONDS:
            self.store.append_candle(symbol, pending, allow_gap=True)
            self.store.append_candle(symbol, candle)
            if last is not None:
                self.store.increment_recovery(symbol)
            self._pending.pop(symbol, None)
            return

        if gap < 0:
            log.warning(
                "Discarding out-of-order M5 candle for %s while awaiting resync "
                "confirmation: %s (pending anchor %s)",
                symbol, candle.timestamp, pending.timestamp,
            )
            self.store.increment_rejected(symbol)
            return

        if gap == 0:
            self._pending[symbol] = candle
            return

        log.warning(
            "M5 resync anchor for %s not confirmed (next candle %.0fs later, "
            "expected 300s); replacing anchor with %s",
            symbol, gap, candle.timestamp,
        )
        self.store.increment_rejected(symbol)
        self._pending[symbol] = candle

    @staticmethod
    def is_valid(state: SymbolState, *, max_age_seconds: int = 600) -> bool:
        """True only if the symbol currently has a live, genuine M5 cadence:
        recent, and the tail of the stored series is an uninterrupted
        300-second-spaced run. max_age_seconds defaults to 600 (2 periods)
        rather than M1's 120 (2 periods there too), since one M5 bar simply
        takes longer to complete."""
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
            ).total_seconds() != M5_SECONDS:
                return False

            for a, b in zip(candles, candles[1:]):
                if (parse_timestamp(b.timestamp) - parse_timestamp(a.timestamp)).total_seconds() <= 0:
                    return False

            return True
        except (ValueError, TypeError):
            return False
