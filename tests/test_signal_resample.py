from datetime import datetime, timedelta, timezone

from pysgrid_forex.models import Candle
from signal_engine.resample import resample


def _candle(ts: datetime, close: float) -> Candle:
    return Candle(
        timestamp=ts.isoformat().replace("+00:00", "Z"),
        open=close - 0.1, high=close + 0.2, low=close - 0.3, close=close, volume=1,
    )


def test_resample_1_minute_is_identity():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [_candle(start + timedelta(minutes=i), 100 + i) for i in range(5)]
    assert resample(candles, 1) == candles


def test_resample_builds_complete_5_minute_bar():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)  # aligned to a 5-min boundary
    candles = [_candle(start + timedelta(minutes=i), 100 + i) for i in range(5)]
    result = resample(candles, 5)
    assert len(result) == 1
    bar = result[0]
    assert bar.timestamp == start.isoformat().replace("+00:00", "Z")
    assert bar.open == candles[0].open
    assert bar.close == candles[-1].close
    assert bar.high == max(c.high for c in candles)
    assert bar.low == min(c.low for c in candles)
    assert bar.volume == sum(c.volume for c in candles)


def test_resample_drops_incomplete_trailing_bucket():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = [_candle(start + timedelta(minutes=i), 100 + i) for i in range(3)]  # only 3 of 5 minutes
    result = resample(candles, 5)
    assert result == []


def test_resample_drops_bucket_with_non_contiguous_members():
    """Defensive check: even if a bucket happens to contain the right
    *count* of candles, resample must still verify they're genuinely
    60s-contiguous before treating the bucket as complete."""
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = [_candle(start + timedelta(minutes=i), 100 + i) for i in (0, 1, 2, 3, 4)]
    candles[2] = _candle(start + timedelta(minutes=2, seconds=30), 999)  # off-grid timestamp
    result = resample(candles, 5)
    assert result == []


def test_resample_only_emits_complete_buckets_not_the_forming_one():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = [_candle(start + timedelta(minutes=i), 100 + i) for i in range(7)]  # one full bucket + 2 forming
    result = resample(candles, 5)
    assert len(result) == 1
    assert result[0].timestamp == start.isoformat().replace("+00:00", "Z")
