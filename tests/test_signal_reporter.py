from signal_engine.models import Action, Signal
from signal_engine.reporter import alert_actionable, diff_actions


def _signal(symbol: str, action: Action) -> Signal:
    return Signal(symbol=symbol, action=action, confidence=80.0, price=1.0)


def test_diff_actions_flags_everything_as_changed_on_first_poll():
    previous: dict = {}
    signals = [_signal("XAUUSD", Action.WAIT), _signal("EURUSD", Action.BUY)]
    changed, actionable = diff_actions(previous, signals)

    assert {s.symbol for s in changed} == {"XAUUSD", "EURUSD"}
    assert [s.symbol for s in actionable] == ["EURUSD"]
    assert previous == {"XAUUSD": Action.WAIT, "EURUSD": Action.BUY}


def test_diff_actions_reports_no_change_when_actions_are_stable():
    previous = {"XAUUSD": Action.WAIT, "EURUSD": Action.BUY}
    signals = [_signal("XAUUSD", Action.WAIT), _signal("EURUSD", Action.BUY)]
    changed, actionable = diff_actions(previous, signals)

    assert changed == []
    assert [s.symbol for s in actionable] == ["EURUSD"]  # still actionable even though unchanged


def test_diff_actions_flags_a_transition_from_wait_to_buy():
    previous = {"XAUUSD": Action.WAIT}
    signals = [_signal("XAUUSD", Action.BUY)]
    changed, actionable = diff_actions(previous, signals)

    assert [s.symbol for s in changed] == ["XAUUSD"]
    assert [s.symbol for s in actionable] == ["XAUUSD"]
    assert previous == {"XAUUSD": Action.BUY}


def test_diff_actions_flags_a_transition_from_buy_back_to_wait():
    previous = {"XAUUSD": Action.BUY}
    signals = [_signal("XAUUSD", Action.WAIT)]
    changed, actionable = diff_actions(previous, signals)

    assert [s.symbol for s in changed] == ["XAUUSD"]
    assert actionable == []  # changed, but no longer actionable


def test_alert_actionable_never_raises_without_plyer_installed():
    # plyer is an optional dependency; this must degrade silently, not crash
    # the polling loop, whether or not it happens to be installed here.
    alert_actionable([_signal("XAUUSD", Action.BUY)])


def test_alert_actionable_is_a_noop_for_an_empty_list(capsys):
    alert_actionable([])
    captured = capsys.readouterr()
    assert captured.out == ""
