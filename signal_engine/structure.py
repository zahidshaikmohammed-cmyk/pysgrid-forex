"""Price structure: swing points, trend bias, pullback (Fibonacci) zones,
and a handful of well-known candlestick confirmation patterns.

Nothing here is a secret indicator -- fractal swing points, EMA-alignment
trend bias, and Fibonacci retracement zones are standard, widely documented
price-action tools. The value this module adds is combining them
mechanically and consistently, every bar, without fatigue or bias -- not
inventing new technical analysis.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pysgrid_forex.models import Candle


class Trend(str, Enum):
    UP = "up"
    DOWN = "down"
    RANGE = "range"


def find_swing_highs(candles: list[Candle], lookback: int = 2) -> list[int]:
    """Fractal swing highs: a bar whose high exceeds every bar within
    `lookback` positions on both sides."""
    idxs = []
    for i in range(lookback, len(candles) - lookback):
        window = candles[i - lookback : i + lookback + 1]
        if all(candles[i].high > c.high for j, c in enumerate(window) if j != lookback):
            idxs.append(i)
    return idxs


def find_swing_lows(candles: list[Candle], lookback: int = 2) -> list[int]:
    idxs = []
    for i in range(lookback, len(candles) - lookback):
        window = candles[i - lookback : i + lookback + 1]
        if all(candles[i].low < c.low for j, c in enumerate(window) if j != lookback):
            idxs.append(i)
    return idxs


def ema_trend_bias(
    price: float, ema_fast: float | None, ema_slow: float | None, ema_trend: float | None
) -> Trend:
    """HTF directional bias from EMA stack alignment: price and the fast/slow
    EMAs all stacked above the long EMA (in order) means an established
    uptrend; the mirror image means downtrend; anything else is treated as
    range/no clear bias, which the strategy layer treats as a reason to
    wait rather than guess."""
    if ema_fast is None or ema_slow is None or ema_trend is None:
        return Trend.RANGE
    if price > ema_fast > ema_slow > ema_trend:
        return Trend.UP
    if price < ema_fast < ema_slow < ema_trend:
        return Trend.DOWN
    return Trend.RANGE


@dataclass
class ImpulseLeg:
    start_price: float
    end_price: float
    direction: Trend


def last_impulse_leg(
    candles: list[Candle], swing_high_idx: list[int], swing_low_idx: list[int], trend: Trend
) -> ImpulseLeg | None:
    """The move that produced the most recently confirmed swing extreme,
    which is what "how deep is the current pullback" has to be measured
    against: for an uptrend, that's the swing LOW that precedes the most
    recent swing HIGH (price is expected to be retracing *after* that high,
    towards this leg's Fibonacci zone, right now); for a downtrend, the
    mirror image."""
    if trend == Trend.UP:
        if not swing_low_idx or not swing_high_idx:
            return None
        last_high = max(swing_high_idx)
        preceding_low = max((i for i in swing_low_idx if i < last_high), default=None)
        if preceding_low is None:
            return None
        return ImpulseLeg(candles[preceding_low].low, candles[last_high].high, Trend.UP)

    if trend == Trend.DOWN:
        if not swing_low_idx or not swing_high_idx:
            return None
        last_low = max(swing_low_idx)
        preceding_high = max((i for i in swing_high_idx if i < last_low), default=None)
        if preceding_high is None:
            return None
        return ImpulseLeg(candles[preceding_high].high, candles[last_low].low, Trend.DOWN)

    return None


def fibonacci_zone(leg: ImpulseLeg, shallow: float = 0.382, deep: float = 0.618) -> tuple[float, float]:
    span = leg.end_price - leg.start_price
    level_shallow = leg.end_price - span * shallow
    level_deep = leg.end_price - span * deep
    return (min(level_shallow, level_deep), max(level_shallow, level_deep))


def price_in_pullback_zone(price: float, zone: tuple[float, float]) -> bool:
    low, high = zone
    return low <= price <= high


def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _range(c: Candle) -> float:
    return c.high - c.low


def _wicks(c: Candle) -> tuple[float, float]:
    """Returns (upper_wick, lower_wick)."""
    return c.high - max(c.open, c.close), min(c.open, c.close) - c.low


def is_bullish_engulfing(prev: Candle, curr: Candle) -> bool:
    return (
        prev.close < prev.open
        and curr.close > curr.open
        and curr.open <= prev.close
        and curr.close >= prev.open
    )


def is_bearish_engulfing(prev: Candle, curr: Candle) -> bool:
    return (
        prev.close > prev.open
        and curr.close < curr.open
        and curr.open >= prev.close
        and curr.close <= prev.open
    )


def is_bullish_pin_bar(c: Candle, min_wick_ratio: float = 2.0, max_body_ratio: float = 0.35) -> bool:
    r = _range(c)
    if r <= 0:
        return False
    body = _body(c)
    upper_wick, lower_wick = _wicks(c)
    return lower_wick >= min_wick_ratio * max(body, 1e-9) and lower_wick > upper_wick and body / r < max_body_ratio


def is_bearish_pin_bar(c: Candle, min_wick_ratio: float = 2.0, max_body_ratio: float = 0.35) -> bool:
    r = _range(c)
    if r <= 0:
        return False
    body = _body(c)
    upper_wick, lower_wick = _wicks(c)
    return upper_wick >= min_wick_ratio * max(body, 1e-9) and upper_wick > lower_wick and body / r < max_body_ratio
