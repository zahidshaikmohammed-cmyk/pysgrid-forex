"""FX session-clock awareness: which markets are open right now, how liquid
that makes a given instrument, and whether the week is even open.

Session hours are the standard, widely-published approximations (Sydney,
Tokyo, London, New York) in UTC. They shift by roughly an hour across
daylight-saving transitions in each region; this module does not attempt to
track individual DST calendars, so treat the boundaries as +/-1 hour fuzzy,
not exact to the minute.
"""
from __future__ import annotations

from datetime import datetime, timezone

# (start_hour, end_hour) in UTC, wrapping past midnight where end < start.
SESSION_WINDOWS: dict[str, tuple[int, int]] = {
    "sydney": (21, 6),
    "tokyo": (23, 8),
    "london": (7, 16),
    "new_york": (12, 21),
}

_BASE_WEIGHTS = {
    "london_ny_overlap": 1.0,
    "london": 0.85,
    "new_york": 0.75,
    "tokyo_london_overlap": 0.6,
    "asian": 0.5,
    "closed_low_liquidity": 0.2,
}


def _in_window(hour: int, start: int, end: int) -> bool:
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps midnight


def is_weekend_closed(dt_utc: datetime) -> bool:
    """Forex is closed roughly Fri 22:00 UTC -> Sun 22:00 UTC."""
    weekday, hour = dt_utc.weekday(), dt_utc.hour
    if weekday == 5:  # Saturday
        return True
    if weekday == 6 and hour < 22:  # Sunday before reopen
        return True
    if weekday == 4 and hour >= 22:  # Friday after close
        return True
    return False


def active_sessions(dt_utc: datetime) -> set[str]:
    return {name for name, (start, end) in SESSION_WINDOWS.items() if _in_window(dt_utc.hour, start, end)}


def session_label(dt_utc: datetime) -> str:
    if is_weekend_closed(dt_utc):
        return "weekend_closed"

    sessions = active_sessions(dt_utc)
    if {"london", "new_york"} <= sessions:
        return "london_ny_overlap"
    if "london" in sessions:
        return "london"
    if "new_york" in sessions:
        return "new_york"
    if {"tokyo", "london"} <= sessions:
        return "tokyo_london_overlap"
    if sessions & {"tokyo", "sydney"}:
        return "asian"
    return "closed_low_liquidity"


def session_weight(dt_utc: datetime, symbol: str) -> float:
    """0.0 (don't trade) to 1.0 (prime liquidity for this instrument right
    now). This is a heuristic, not a guarantee of actual spread/liquidity --
    it encodes well-known FX market conventions (gold and majors are most
    active in the London/New York overlap; JPY and AUD/NZD pairs get a
    relative boost during their home Asian sessions)."""
    label = session_label(dt_utc)
    if label == "weekend_closed":
        return 0.0

    weight = _BASE_WEIGHTS.get(label, 0.3)
    symbol = symbol.upper()

    if "JPY" in symbol and label in {"asian", "tokyo_london_overlap"}:
        weight = min(1.0, weight + 0.15)
    if ("AUD" in symbol or "NZD" in symbol) and label == "asian":
        weight = min(1.0, weight + 0.15)
    if symbol in {"XAUUSD", "XAGUSD"} and label == "asian":
        weight = max(0.0, weight - 0.1)

    return weight


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
