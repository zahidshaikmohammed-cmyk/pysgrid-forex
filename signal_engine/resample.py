"""Gap-aware timeframe aggregation.

pysgrid-forex guarantees every M1 candle it serves is either a genuine
60-second continuation of its predecessor or an explicitly recorded gap
(never a fabricated bar). Building M5/M15/H1/H4 bars on top of that series
has to preserve the same guarantee: a higher-timeframe bar is only emitted
when EVERY minute inside it is present and contiguous. A bucket that
straddles a gap, or is still forming, is dropped rather than approximated --
this engine never synthesizes a bar from partial data.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pysgrid_forex.models import Candle, parse_timestamp


def resample(candles: list[Candle], timeframe_minutes: int) -> list[Candle]:
    if timeframe_minutes <= 1:
        return list(candles)

    bucket_seconds = timeframe_minutes * 60
    buckets: dict[datetime, list[Candle]] = {}

    for candle in candles:
        ts = parse_timestamp(candle.timestamp)
        epoch = int(ts.timestamp())
        bucket_epoch = epoch - (epoch % bucket_seconds)
        bucket_start = datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)
        buckets.setdefault(bucket_start, []).append(candle)

    result: list[Candle] = []
    for bucket_start in sorted(buckets):
        members = sorted(buckets[bucket_start], key=lambda c: c.timestamp)

        if len(members) != timeframe_minutes:
            continue  # incomplete (still forming) or broken by a gap

        if parse_timestamp(members[0].timestamp) != bucket_start:
            continue  # first minute of the bucket is missing

        contiguous = all(
            (parse_timestamp(b.timestamp) - parse_timestamp(a.timestamp)).total_seconds() == 60
            for a, b in zip(members, members[1:])
        )
        if not contiguous:
            continue

        result.append(
            Candle(
                timestamp=bucket_start.isoformat().replace("+00:00", "Z"),
                open=members[0].open,
                high=max(m.high for m in members),
                low=min(m.low for m in members),
                close=members[-1].close,
                volume=sum(m.volume for m in members),
            )
        )

    return result


def resample_all(candles: list[Candle], timeframes_minutes: tuple[int, ...]) -> dict[int, list[Candle]]:
    return {tf: resample(candles, tf) for tf in timeframes_minutes}
