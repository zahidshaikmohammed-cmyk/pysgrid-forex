"""Forex signal engine CLI.

Run from the pysgrid-forex repo root:

    python -m signal_engine.main               # continuous, prints every poll
    python -m signal_engine.main --once         # one pass, then exit
    python -m signal_engine.main --detail       # full rationale, not just the table

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
from .feed_client import FeedClient
from .reporter import DailySignalLog, format_detail, format_table
from .strategy import generate_signal

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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument("--detail", action="store_true", help="Print full rationale for every symbol, not just a table")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors (useful in plain PowerShell)")
    parser.add_argument("--api-base", default=None, help="Override PYSGRID_API_BASE")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol override")
    args = parser.parse_args(argv)

    config = EngineConfig.from_env()
    if args.api_base:
        config = replace(config, api_base=args.api_base.rstrip("/"))
    if args.symbols:
        symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
        config = replace(config, symbols=symbols)

    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    print(f"pysgrid-forex signal engine -- feed: {config.api_base}")
    print(f"symbols: {', '.join(config.symbols)}")
    print(
        "This is decision support, not financial advice or an execution bot. "
        "Verify independently before risking real capital.\n"
    )

    signal_log = DailySignalLog(config.signals_dir)

    with FeedClient(config) as feed, CalendarFeed(config) as calendar:
        try:
            while True:
                signals = run_once(config, feed, calendar)
                signal_log.append_many(signals)

                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"\n=== {timestamp} ===")
                if args.detail:
                    for s in signals:
                        print(format_detail(s))
                        print()
                else:
                    print(format_table(signals, color=not args.no_color))

                if args.once:
                    break
                time.sleep(config.poll_interval_seconds)
        except KeyboardInterrupt:
            print("\nStopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
