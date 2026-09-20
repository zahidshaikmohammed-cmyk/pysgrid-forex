from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_SYMBOLS = (
    "XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY",
    "AUDUSD", "USDCAD", "NZDUSD", "XAGUSD", "USOIL",
)

# Community-mirrored, key-free redistribution of the ForexFactory economic
# calendar. This is the de facto free source most retail trading tools use
# because ForexFactory itself has no official public API. It is NOT an
# official/guaranteed feed: verify it is actually reachable and current from
# wherever you run this (it could not be reached from the sandboxed
# environment this engine was built in -- see docs/SIGNAL_ENGINE.md).
DEFAULT_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def _csv(value: str) -> tuple[str, ...]:
    return tuple(x.strip().upper() for x in value.split(",") if x.strip())


@dataclass(frozen=True)
class EngineConfig:
    # Where the validated M1 feed lives. Point this at your Oracle
    # deployment's public URL when not running against a local dev server.
    api_base: str = "http://127.0.0.1:8080"
    symbols: tuple[str, ...] = field(default_factory=lambda: DEFAULT_SYMBOLS)

    poll_interval_seconds: int = 60

    # Indicator parameters
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14
    bollinger_period: int = 20
    bollinger_stddev: float = 2.0
    swing_lookback: int = 2  # bars each side for fractal swing detection
    volume_lookback: int = 20

    # Confluence timeframes built by resampling the validated M1 series.
    # M1 is the execution/trigger timeframe; higher ones set trend bias.
    timeframes_minutes: tuple[int, ...] = (1, 5, 15, 60, 240)
    trend_timeframe_minutes: int = 240
    trigger_timeframe_minutes: int = 15

    # Economic calendar / news-risk gating
    calendar_url: str = DEFAULT_CALENDAR_URL
    calendar_refresh_seconds: int = 900
    high_impact_blackout_minutes_before: int = 30
    high_impact_blackout_minutes_after: int = 15
    calendar_enabled: bool = True

    min_confidence_to_trade: float = 60.0

    signals_dir: str = "./signals"
    log_level: str = "INFO"

    # Telegram alerts (optional). Both must be set for this channel to be
    # active; leaving either blank silently disables it, other alert paths
    # (terminal bell, plyer desktop toast) still work regardless.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_env(cls) -> "EngineConfig":
        return cls(
            api_base=os.getenv("PYSGRID_API_BASE", "http://127.0.0.1:8080").rstrip("/"),
            symbols=_csv(os.getenv("SIGNAL_SYMBOLS", ",".join(DEFAULT_SYMBOLS))) or DEFAULT_SYMBOLS,
            poll_interval_seconds=int(os.getenv("SIGNAL_POLL_SECONDS", "60")),
            calendar_url=os.getenv("SIGNAL_CALENDAR_URL", DEFAULT_CALENDAR_URL),
            calendar_enabled=os.getenv("SIGNAL_CALENDAR_ENABLED", "true").strip().lower() != "false",
            min_confidence_to_trade=float(os.getenv("SIGNAL_MIN_CONFIDENCE", "60")),
            signals_dir=os.getenv("SIGNAL_LOG_DIR", "./signals"),
            log_level=os.getenv("SIGNAL_LOG_LEVEL", "INFO").upper(),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        )
