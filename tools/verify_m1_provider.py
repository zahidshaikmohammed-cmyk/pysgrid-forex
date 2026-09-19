#!/usr/bin/env python3
"""Independently verify RealMarketAPI's advertised M1 WebSocket cadence.

This is a permanent operational tool, not a one-off diagnostic to delete
after use. Run it with real production credentials whenever the provider's
endpoint, plan, or documented behaviour changes, and before trusting a new
symbol's feed:

    REALMARKET_API_KEY=... python3 tools/verify_m1_provider.py --symbols XAUUSD,EURUSD

It connects directly to the configured WebSocket endpoint (bypassing the
application's own aggregation/validation code entirely, so it cannot be
fooled by a bug in this codebase) and requires observing, per symbol, at
least two consecutive completed candle boundaries that are exactly 60
seconds apart before declaring that symbol PASS. Anything else -- a
5-minute cadence, irregular spacing, or no data within the timeout -- is
reported as FAIL for that symbol.

Passing `timeFrame=M1` in the query string is not evidence of anything;
this script only trusts what it actually observes on the wire.

Exit code is 0 only if every requested symbol PASSes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from urllib.parse import urlencode

import websockets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pysgrid_forex.models import Candle, parse_timestamp  # noqa: E402


def _iter_updates(body: object) -> list[dict]:
    if isinstance(body, dict):
        for key in ("message", "payload", "result", "data", "Data", "candle", "Candle"):
            nested = body.get(key)
            if isinstance(nested, (dict, list)):
                return _iter_updates(nested)
        return [body]
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    return []


async def probe_symbol(
    ws_base: str, api_key: str, symbol: str, timeframe: str, timeout: float
) -> tuple[str, bool, str]:
    url = f"{ws_base}?" + urlencode({"apiKey": api_key, "symbolCode": symbol, "timeFrame": timeframe})
    open_times: list[datetime] = []
    last_open: datetime | None = None
    deadline = asyncio.get_event_loop().time() + timeout

    try:
        async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5) as ws:
            while asyncio.get_event_loop().time() < deadline:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for item in _iter_updates(body):
                    try:
                        candle = Candle.from_provider(item)
                    except (KeyError, ValueError, TypeError):
                        continue
                    ts = parse_timestamp(candle.timestamp).replace(second=0, microsecond=0)
                    if last_open is None or ts != last_open:
                        open_times.append(ts)
                        last_open = ts
                    if len(open_times) >= 3:
                        break
                if len(open_times) >= 3:
                    break
    except Exception as exc:  # noqa: BLE001 - report, don't crash the sweep
        return symbol, False, f"connection error: {exc!r}"

    if len(open_times) < 2:
        return symbol, False, f"only observed {len(open_times)} distinct OpenTime bucket(s) in {timeout:.0f}s"

    gaps = [
        (b - a).total_seconds() for a, b in zip(open_times, open_times[1:])
    ]
    if all(g == 60 for g in gaps):
        return symbol, True, f"confirmed 60s cadence across {len(open_times)} boundaries: {gaps}"
    return symbol, False, f"non-M1 spacing observed: {gaps} (open times: {[t.isoformat() for t in open_times]})"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default="XAUUSD", help="Comma-separated symbol codes to probe")
    parser.add_argument("--timeframe", default="M1")
    parser.add_argument("--timeout", type=float, default=150.0, help="Seconds to wait per symbol")
    parser.add_argument(
        "--ws-base",
        default=os.environ.get("PYSGRID_WS_BASE", "wss://api.realmarketapi.com/candles"),
    )
    args = parser.parse_args()

    api_key = os.environ.get("REALMARKET_API_KEY", "").strip()
    if not api_key:
        print("REALMARKET_API_KEY is not set; cannot probe the live provider.", file=sys.stderr)
        return 2

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    results = await asyncio.gather(
        *(probe_symbol(args.ws_base, api_key, s, args.timeframe, args.timeout) for s in symbols)
    )

    ok = True
    summary: dict[str, bool] = {}
    for symbol, passed, detail in results:
        summary[symbol] = passed
        ok = ok and passed
        print(f"{'PASS' if passed else 'FAIL'} {symbol}: {detail}")

    print("VERIFY_M1_PROVIDER_SUMMARY:", json.dumps(summary))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
