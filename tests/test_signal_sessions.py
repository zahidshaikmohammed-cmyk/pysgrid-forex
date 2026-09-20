from datetime import datetime, timezone

from signal_engine.sessions import is_weekend_closed, session_label, session_weight


def _dt(y=2026, m=1, d=5, h=0, minute=0) -> datetime:  # 2026-01-05 is a Monday
    return datetime(y, m, d, h, minute, tzinfo=timezone.utc)


def test_saturday_is_weekend_closed():
    saturday = _dt(2026, 1, 3, 12)  # 2026-01-03 is a Saturday
    assert is_weekend_closed(saturday)
    assert session_weight(saturday, "EURUSD") == 0.0


def test_sunday_before_reopen_is_closed_but_after_is_open():
    sunday_early = _dt(2026, 1, 4, 10)
    sunday_late = _dt(2026, 1, 4, 23)
    assert is_weekend_closed(sunday_early)
    assert not is_weekend_closed(sunday_late)


def test_friday_after_close_is_weekend_closed():
    friday_late = _dt(2026, 1, 2, 23)  # 2026-01-02 is a Friday
    assert is_weekend_closed(friday_late)


def test_london_ny_overlap_is_highest_weight():
    overlap = _dt(h=13)  # Monday 13:00 UTC -> London + NY both active
    assert session_label(overlap) == "london_ny_overlap"
    assert session_weight(overlap, "EURUSD") == 1.0


def test_asian_session_boosts_jpy_pairs():
    asian = _dt(h=1)  # Monday 01:00 UTC -> Tokyo/Sydney window
    assert session_label(asian) == "asian"
    jpy_weight = session_weight(asian, "USDJPY")
    default_weight = session_weight(asian, "EURUSD")
    assert jpy_weight > default_weight


def test_gold_gets_reduced_weight_in_asian_session():
    asian = _dt(h=1)
    gold_weight = session_weight(asian, "XAUUSD")
    default_weight = session_weight(asian, "EURUSD")
    assert gold_weight < default_weight
