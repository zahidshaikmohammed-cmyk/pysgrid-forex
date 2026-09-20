from datetime import datetime, timezone

import httpx

from signal_engine.calendar_feed import CalendarFeed, currency_codes_for_symbol
from signal_engine.config import EngineConfig


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_currency_codes_for_standard_pair():
    assert currency_codes_for_symbol("EURUSD") == {"EUR", "USD"}


def test_currency_codes_for_metals_and_oil_are_usd():
    assert currency_codes_for_symbol("XAUUSD") == {"USD"}
    assert currency_codes_for_symbol("XAGUSD") == {"USD"}
    assert currency_codes_for_symbol("USOIL") == {"USD"}


def test_parses_events_and_filters_by_currency_time_and_impact():
    payload = [
        {"title": "Non-Farm Payrolls", "country": "USD", "impact": "High", "date": "2026-01-05T13:30:00Z"},
        {"title": "Retail Sales", "country": "EUR", "impact": "Low", "date": "2026-01-05T13:30:00Z"},
        {"title": "Some Speech", "country": "USD", "impact": "Medium", "date": "2026-01-06T09:00:00Z"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    config = EngineConfig(calendar_enabled=True)
    with CalendarFeed(config, client=_client(handler)) as feed:
        events = feed.events()
        assert len(events) == 3

        at = datetime(2026, 1, 5, 13, 30, tzinfo=timezone.utc)
        near = feed.events_near({"USD"}, at, before_minutes=30, after_minutes=15, min_impact="high")
        assert len(near) == 1
        assert near[0].title == "Non-Farm Payrolls"

        # Medium impact shouldn't clear a high-impact filter
        near_low_bar = feed.events_near({"EUR"}, at, before_minutes=30, after_minutes=15, min_impact="high")
        assert near_low_bar == []


def test_disabled_calendar_returns_no_events_without_a_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[])

    config = EngineConfig(calendar_enabled=False)
    with CalendarFeed(config, client=_client(handler)) as feed:
        assert feed.events() == []
        assert calls == []


def test_network_failure_degrades_gracefully_and_reports_last_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    config = EngineConfig()
    with CalendarFeed(config, client=_client(handler)) as feed:
        events = feed.events()
        assert events == []
        assert feed.last_error is not None


def test_stale_cache_is_served_when_a_later_refresh_fails():
    payload = [{"title": "CPI", "country": "USD", "impact": "High", "date": "2026-01-05T13:30:00Z"}]
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=payload)

    config = EngineConfig(calendar_refresh_seconds=0)
    with CalendarFeed(config, client=_client(handler)) as feed:
        first = feed.events()
        assert len(first) == 1

        state["fail"] = True
        second = feed.events(force_refresh=True)
        assert second == first  # stale cache served instead of an empty result
        assert feed.last_error is not None
