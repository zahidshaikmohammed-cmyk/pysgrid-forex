"""Pure-Python technical indicators.

Deliberately dependency-free (no numpy/pandas): every formula here is
auditable line by line, and it keeps `pip install -r requirements.txt`
trivial on a plain Windows machine. These are the well-known, standard
formulas (Wilder's RSI/ATR smoothing, EMA-of-EMA-difference MACD, SMA-based
Bollinger Bands) -- nothing proprietary or "secret", because there is no
substitute for the real definitions here.

Every function returns a list the same length as its input, with `None`
in the positions where there isn't yet enough history to compute a value.
That alignment is what lets the strategy layer zip indicator series
directly against candles without off-by-one bugs.
"""
from __future__ import annotations

from pysgrid_forex.models import Candle


def ema(values: list[float], period: int) -> list[float | None]:
    if period <= 0 or len(values) < period:
        return [None] * len(values)

    k = 2.0 / (period + 1)
    result: list[float | None] = [None] * (period - 1)
    current = sum(values[:period]) / period
    result.append(current)
    for value in values[period:]:
        current = value * k + current * (1 - k)
        result.append(current)
    return result


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    if len(closes) < period + 1:
        return [None] * len(closes)

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    def _rsi_from(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    values = [_rsi_from(avg_gain, avg_loss)]

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        values.append(_rsi_from(avg_gain, avg_loss))

    return [None] * period + values


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]

    first_valid = next((i for i, v in enumerate(macd_line) if v is not None), None)
    if first_valid is None:
        signal_line: list[float | None] = [None] * len(closes)
    else:
        tail = [v for v in macd_line[first_valid:]]  # type: ignore[misc]
        tail_ema = ema(tail, signal)  # type: ignore[arg-type]
        signal_line = [None] * first_valid + tail_ema

    histogram: list[float | None] = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


def atr(candles: list[Candle], period: int = 14) -> list[float | None]:
    if len(candles) < period + 1:
        return [None] * len(candles)

    true_ranges = []
    for i in range(1, len(candles)):
        high, low, prev_close = candles[i].high, candles[i].low, candles[i - 1].close
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    values = [sum(true_ranges[:period]) / period]
    for i in range(period, len(true_ranges)):
        values.append((values[-1] * (period - 1) + true_ranges[i]) / period)

    return [None] * period + values


def bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(closes)
    mid: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n

    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance**0.5
        mid[i] = mean
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std

    return mid, upper, lower


def volume_zscore(volumes: list[float], lookback: int = 20) -> list[float | None]:
    """How unusual the current bar's volume is versus its own recent
    history. Note: most forex feeds (including RealMarketAPI) report tick
    volume -- the count of price updates -- not true traded volume, since
    forex is an OTC market with no central tape. Treat this as a proxy for
    "how much is happening right now", not literal traded size."""
    n = len(volumes)
    result: list[float | None] = [None] * n
    for i in range(lookback, n):
        window = volumes[i - lookback : i]
        mean = sum(window) / lookback
        variance = sum((x - mean) ** 2 for x in window) / lookback
        std = variance**0.5
        result[i] = 0.0 if std == 0 else (volumes[i] - mean) / std
    return result


def last_valid(series: list[float | None]) -> float | None:
    for value in reversed(series):
        if value is not None:
            return value
    return None
