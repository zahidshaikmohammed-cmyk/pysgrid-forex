from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value /= 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    bid: float | None = None
    ask: float | None = None

    @classmethod
    def from_provider(cls, payload: dict[str, Any]) -> "Candle":
        def pick(*names: str) -> Any:
            for name in names:
                if name in payload and payload[name] is not None:
                    return payload[name]
            raise KeyError(names[0])

        def pick_optional(*names: str) -> Any | None:
            for name in names:
                if name in payload and payload[name] is not None:
                    return payload[name]
            return None

        dt = parse_timestamp(pick("OpenTime", "openTime", "timestamp", "Timestamp", "time"))
        bid_raw = pick_optional("Bid", "bid")
        ask_raw = pick_optional("Ask", "ask")
        candle = cls(
            timestamp=dt.isoformat().replace("+00:00", "Z"),
            open=float(pick("OpenPrice", "openPrice", "open", "Open")),
            high=float(pick("HighPrice", "highPrice", "high", "High")),
            low=float(pick("LowPrice", "lowPrice", "low", "Low")),
            close=float(pick("ClosePrice", "closePrice", "close", "Close")),
            volume=float(pick("Volume", "volume")),
            bid=float(bid_raw) if bid_raw is not None else None,
            ask=float(ask_raw) if ask_raw is not None else None,
        )
        if not (candle.open > 0 and candle.high > 0 and candle.low > 0 and candle.close > 0):
            raise ValueError("OHLC prices must be positive")
        if candle.high < max(candle.open, candle.close):
            raise ValueError("high is below open/close")
        if candle.low > min(candle.open, candle.close):
            raise ValueError("low is above open/close")
        if candle.volume < 0:
            raise ValueError("volume cannot be negative")
        if candle.bid is not None and candle.bid <= 0:
            raise ValueError("bid must be positive")
        if candle.ask is not None and candle.ask <= 0:
            raise ValueError("ask must be positive")
        if candle.bid is not None and candle.ask is not None and candle.ask < candle.bid:
            raise ValueError("ask cannot be below bid")
        return candle

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SymbolState:
    symbol: str
    market_state: str = "unknown"
    status: str = "no_data"
    last_candle_timestamp: str | None = None
    updated_at: str | None = None
    websocket_connected: bool = False
    reconnect_count: int = 0
    gap_recoveries: int = 0
    rejected_count: int = 0
    candles: list[Candle] | None = None

    def to_dict(self, *, m1_valid: bool | None = None) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "market_state": self.market_state,
            "status": self.status,
            "last_candle_timestamp": self.last_candle_timestamp,
            "updated_at": self.updated_at,
            "websocket_connected": self.websocket_connected,
            "reconnect_count": self.reconnect_count,
            "gap_recoveries": self.gap_recoveries,
            "rejected_count": self.rejected_count,
            "candles_1m": [c.as_dict() for c in (self.candles or [])],
        }
        if m1_valid is not None:
            payload["m1_valid"] = m1_valid
        return payload
