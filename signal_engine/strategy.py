"""Signal fusion: turns a validated M1 candle series into a BUY/SELL/WAIT
call with an explicit, itemized rationale.

Design principle: every WAIT and every BUY/SELL states exactly which checks
passed and which didn't. There is no hidden scoring nobody can audit -- if
you disagree with a signal, you can read `reasons` and see precisely why it
fired, the same way you'd want a human analyst to show their work.

Honest limitation baked into this module (read this before trusting the
"trend" timeframe): pysgrid-forex's own CandleStore retains only the most
recent `max_candles` M1 bars (1500 by default, i.e. ~25 hours). That hard
ceiling limits how much higher-timeframe history can ever be derived from
it -- there is no way to compute a textbook "H4 EMA200" (which needs ~33
days of H4 bars) from a 25-hour window, and pretending otherwise would be
lying with a chart. `select_timeframes` below picks the LARGEST timeframe
that actually has enough resampled bars for a meaningful EMA-trend period,
and reports the data-quality tier it landed on. Raise pysgrid-forex's
PYSGRID_MAX_CANDLES if you want genuine multi-day HTF trend confirmation
(this engine picks up the extra history automatically, no config change
needed here); until then, this operates as an intraday (M1-M15 class)
system, honestly labeled as such.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pysgrid_forex.models import Candle

from . import indicators as ind
from . import structure as struct
from .calendar_feed import CalendarFeed, currency_codes_for_symbol
from .config import EngineConfig
from .feed_client import SymbolSnapshot
from .models import Action, Signal
from .resample import resample
from .sessions import session_label, session_weight


def select_timeframes(m1_bar_count: int, config: EngineConfig) -> tuple[int, int, str]:
    """Returns (trend_timeframe_minutes, trigger_timeframe_minutes, data_quality)."""
    candidates = sorted(config.timeframes_minutes, reverse=True)
    trend_tf = None
    trend_bars = 0
    for tf in candidates:
        bars = m1_bar_count // tf
        if bars >= config.ema_trend:
            trend_tf, trend_bars = tf, bars
            break

    if trend_tf is None:
        trend_tf = min(config.timeframes_minutes)
        trend_bars = m1_bar_count // trend_tf
        quality = "thin"
    elif trend_bars >= config.ema_trend * 1.5:
        quality = "healthy"
    else:
        quality = "marginal"

    finer = sorted(tf for tf in config.timeframes_minutes if tf < trend_tf)
    trigger_tf = finer[-1] if finer else trend_tf
    return trend_tf, trigger_tf, quality


def _wait(symbol: str, price: float | None, reason: str, extra: list[str] | None = None) -> Signal:
    reasons = [reason] + (extra or [])
    return Signal(symbol=symbol, action=Action.WAIT, confidence=0.0, price=price, reasons=reasons)


def generate_signal(
    symbol: str,
    snapshot: SymbolSnapshot | None,
    config: EngineConfig,
    calendar: CalendarFeed | None = None,
    *,
    now: datetime | None = None,
) -> Signal:
    now = now or datetime.now(timezone.utc)

    if snapshot is None:
        return _wait(symbol, None, "no response from pysgrid-forex feed for this symbol")

    if not snapshot.m1_valid:
        return _wait(
            symbol, None,
            f"pysgrid-forex reports m1_valid=false (status={snapshot.status}); "
            "refusing to trade on an unvalidated M1 series",
        )

    candles = snapshot.candles
    min_needed = max(config.ema_slow, config.rsi_period, config.bollinger_period) + 5
    if len(candles) < min_needed:
        return _wait(symbol, candles[-1].close if candles else None,
                     f"only {len(candles)} validated M1 candles available, need >= {min_needed}")

    weight = session_weight(now, symbol)
    label = session_label(now)
    if weight == 0.0:
        return _wait(symbol, candles[-1].close, f"market session closed ({label})")

    currencies = currency_codes_for_symbol(symbol)
    calendar_note: str | None = None
    if config.calendar_enabled and calendar is not None:
        near = calendar.events_near(
            currencies, now,
            before_minutes=config.high_impact_blackout_minutes_before,
            after_minutes=config.high_impact_blackout_minutes_after,
            min_impact="high",
        )
        if near:
            titles = ", ".join(f"{e.currency} {e.title} @ {e.time_utc.strftime('%H:%M UTC')}" for e in near)
            return _wait(symbol, candles[-1].close, f"high-impact news risk window: {titles}")
        if calendar.last_error:
            calendar_note = f"news calendar unavailable ({calendar.last_error}); trading without event-risk filter"
    else:
        calendar_note = "news calendar disabled; trading without event-risk filter"

    trend_tf, trigger_tf, quality = select_timeframes(len(candles), config)
    trend_candles = resample(candles, trend_tf) if trend_tf > 1 else list(candles)
    trigger_candles = resample(candles, trigger_tf) if trigger_tf > 1 else list(candles)

    if len(trend_candles) < config.ema_slow or len(trigger_candles) < config.swing_lookback * 2 + 5:
        return _wait(symbol, candles[-1].close, "insufficient resampled history to establish trend/structure")

    closes_trend = [c.close for c in trend_candles]
    ema_fast_s = ind.ema(closes_trend, config.ema_fast)
    ema_slow_s = ind.ema(closes_trend, config.ema_slow)
    ema_trend_s = ind.ema(closes_trend, config.ema_trend) if len(closes_trend) >= config.ema_trend else [None] * len(closes_trend)

    last_price = trend_candles[-1].close
    trend = struct.ema_trend_bias(last_price, ind.last_valid(ema_fast_s), ind.last_valid(ema_slow_s), ind.last_valid(ema_trend_s))

    horizon_hours = trend_tf * len(trend_candles) / 60
    tf_note = f"trend timeframe={trend_tf}m ({quality} history, ~{horizon_hours:.1f}h span)"

    if trend == struct.Trend.RANGE:
        return _wait(symbol, last_price, f"no clear HTF trend ({tf_note})")

    highs = struct.find_swing_highs(trigger_candles, config.swing_lookback)
    lows = struct.find_swing_lows(trigger_candles, config.swing_lookback)
    leg = struct.last_impulse_leg(trigger_candles, highs, lows, trend)
    if leg is None:
        return _wait(symbol, last_price, f"no measurable {trend.value} impulse leg on trigger timeframe={trigger_tf}m")

    zone = struct.fibonacci_zone(leg)
    last_candle = trigger_candles[-1]
    entry_price = last_candle.close
    # A genuine reversal candle closes back AWAY from the zone by
    # definition -- that's what makes it a reversal. So the zone touch has
    # to be checked against the wick that actually traded into it (the low
    # for a bullish setup, the high for a bearish one), not the close.
    touch_price = last_candle.low if trend == struct.Trend.UP else last_candle.high
    if not struct.price_in_pullback_zone(touch_price, zone):
        return _wait(
            symbol, entry_price,
            f"{trend.value} trend confirmed but price not yet in pullback zone "
            f"({zone[0]:.5f}-{zone[1]:.5f})",
        )

    closes_trigger = [c.close for c in trigger_candles]
    rsi_s = ind.rsi(closes_trigger, config.rsi_period)
    _, _, hist_s = ind.macd(closes_trigger, config.macd_fast, config.macd_slow, config.macd_signal)
    last_rsi = ind.last_valid(rsi_s)
    valid_hist = [h for h in hist_s if h is not None]

    if last_rsi is None or len(valid_hist) < 2:
        return _wait(symbol, entry_price, "insufficient momentum indicator history yet")

    momentum_ok = False
    if trend == struct.Trend.UP:
        momentum_ok = 35 <= last_rsi <= 65 and valid_hist[-1] > valid_hist[-2]
    else:
        momentum_ok = 35 <= last_rsi <= 65 and valid_hist[-1] < valid_hist[-2]

    if not momentum_ok:
        return _wait(
            symbol, entry_price,
            f"in pullback zone but momentum not confirming a {trend.value} resumption yet "
            f"(RSI={last_rsi:.1f})",
        )

    prev_candle, last_candle = trigger_candles[-2], trigger_candles[-1]
    pattern_confirmed = False
    pattern_name = ""
    if trend == struct.Trend.UP:
        if struct.is_bullish_engulfing(prev_candle, last_candle):
            pattern_confirmed, pattern_name = True, "bullish engulfing"
        elif struct.is_bullish_pin_bar(last_candle):
            pattern_confirmed, pattern_name = True, "bullish pin bar"
    else:
        if struct.is_bearish_engulfing(prev_candle, last_candle):
            pattern_confirmed, pattern_name = True, "bearish engulfing"
        elif struct.is_bearish_pin_bar(last_candle):
            pattern_confirmed, pattern_name = True, "bearish pin bar"

    if not pattern_confirmed:
        return _wait(symbol, entry_price, f"in pullback zone with momentum turning, waiting for a confirming candle ({trend.value})")

    volumes = [c.volume for c in trigger_candles]
    vol_z = ind.last_valid(ind.volume_zscore(volumes, config.volume_lookback))
    atr_s = ind.atr(trigger_candles, config.atr_period)
    last_atr = ind.last_valid(atr_s)

    bb_mid_s, bb_upper_s, bb_lower_s = ind.bollinger_bands(closes_trigger, config.bollinger_period, config.bollinger_stddev)
    bb_mid, bb_upper, bb_lower = ind.last_valid(bb_mid_s), ind.last_valid(bb_upper_s), ind.last_valid(bb_lower_s)
    bb_width_pct = None
    if bb_mid and bb_upper is not None and bb_lower is not None:
        bb_width_pct = (bb_upper - bb_lower) / bb_mid * 100.0

    reasons = [
        f"{trend.value.upper()} trend on {tf_note}",
        f"price in Fibonacci pullback zone of last impulse leg ({zone[0]:.5f}-{zone[1]:.5f})",
        f"RSI({config.rsi_period})={last_rsi:.1f} with MACD histogram turning {'up' if trend == struct.Trend.UP else 'down'}",
        f"{pattern_name} confirmation on trigger timeframe={trigger_tf}m",
        f"session={label} (liquidity weight {weight:.2f})",
    ]
    if vol_z is not None:
        reasons.append(f"volume z-score={vol_z:.2f} vs its own {config.volume_lookback}-bar history (tick volume, not true traded size)")
    if bb_width_pct is not None:
        reasons.append(f"Bollinger({config.bollinger_period},{config.bollinger_stddev}) band width={bb_width_pct:.2f}% of price")
    if calendar_note:
        reasons.append(calendar_note)

    confidence = 50.0
    confidence += 15.0  # trend alignment gate already passed
    confidence += 15.0  # momentum confirmation gate already passed
    confidence += 10.0  # candlestick confirmation gate already passed
    confidence += 15.0 * weight
    if vol_z is not None:
        confidence += max(-5.0, min(5.0, vol_z * 5.0))
    if bb_width_pct is not None:
        # Rough, uncalibrated-per-instrument heuristic: a very tight band
        # means the market is quiet/consolidating right now (a weaker
        # backdrop for a continuation trade); a wide one means real
        # participation is already moving price.
        if bb_width_pct < 0.15:
            confidence -= 5.0
        elif bb_width_pct > 1.0:
            confidence += 3.0
    if quality == "marginal":
        confidence -= 5.0
    elif quality == "thin":
        confidence -= 10.0
    if calendar_note:
        confidence -= 5.0
    confidence = max(0.0, min(100.0, confidence))

    action = Action.BUY if trend == struct.Trend.UP else Action.SELL

    suggested_sl = suggested_tp = None
    if last_atr is not None and last_atr > 0:
        if action == Action.BUY:
            suggested_sl = entry_price - 1.5 * last_atr
            suggested_tp = entry_price + 3.0 * last_atr
        else:
            suggested_sl = entry_price + 1.5 * last_atr
            suggested_tp = entry_price - 3.0 * last_atr
        reasons.append(f"ATR({config.atr_period})={last_atr:.5f} -> stop 1.5x / target 3x (1:2 R:R)")

    if confidence < config.min_confidence_to_trade:
        return _wait(
            symbol, entry_price,
            f"all technical checks passed but confidence {confidence:.0f} is below the "
            f"{config.min_confidence_to_trade:.0f} threshold to act on",
            extra=reasons,
        )

    return Signal(
        symbol=symbol,
        action=action,
        confidence=round(confidence, 1),
        price=entry_price,
        reasons=reasons,
        suggested_stop_loss=suggested_sl,
        suggested_take_profit=suggested_tp,
    )
