#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlencode

import websockets

API_KEY = os.environ["REALMARKET_API_KEY"]
SYMBOL = "XAUUSD"
TIMEFRAME = "M1"
URL = "wss://api.realmarketapi.com/price?" + urlencode(
    {"apiKey": API_KEY, "symbolCode": SYMBOL, "timeFrame": TIMEFRAME}
)

async def main() -> None:
    deadline = time.monotonic() + 75
    opens: list[datetime] = []
    messages = 0
    errors = 0
    keys = Counter()

    print("RAW_PROVIDER_PROBE_START")
    print("symbol=XAUUSD timeframe=M1 duration=75s")

    async with websockets.connect(
        URL,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=5,
        max_size=2_000_000,
    ) as ws:
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                print("NO_MESSAGE_FOR_10S")
                continue

            messages += 1
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")

            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                errors += 1
                print("NON_JSON_MESSAGE")
                continue

            if isinstance(body, dict):
                keys.update(body.keys())

            items = body if isinstance(body, list) else [body]
            found = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = None
                for name in ("OpenTime", "openTime", "timestamp", "Timestamp", "time"):
                    if item.get(name) is not None:
                        value = item[name]
                        break
                if value is None:
                    continue
                try:
                    text_value = str(value).replace("Z", "+00:00")
                    dt = datetime.fromisoformat(text_value)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    opens.append(dt)
                    print(
                        "RAW_FRAME",
                        "openTime=", dt.isoformat(),
                        "close=", item.get("ClosePrice", item.get("closePrice", item.get("close"))),
                        "bid=", item.get("Bid", item.get("bid")),
                        "ask=", item.get("Ask", item.get("ask")),
                    )
                    found = True
                except Exception:
                    errors += 1

            if not found:
                print("FRAME_WITHOUT_OPEN_TIME")

    unique = sorted(set(opens))
    print("RAW_MESSAGES:", messages)
    print("PARSE_ERRORS:", errors)
    print("RAW_OPEN_TIMES:", len(unique))
    if len(unique) >= 2:
        gaps = [
            (b - a).total_seconds()
            for a, b in zip(unique, unique[1:])
        ]
        print("RAW_INTERVALS_SECONDS:", gaps)
        print("RAW_NON_60S:", sum(1 for x in gaps if x != 60))
    print("TOP_LEVEL_KEYS:", sorted(keys))
    print("RAW_PROVIDER_PROBE_END")

if __name__ == "__main__":
    asyncio.run(main())
