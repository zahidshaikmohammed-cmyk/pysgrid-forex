"""Forex signal engine CLI.

Run from the pysgrid-forex repo root. Put persistent settings (API base,
Telegram credentials, etc.) in a `.env` file there instead of retyping
`$env:X = ...` in every fresh terminal -- see .env.example. Anything you
DO set with `$env:` still overrides the file.

    python -m signal_engine.main               # continuous, quiet until something changes
    python -m signal_engine.main --once         # one pass, then exit
    python -m signal_engine.main --detail       # full rationale, not just the table
    python -m signal_engine.main --always       # print every poll, even with no change
    python -m signal_engine.main --test-telegram  # verify Telegram alerts, then exit

By default, continuous mode does NOT reprint the full table every poll --
that just trains you to stop looking at it. It only prints in full when a
symbol's action actually changes or a BUY/SELL is currently live, and rings
the terminal bell (plus a desktop notification if `plyer` is installed) the
moment a real BUY/SELL appears. Every poll is still logged to disk either
way, so nothing is lost between the terminal lines you do see.

This is a decision-support tool: it reads the validated M1 feed pysgrid-forex
serves, and outputs BUY/SELL/WAIT calls with an explicit rationale and
ATR-based stop/target suggestions. It does not place trades, does not touch
a broker, and does not guarantee an outcome. Treat every signal as a
starting point for your own judgement and risk management, not an order.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone

from .calendar_feed import CalendarFeed
from .config import EngineConfig
from .dotenv import load_dotenv
from .feed_client import FeedClient
from .models import Action
from .reporter import DailySignalLog, alert_actionable, diff_actions, format_detail, format_table
from .strategy import generate_signal
from .telegram_notifier import TelegramNotifier

log = logging.getLogger("signal_engine")


def run_once(config: EngineConfig, feed: FeedClient, calendar: CalendarFeed | None):
    snapshots = feed.fetch_all()
    now = datetime.now(timezone.utc)
    signals = []
    for symbol in config.symbols:
        snapshot = snapshots.get(symbol)
        signal = generate_signal(symbol, snapshot, config, calendar, now=now)
        signals.append(signal)
    return signals


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument("--detail", action="store_true", help="Print full rationale for every symbol, not just a table")
    parser.add_argument("--always", action="store_true", help="Print every poll in continuous mode, even with no change")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors (useful in plain PowerShell)")
    parser.add_argument("--api-base", default=None, help="Override PYSGRID_API_BASE")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol override")
    parser.add_argument(
        "--test-telegram", action="store_true",
        help="Send one test Telegram message using TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID, then exit",
    )
    args = parser.parse_args(argv)

    config = EngineConfig.from_env()
    if args.api_base:
        config = replace(config, api_base=args.api_base.rstrip("/"))
    if args.symbols:
        symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
        config = replace(config, symbols=symbols)

    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.test_telegram:
        with TelegramNotifier(config) as telegram:
            if not telegram.enabled:
                print("Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
                return 1
            ok = telegram.send("pysgrid-forex signal engine: Telegram alerts are working.")
            print("Sent OK -- check Telegram." if ok else "Send failed -- check the warning above for why.")
            return 0 if ok else 1

    print(f"pysgrid-forex signal engine -- feed: {config.api_base}")
    print(f"symbols: {', '.join(config.symbols)}")
    print(
        "This is decision support, not financial advice or an execution bot. "
        "Verify independently before risking real capital.\n"
    )

    signal_log = DailySignalLog(config.signals_dir)
    previous_actions: dict[str, Action] = {}
    first_poll = True

    with FeedClient(config) as feed, CalendarFeed(config) as calendar, TelegramNotifier(config) as telegram:
        print(f"Telegram alerts: {'enabled' if telegram.enabled else 'disabled (see --test-telegram)'}\n")
        try:
            while True:
                signals = run_once(config, feed, calendar)
                signal_log.append_many(signals)
                changed, actionable = diff_actions(previous_actions, signals)

                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                should_print = args.once or args.always or first_poll or bool(changed) or bool(actionable)

                if should_print:
                    print(f"\n=== {timestamp} ===")
                    if args.detail:
                        for s in signals:
                            print(format_detail(s))
                            print()
                    else:
                        print(format_table(signals, color=not args.no_color))
                else:
                    waiting = sum(1 for s in signals if s.action == Action.WAIT)
                    print(f"[{timestamp}] no change ({waiting}/{len(signals)} WAIT)")

                if actionable:
                    alert_actionable(actionable)
                    telegram.notify_signals(actionable)

                first_poll = False
                if args.once:
                    break
                time.sleep(config.poll_interval_seconds)
        except KeyboardInterrupt:
            print("\nStopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
