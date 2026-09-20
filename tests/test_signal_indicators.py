from signal_engine.indicators import atr, bollinger_bands, ema, last_valid, macd, rsi, volume_zscore
from pysgrid_forex.models import Candle


def test_ema_needs_full_period_before_producing_a_value():
    values = [1.0, 2.0, 3.0]
    result = ema(values, period=5)
    assert result == [None, None, None]


def test_ema_matches_hand_computed_values():
    values = [1, 2, 3, 4, 5, 6]
    result = ema(values, period=3)
    assert result[0] is None and result[1] is None
    assert result[2] == (1 + 2 + 3) / 3
    k = 2 / 4
    expected_3 = 4 * k + result[2] * (1 - k)
    assert abs(result[3] - expected_3) < 1e-9


def test_rsi_all_gains_is_100():
    closes = [float(i) for i in range(1, 20)]  # strictly increasing
    result = rsi(closes, period=14)
    assert last_valid(result) == 100.0


def test_rsi_all_losses_is_0():
    closes = [float(i) for i in range(20, 1, -1)]  # strictly decreasing
    result = rsi(closes, period=14)
    assert last_valid(result) == 0.0


def test_macd_histogram_positive_when_a_fresh_uptrend_begins():
    # A long-established, perfectly constant-slope trend makes both EMAs
    # (and therefore the histogram) converge towards zero -- that's just
    # how EMA lag works on a linear ramp, not a bug. A histogram that's
    # actually informative needs a *change* in trend to react to: flat,
    # then a fresh strong rise.
    closes = [100.0] * 30 + [100.0 + i * 1.0 for i in range(1, 31)]
    _, _, hist = macd(closes, fast=12, slow=26, signal=9)
    assert last_valid(hist) > 0


def test_atr_positive_for_moving_market():
    candles = [
        Candle(f"t{i}", open=100 + i, high=101 + i, low=99 + i, close=100.5 + i, volume=1)
        for i in range(20)
    ]
    result = atr(candles, period=14)
    assert last_valid(result) is not None
    assert last_valid(result) > 0


def test_bollinger_bands_upper_above_lower():
    closes = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 100, 101, 99, 102, 98, 103, 97, 104, 96, 105]
    mid, upper, lower = bollinger_bands(closes, period=10, num_std=2.0)
    assert last_valid(mid) is not None
    assert last_valid(upper) > last_valid(mid) > last_valid(lower)


def test_volume_zscore_flags_a_spike():
    # A perfectly constant history has zero variance, which makes a z-score
    # mathematically undefined -- the function defensively returns 0.0 for
    # that case rather than dividing by zero. To see the spike-detection
    # behavior itself, the trailing window needs some natural spread.
    baseline = [10, 11, 9, 10, 12, 8, 11, 9, 10, 11, 9, 12, 8, 10, 11, 9, 10, 12, 8, 11]
    volumes = [float(v) for v in baseline] + [100.0]
    result = volume_zscore(volumes, lookback=20)
    assert last_valid(result) > 3


def test_last_valid_returns_none_for_all_none():
    assert last_valid([None, None, None]) is None
