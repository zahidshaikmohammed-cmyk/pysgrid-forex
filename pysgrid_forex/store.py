from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .models import Candle, SymbolState


class CandleStore:
    def __init__(self, data_dir: str, max_candles: int = 500):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_candles = max_candles
        self._lock = RLock()
        self._states: dict[str, SymbolState] = {}

    def _path(self, symbol: str) -> Path:
        return self.root / f"{symbol}.json"

    def load(self, symbol: str) -> SymbolState:
        with self._lock:
            if symbol in self._states:
                return self._states[symbol]
            path = self._path(symbol)
            state = SymbolState(symbol=symbol, candles=[])
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    state.market_state = raw.get("market_state", "unknown")
                    state.status = raw.get("status", "no_data")
                    state.last_candle_timestamp = raw.get("last_candle_timestamp")
                    state.updated_at = raw.get("updated_at")
                    state.gap_recoveries = int(raw.get("gap_recoveries", 0))
                    state.candles = [Candle(**x) for x in raw.get("candles_1m", [])][-self.max_candles:]
                except (OSError, ValueError, TypeError):
                    state = SymbolState(symbol=symbol, candles=[])
            self._states[symbol] = state
            return state

    def all_states(self, symbols: tuple[str, ...]) -> dict[str, SymbolState]:
        return {s: self.load(s) for s in symbols}

    def upsert(self, symbol: str, candle: Candle) -> bool:
        with self._lock:
            state = self.load(symbol)
            existing = {c.timestamp: c for c in (state.candles or [])}
            changed = existing.get(candle.timestamp) != candle
            existing[candle.timestamp] = candle
            state.candles = sorted(existing.values(), key=lambda c: c.timestamp)[-self.max_candles:]
            state.last_candle_timestamp = state.candles[-1].timestamp if state.candles else None
            state.updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            state.status = "ok"
            state.market_state = "open"
            self._persist(state)
            return changed

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

    def _persist(self, state: SymbolState) -> None:
        payload = json.dumps(state.to_dict(), separators=(",", ":"), ensure_ascii=False)
        path = self._path(state.symbol)
        fd, temp = tempfile.mkstemp(prefix=f".{state.symbol}.", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
