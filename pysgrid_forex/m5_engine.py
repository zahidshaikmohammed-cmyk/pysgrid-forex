"""M1 -> M5 aggregation.

Historical note: for a period ending 2026-09-21, RealMarketAPI's WebSocket
delivered candles spaced exactly 300 seconds apart under `timeFrame=M1`
(confirmed against live open-market data -- see git history around that
date). This module used to treat that raw provider data as an honest,
native M5 feed for exactly that reason.

RealMarketAPI's support team has since confirmed (2026-09-21) that the M1
defect was on their side and is fixed: "XAUUSD M1 is now working correctly
on both REST and WebSocket." The Oracle acquisition layer keeps requesting
`timeFrame=M1` -- unchanged -- and now receives genuine 1-minute candles.
Consequently this module no longer accepts raw provider data directly.
Instead it aggregates five contiguous, already-validated M1 candles (as
accepted by Engine.on_candle's own resync/gap discipline -- see that
module) into one completed 5-minute OHLCV bar:

    open   = first M1 open
    high   = max of the five M1 highs
    low    = min of the five M1 lows
    close  = last M1 close
    volume = sum of the five M1 volumes

Only a genuinely complete, 5-minute-aligned bucket of five contiguous M1
candles ever becomes an M5 candle. A missing, duplicate, out-of-order, or
delayed M1 candle discards the in-progress bucket outright -- nothing is
ever fabricated, forward-filled, interpolated, or duplicated to complete
it. This module does not re-verify M1 cadence itself; it trusts that every
candle it is given already passed Engine.on_candle's own discipline, and
its own job is strictly the M1->M5 bucketing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .models import Candle, SymbolState, parse_timestamp
from .store import CandleStore, NonSequentialCandleError

log = logging.getLogger(__name__)

M1_SECONDS = 60
M5_SECONDS = 300
BUCKET_SIZE = M5_SECONDS // M1_SECONDS  # 5 M1 candles per M5 candle


@dataclass
class _Bucket:
    start_ts: datetime
    candles: list[Candle] = field(default_factory=list)


class M5Engine:
    """Aggregates a stream of already-validated, contiguous M1 candles --
    fed one at a time, in arrival order, by Engine.on_candle -- into
    completed 5-minute OHLCV bars, and persists only genuinely complete
    buckets to an M5-period CandleStore.
    """

    def __init__(self, store: CandleStore):
        if store.period_seconds != M5_SECONDS:
            raise ValueError(f"M5Engine requires a store with period_seconds=300, got {store.period_seconds}")
        self.store = store
        self._buckets: dict[str, _Bucket] = {}
        self._expected_next: dict[str, datetime] = {}

    def on_m1_candle(self, symbol: str, candle: Candle) -> None:
        """Feed one validated M1 candle into this symbol's in-progress
        5-minute bucket. Must be called in the same order Engine.on_candle
        actually accepts candles into the M1 store (including both halves
        of a confirmed resync pair) -- this method does not re-derive that
        ordering itself."""
        try:
            ts = parse_timestamp(candle.timestamp)
        except (ValueError, TypeError):
            log.warning(
                "M1->M5 aggregator discarding candle for %s with unparseable timestamp: %r",
                symbol, candle.timestamp,
            )
            return

        bucket = self._buckets.get(symbol)
        expected = self._expected_next.get(symbol)

        if bucket is not None and bucket.candles and ts == parse_timestamp(bucket.candles[-1].timestamp):
            # Same-minute correction of the most recently accepted M1 slot
            # (mirrors CandleStore's own idempotent-correction handling).
            bucket.candles[-1] = candle
            return

        if expected is not None and ts < expected:
            # A stale/delayed candle for a slot already moved past. The
            # in-progress bucket (if any) is left untouched by it.
            log.warning(
                "M1->M5 aggregator for %s ignoring stale/delayed M1 candle %s (already past %s)",
                symbol, candle.timestamp, expected.isoformat().replace("+00:00", "Z"),
            )
            return

        if bucket is not None and expected is not None and ts == expected:
            bucket.candles.append(candle)
            self._expected_next[symbol] = ts + timedelta(seconds=M1_SECONDS)
            if len(bucket.candles) == BUCKET_SIZE:
                self._complete_bucket(symbol, bucket)
                self._buckets.pop(symbol, None)
            return

        # Any other case is a break in contiguity: a missing M1 candle, an
        # out-of-order jump, a post-reconnect resync anchor, or the very
        # first candle ever seen for this symbol.
        if bucket is not None:
            log.warning(
                "M1->M5 aggregator for %s: bucket starting %s discarded incomplete "
                "(%d/%d M1 candles) -- next candle %s did not continue it. No M5 "
                "candle fabricated for this window.",
                symbol, bucket.start_ts.isoformat().replace("+00:00", "Z"),
                len(bucket.candles), BUCKET_SIZE, candle.timestamp,
            )
            self._buckets.pop(symbol, None)

        if ts.minute % 5 == 0 and ts.second == 0 and ts.microsecond == 0:
            self._buckets[symbol] = _Bucket(start_ts=ts, candles=[candle])
            self._expected_next[symbol] = ts + timedelta(seconds=M1_SECONDS)
        else:
            # Not aligned to a 5-minute boundary -- wait for one before
            # starting a new bucket, rather than ever emitting a
            # misaligned M5 candle.
            self._expected_next.pop(symbol, None)

    def _complete_bucket(self, symbol: str, bucket: _Bucket) -> None:
        candles = bucket.candles
        m5_candle = Candle(
            timestamp=bucket.start_ts.isoformat().replace("+00:00", "Z"),
            open=candles[0].open,
            high=max(c.high for c in candles),
            low=min(c.low for c in candles),
            close=candles[-1].close,
            volume=sum(c.volume for c in candles),
        )

        state = self.store.load(symbol)
        last = state.candles[-1] if state.candles else None
        allow_gap = False
        if last is not None:
            delta = (bucket.start_ts - parse_timestamp(last.timestamp)).total_seconds()
            allow_gap = delta != M5_SECONDS

        try:
            self.store.append_candle(symbol, m5_candle, allow_gap=allow_gap)
        except NonSequentialCandleError:
            log.error(
                "M1->M5 aggregator: completed M5 candle for %s at %s rejected by "
                "the store unexpectedly -- dropping rather than forcing it in",
                symbol, m5_candle.timestamp,
            )
            return

        if allow_gap:
            self.store.increment_recovery(symbol)

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
