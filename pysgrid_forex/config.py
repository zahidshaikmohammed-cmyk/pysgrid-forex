from __future__ import annotations

import os
from dataclasses import dataclass, field


DEFAULT_SYMBOLS = (
    "XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY",
    "AUDUSD", "USDCAD", "NZDUSD", "XAGUSD", "USOIL",
)


def _csv(value: str) -> tuple[str, ...]:
    return tuple(x.strip().upper() for x in value.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    symbols: tuple[str, ...] = field(default_factory=lambda: DEFAULT_SYMBOLS)
    timeframe: str = "M1"
    data_dir: str = "./data"
    host: str = "0.0.0.0"
    port: int = 8080
    max_candles: int = 1500
    stale_seconds: int = 120
    reconnect_max_seconds: int = 30
    rest_timeout_seconds: float = 15.0
    log_level: str = "INFO"
    rest_base: str = "https://api.realmarketapi.com/api/v1"
    # RealMarketAPI exposes a dedicated candle WebSocket. Use it for the
    # completed OHLCV feed rather than the price/ticker stream.
    ws_base: str = "wss://api.realmarketapi.com/ws/candles"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_key=os.getenv("REALMARKET_API_KEY", "").strip(),
            symbols=_csv(os.getenv("PYSGRID_SYMBOLS", ",".join(DEFAULT_SYMBOLS))) or DEFAULT_SYMBOLS,
            timeframe=os.getenv("PYSGRID_TIMEFRAME", "M1").upper(),
            data_dir=os.getenv("PYSGRID_DATA_DIR", "./data"),
            host=os.getenv("PYSGRID_HOST", "0.0.0.0"),
            port=int(os.getenv("PYSGRID_PORT", "8080")),
            max_candles=int(os.getenv("PYSGRID_MAX_CANDLES", "1500")),
            stale_seconds=int(os.getenv("PYSGRID_STALE_SECONDS", "120")),
            reconnect_max_seconds=int(os.getenv("PYSGRID_RECONNECT_MAX_SECONDS", "30")),
            rest_timeout_seconds=float(os.getenv("PYSGRID_REST_TIMEOUT_SECONDS", "15")),
            log_level=os.getenv("PYSGRID_LOG_LEVEL", "INFO").upper(),
        )
