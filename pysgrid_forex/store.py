from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .models import Candle, SymbolState, parse_timestamp

log = logging.getLogger(__name__)

# v4 adds write-time M1 contiguity enforcement (see append_candle). Any data
# written by earlier schema versions may contain candles that were never
# validated for genuine 1-minute spacing (including the previously observed
# 5-minute /candle feed masquerading as M1), so it must not be trusted and is
# invalidated exactly once on first load under this version.
DATA_SCHEMA_VERSION = 4

M1_SECONDS = 60


class NonSequentialCandleError(ValueError):
    """Raised when a candle would corrupt the store's period-contiguity guarantee."""


class CandleStore:
    """Persists per-symbol OHLCV state for a single, fixed candle period.

    The store enforces, at the point of write, that its stored series can
    never contain two adjacent entries that are not exactly ``period_seconds``
    apart unless the caller explicitly acknowledges a gap (``allow_gap=True``).
    This makes "every stored candle is genuine <period>" a property of the
    storage layer itself, not something callers have to get right, and it
    holds regardless of what the last two entries happen to look like.

    ``period_seconds`` defaults to 60 (M1) to preserve the exact existing
    behavior for every caller that doesn't pass it explicitly. A second
    instance with ``period_seconds=300`` is what the M5 pipeline uses --
    same guarantees, same code, different period.
    """

    def __init__(self, data_dir: str, max_candles: int = 500, period_seconds: int = M1_SECONDS):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_candles = max_candles
        self.period_seconds = period_seconds
        # The on-disk/serialized key must reflect the actual period stored --
        # writing M5 data under a "candles_1m" key would be exactly the kind
        # of mislabeling this whole project exists to prevent.
        self.candles_key = "candles_1m" if period_seconds == M1_SECONDS else f"candles_{period_seconds // 60}m"
        self._lock = RLock()
        self._states: dict[str, SymbolState] = {}

    def _path(self, symbol: str) -> Path:
        return self.root / f"{symbol}.json"

    @staticmethod
    def _validate_monotonic(candles: list[Candle]) -> bool:
        """True if candles are strictly increasing in time (gaps allowed).

        Real M1 feeds legitimately have gaps (weekend closures, brief
        provider outages), so a gap alone is not corruption. What must never
        happen is a non-positive step: a duplicate, backwards, or otherwise
        impossible timestamp sequence, which can only mean the persisted
        data was produced by logic that bypassed ``append_candle``.
        """
        for previous, current in zip(candles, candles[1:]):
            delta = (
                parse_timestamp(current.timestamp) - parse_timestamp(previous.timestamp)
            ).total_seconds()
            if delta <= 0:
                return False
        return True

    def load(self, symbol: str) -> SymbolState:
        with self._lock:
            if symbol in self._states:
                return self._states[symbol]

            path = self._path(symbol)
            state = SymbolState(symbol=symbol, candles=[])

            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))

                    if raw.get("data_schema_version") != DATA_SCHEMA_VERSION:
                        self._states[symbol] = state
                        return state

                    state.market_state = raw.get("market_state", "unknown")
                    state.status = raw.get("status", "no_data")
                    state.last_candle_timestamp = raw.get("last_candle_timestamp")
                    state.updated_at = raw.get("updated_at")
                    state.websocket_connected = bool(raw.get("websocket_connected", False))
                    state.reconnect_count = int(raw.get("reconnect_count", 0))
                    state.gap_recoveries = int(raw.get("gap_recoveries", 0))
                    state.rejected_count = int(raw.get("rejected_count", 0))
                    candles = [Candle(**x) for x in raw.get(self.candles_key, [])]

                    if not self._validate_monotonic(candles):
                        log.error(
                            "Persisted candle series for %s is not monotonic; "
                            "discarding on-disk history",
                            symbol,
                        )
                        raise ValueError("stored candle series is not monotonic")

                    state.candles = candles[-self.max_candles :]
                    if state.candles:
                        state.last_candle_timestamp = state.candles[-1].timestamp
                except (OSError, ValueError, TypeError, KeyError):
                    state = SymbolState(symbol=symbol, candles=[])

            self._states[symbol] = state
            return state

    def all_states(self, symbols: tuple[str, ...]) -> dict[str, SymbolState]:
        return {s: self.load(s) for s in symbols}

    def append_candle(self, symbol: str, candle: Candle, *, allow_gap: bool = False) -> bool:
        """Append a completed candle, enforcing this store's period-contiguity invariant.

        - An empty store accepts any first candle.
        - A candle whose timestamp equals the last stored one is treated as
          an idempotent correction (safe re-delivery), not a new bar.
        - A candle exactly ``period_seconds`` after the last stored one is a
          normal continuation and is always accepted.
        - Anything else (a duplicate/backwards timestamp, or a jump of any
          other size) is rejected with ``NonSequentialCandleError`` unless
          the caller passes ``allow_gap=True``, which only a confirmed-resync
          path is allowed to do (see Engine.on_candle / M5Engine.on_candle).
          A gap is recorded as-is; no intermediate candles are fabricated.

        Returns True if the stored state changed.
        """
        with self._lock:
            state = self.load(symbol)
            candles = list(state.candles or [])

            if candles:
                last = candles[-1]
                delta = (
                    parse_timestamp(candle.timestamp) - parse_timestamp(last.timestamp)
                ).total_seconds()

                if delta == 0:
                    if last == candle:
                        return False
                    candles[-1] = candle
                elif delta < 0:
                    raise NonSequentialCandleError(
                        f"{symbol}: candle at {candle.timestamp} is not after "
                        f"the last stored candle at {last.timestamp}"
                    )
                elif delta != self.period_seconds and not allow_gap:
                    raise NonSequentialCandleError(
                        f"{symbol}: candle at {candle.timestamp} is {delta:.0f}s "
                        f"after the last stored candle at {last.timestamp}, "
                        f"expected exactly {self.period_seconds}s"
                    )
                else:
                    candles.append(candle)
            else:
                candles.append(candle)

            state.candles = candles[-self.max_candles :]
            state.last_candle_timestamp = state.candles[-1].timestamp
            state.updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            state.status = "ok"
            state.market_state = "open"
            self._persist(state)
            return True

    def mark_socket(self, symbol: str, connected: bool) -> None:
        state = self.load(symbol)
        state.websocket_connected = connected
        self._persist(state)

    def increment_reconnect(self, symbol: str) -> None:
        state = self.load(symbol)
        state.reconnect_count += 1
        self._persist(state)

    def increment_recovery(self, symbol: str) -> None:
        state = self.load(symbol)
        state.gap_recoveries += 1
        self._persist(state)

    def increment_rejected(self, symbol: str) -> None:
        state = self.load(symbol)
        state.rejected_count += 1
        self._persist(state)

    def _persist(self, state: SymbolState) -> None:
        payload = json.dumps(
            {
                "data_schema_version": DATA_SCHEMA_VERSION,
                **state.to_dict(candles_key=self.candles_key),
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        path = self._path(state.symbol)
        fd, temp = tempfile.mkstemp(
            prefix=f".{state.symbol}.",
            dir=self.root,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
