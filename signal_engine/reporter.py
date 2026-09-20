from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import Action, Signal

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
