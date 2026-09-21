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

    # M5 pipeline: RealMarketAPI's WebSockets were confirmed to deliver
    # genuine native 5-minute candles (see README's Data-integrity rules).
    # This reuses the exact same WebSocket connections as the M1 pipeline
    # (no new connections opened -- the account's concurrent-connection
    # limit is already fully used by the M1 pipeline's one-per-symbol
    # connections). 24h of 5-minute candles is 288; default keeps a margin.
    m5_data_dir: str = "./data-m5"
    m5_max_candles: int = 300
    m5_stale_seconds: int = 600
    reconnect_max_seconds: int = 30
    rest_timeout_seconds: float = 15.0
    log_level: str = "INFO"
    rest_base: str = "https://api.realmarketapi.com/api/v1"
    # RealMarketAPI's WebSocket candle-stream endpoint for timeFrame=M1.
    #
    # IMPORTANT: this endpoint's true bar resolution has NOT been
    # independently confirmed against production credentials from within
    # this codebase's CI/dev environment (no outbound network access to
    # api.realmarketapi.com and no API key are available there). The
    # sibling REST endpoint (`rest_base` + "/candle") is *known*, from a
    # prior live probe, to silently return 5-minute-spaced bars even when
    # timeFrame=M1 is requested -- so timeFrame=M1 alone must never be
    # trusted as proof of genuine 1-minute resolution on this endpoint
    # either. Run `tools/verify_m1_provider.py` against a real API key
    # before depending on this feed. Independently of that verification,
    # the runtime (engine.py + store.py) refuses to persist any candle as
    # valid M1 unless it is confirmed to land exactly 60 seconds after the
    # previous one, so a mislabeled feed is rejected rather than silently
    # accepted regardless of what this endpoint turns out to deliver.
    ws_base: str = "wss://api.realmarketapi.com/candles"

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
            m5_data_dir=os.getenv("PYSGRID_M5_DATA_DIR", "./data-m5"),
            m5_max_candles=int(os.getenv("PYSGRID_M5_MAX_CANDLES", "300")),
            m5_stale_seconds=int(os.getenv("PYSGRID_M5_STALE_SECONDS", "600")),
        )
