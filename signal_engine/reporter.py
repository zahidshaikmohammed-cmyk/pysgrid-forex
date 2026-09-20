from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .models import Action, Signal

log = logging.getLogger(__name__)

_ACTION_COLOR = {
    Action.BUY: "\033[92m",   # green
    Action.SELL: "\033[91m",  # red
    Action.WAIT: "\033[93m",  # yellow
}
_RESET = "\033[0m"


def format_table(signals: list[Signal], *, color: bool = True) -> str:
    lines = []
    header = f"{'SYMBOL':<9}{'ACTION':<7}{'CONF':>6}  {'PRICE':>12}  REASON"
    lines.append(header)
    lines.append("-" * len(header))
    for s in signals:
        top_reason = s.reasons[0] if s.reasons else ""
        price = f"{s.price:.5f}" if s.price is not None else "-"
        action_text = s.action.value
        if color:
            action_text = f"{_ACTION_COLOR.get(s.action, '')}{action_text:<7}{_RESET}"
        else:
            action_text = f"{action_text:<7}"
        lines.append(f"{s.symbol:<9}{action_text}{s.confidence:>5.0f}%  {price:>12}  {top_reason}")
    return "\n".join(lines)


def format_detail(signal: Signal) -> str:
    lines = [f"{signal.symbol} -> {signal.action.value} (confidence {signal.confidence:.0f}%)"]
    if signal.price is not None:
        lines.append(f"  price: {signal.price:.5f}")
    if signal.suggested_stop_loss is not None:
        lines.append(f"  suggested stop loss:   {signal.suggested_stop_loss:.5f}")
    if signal.suggested_take_profit is not None:
        lines.append(f"  suggested take profit: {signal.suggested_take_profit:.5f}")
    lines.append("  reasons:")
    for r in signal.reasons:
        lines.append(f"    - {r}")
    return "\n".join(lines)


def diff_actions(previous: dict[str, Action], signals: list[Signal]) -> tuple[list[Signal], list[Signal]]:
    """Compares this poll's actions against the last poll's, per symbol.

    Returns (changed, actionable): `changed` is every signal whose action
    differs from what it was last poll (including the very first poll, where
    "previous" is unknown for everything); `actionable` is every signal that
    is currently BUY or SELL, changed or not. `previous` is mutated in place
    to the new state, so the caller just keeps passing the same dict back in.
    """
    changed = []
    actionable = []
    for s in signals:
        if previous.get(s.symbol) != s.action:
            changed.append(s)
        if s.action != Action.WAIT:
            actionable.append(s)
        previous[s.symbol] = s.action
    return changed, actionable


def alert_actionable(signals: list[Signal]) -> None:
    """Best-effort way to notice a real BUY/SELL without staring at the
    terminal: a terminal bell (works everywhere, including PowerShell) plus
    a desktop toast notification if the optional `plyer` package is
    installed (`pip install plyer`). Never raises -- a notification failure
    must not take down the polling loop."""
    if not signals:
        return

    print("\a", end="", flush=True)

    try:
        from plyer import notification

        summary = "; ".join(f"{s.symbol} {s.action.value} ({s.confidence:.0f}%)" for s in signals)
        notification.notify(title="pysgrid-forex signal", message=summary, timeout=20)
    except Exception as exc:  # noqa: BLE001 - a missing/broken notifier must never crash the loop
        log.debug("Desktop notification unavailable (pip install plyer for one): %s", exc)


class DailySignalLog:
    """Appends every generated signal to a per-day JSON file so a day's
    worth of calls can be reviewed or backtested against afterwards."""

    def __init__(self, directory: str):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, dt: datetime) -> Path:
        return self.root / f"{dt.strftime('%Y-%m-%d')}.jsonl"

    def append(self, signal: Signal) -> None:
        path = self._path_for(datetime.now(timezone.utc))
        line = json.dumps(signal.to_dict(), separators=(",", ":"))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def append_many(self, signals: list[Signal]) -> None:
        for s in signals:
            self.append(s)
