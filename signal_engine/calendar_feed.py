"""Economic calendar client (news-risk gating), keyless.

Uses a free, community-mirrored redistribution of the ForexFactory weekly
calendar. This is NOT an official, contractually-guaranteed feed -- it is
the same de facto free source most retail EAs/tools rely on, because
ForexFactory has no official public API. Its schema has been observed to
drift over time, so parsing below is defensive (tries several plausible key
names) and, critically, FAILS SAFE: if the feed is unreachable, empty, or
unparseable, this engine treats that as "no calendar data available" and
says so explicitly, rather than silently assuming it's clear of news risk.

Reachability of this specific URL could not be verified from the sandboxed
environment this engine was built in (outbound network access to arbitrary
third-party hosts was not available there). Verify it works from wherever
you actually run this, and swap SIGNAL_CALENDAR_URL if it doesn't.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from .config import EngineConfig

log = logging.getLogger(__name__)

_IMPACT_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass
class EconomicEvent:
    title: str
    currency: str
    impact: str  # "low" | "medium" | "high" | "holiday" | "unknown"
    time_utc: datetime


def _pick(item: dict, *keys: str) -> object | None:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _parse_impact(raw: object) -> str:
    text = str(raw or "").strip().lower()
    if text in ("high", "medium", "low"):
        return text
    if "holiday" in text:
        return "holiday"
    return "unknown"


def _parse_event(item: dict) -> EconomicEvent | None:
    title = _pick(item, "title", "event", "name")
    currency = _pick(item, "country", "currency")
    impact = _parse_impact(_pick(item, "impact", "importance"))
    raw_time = _pick(item, "date", "datetime", "time", "timestamp")
    if title is None or currency is None or raw_time is None:
        return None
    try:
        text = str(raw_time).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None
    return EconomicEvent(title=str(title), currency=str(currency).upper(), impact=impact, time_utc=dt)


class CalendarFeed:
    def __init__(self, config: EngineConfig, client: httpx.Client | None = None):
        self.config = config
        self._client = client or httpx.Client(timeout=10.0)
        self._owns_client = client is None
        self._cache: list[EconomicEvent] = []
        self._cached_at: float = 0.0
        self._last_error: str | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "CalendarFeed":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def events(self, *, force_refresh: bool = False) -> list[EconomicEvent]:
        if not self.config.calendar_enabled:
            return []

        age = time.monotonic() - self._cached_at
        if not force_refresh and self._cache and age < self.config.calendar_refresh_seconds:
            return self._cache

        try:
            response = self._client.get(self.config.calendar_url)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._last_error = f"calendar fetch failed: {exc!r}"
            log.warning(self._last_error)
            return self._cache  # serve stale cache rather than nothing, if we have it

        items = body if isinstance(body, list) else body.get("events", []) if isinstance(body, dict) else []
        events = [e for e in (_parse_event(i) for i in items if isinstance(i, dict)) if e is not None]

        if not events:
            self._last_error = "calendar feed returned no parseable events"
            log.warning(self._last_error)
            return self._cache

        self._last_error = None
        self._cache = events
        self._cached_at = time.monotonic()
        return events

    def events_near(
        self,
        currencies: set[str],
        at: datetime,
        *,
        before_minutes: int,
        after_minutes: int,
        min_impact: str = "high",
    ) -> list[EconomicEvent]:
        window_start = at - timedelta(minutes=before_minutes)
        window_end = at + timedelta(minutes=after_minutes)
        min_rank = _IMPACT_RANK.get(min_impact, 3)

        return [
            e
            for e in self.events()
            if e.currency in currencies
            and window_start <= e.time_utc <= window_end
            and _IMPACT_RANK.get(e.impact, 0) >= min_rank
        ]


def currency_codes_for_symbol(symbol: str) -> set[str]:
    symbol = symbol.upper()
    special = {"XAUUSD": {"USD"}, "XAGUSD": {"USD"}, "USOIL": {"USD"}}
    if symbol in special:
        return special[symbol]
    if len(symbol) == 6:
        return {symbol[:3], symbol[3:]}
    return set()
