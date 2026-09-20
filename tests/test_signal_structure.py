from pysgrid_forex.models import Candle
from signal_engine.structure import (
    Trend,
    ema_trend_bias,
    fibonacci_zone,
    find_swing_highs,
    find_swing_lows,
    is_bearish_engulfing,
    is_bearish_pin_bar,
    is_bullish_engulfing,
    is_bullish_pin_bar,
    last_impulse_leg,
    price_in_pullback_zone,
)


def _c(i: int, open_: float, high: float, low: float, close: float, volume: float = 1) -> Candle:
    return Candle(f"t{i}", open_, high, low, close, volume)


def test_find_swing_highs_detects_a_local_peak():
    candles = [
        _c(0, 1, 1.0, 0.9, 1.0),
        _c(1, 1, 1.2, 1.0, 1.1),
        _c(2, 1, 1.5, 1.1, 1.3),  # peak
        _c(3, 1, 1.2, 1.0, 1.1),
        _c(4, 1, 1.0, 0.8, 0.9),
    ]
    assert find_swing_highs(candles, lookback=2) == [2]


def test_find_swing_lows_detects_a_local_trough():
    candles = [
        _c(0, 1, 1.5, 1.3, 1.4),
        _c(1, 1, 1.3, 1.1, 1.2),
        _c(2, 1, 1.1, 0.8, 0.9),  # trough
        _c(3, 1, 1.3, 1.1, 1.2),
        _c(4, 1, 1.5, 1.3, 1.4),
    ]
    assert find_swing_lows(candles, lookback=2) == [2]


def test_ema_trend_bias_up_requires_full_stack_alignment():
    assert ema_trend_bias(price=110, ema_fast=105, ema_slow=100, ema_trend=95) == Trend.UP
    assert ema_trend_bias(price=110, ema_fast=100, ema_slow=105, ema_trend=95) == Trend.RANGE  # fast below slow
    assert ema_trend_bias(price=110, ema_fast=None, ema_slow=100, ema_trend=95) == Trend.RANGE


def test_ema_trend_bias_down_requires_full_stack_alignment():
    assert ema_trend_bias(price=90, ema_fast=95, ema_slow=100, ema_trend=105) == Trend.DOWN


def test_last_impulse_leg_up_uses_most_recent_low_to_high():
    candles = [_c(i, 1, 1, 1, 1) for i in range(10)]
    swing_lows = [1, 5]
    swing_highs = [3, 8]
    candles[5] = _c(5, 1, 1.1, 0.5, 1.0)   # most recent low
    candles[8] = _c(8, 1, 2.0, 1.5, 1.9)   # most recent high, after the low
    leg = last_impulse_leg(candles, swing_highs, swing_lows, Trend.UP)
    assert leg is not None
    assert leg.start_price == 0.5
    assert leg.end_price == 2.0


def test_fibonacci_zone_and_pullback_membership_for_uptrend():
    candles = [_c(i, 1, 1, 1, 1) for i in range(10)]
    candles[1] = _c(1, 1, 1.1, 0.0, 1.0)    # low = 0.0
    candles[3] = _c(3, 1, 100.0, 90.0, 99.0)  # high = 100.0
    leg = last_impulse_leg(candles, [3], [1], Trend.UP)
    zone_low, zone_high = fibonacci_zone(leg)
    assert zone_low == 100.0 * (1 - 0.618)
    assert zone_high == 100.0 * (1 - 0.382)
    assert price_in_pullback_zone(50.0, (zone_low, zone_high))
    assert not price_in_pullback_zone(99.0, (zone_low, zone_high))


def test_candlestick_patterns():
    bearish_prev = _c(0, open_=10, high=10.2, low=9.0, close=9.2)
    bullish_engulf = _c(1, open_=9.0, high=10.5, low=8.9, close=10.3)
    assert is_bullish_engulfing(bearish_prev, bullish_engulf)
    assert not is_bearish_engulfing(bearish_prev, bullish_engulf)

    bullish_prev = _c(0, open_=9.2, high=10.2, low=9.0, close=10.0)
    bearish_engulf = _c(1, open_=10.1, high=10.2, low=8.5, close=8.8)
    assert is_bearish_engulfing(bullish_prev, bearish_engulf)

    bullish_pin = _c(0, open_=10.0, high=10.05, low=9.0, close=10.02)
    assert is_bullish_pin_bar(bullish_pin)
    assert not is_bearish_pin_bar(bullish_pin)

    bearish_pin = _c(0, open_=10.0, high=11.0, low=9.95, close=9.98)
    assert is_bearish_pin_bar(bearish_pin)
