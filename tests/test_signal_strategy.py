from dataclasses import replace
from datetime import datetime, timedelta, timezone

from pysgrid_forex.models import Candle, parse_timestamp
from signal_engine.config import EngineConfig
from signal_engine.feed_client import SymbolSnapshot
from signal_engine.models import Action
from signal_engine.strategy import generate_signal, select_timeframes

# Config with small periods so a compact, hand-auditable candle series can
# exercise the full pipeline (trend -> structure -> momentum -> pattern ->
# confidence) without needing months of history. The pipeline logic under
# test doesn't depend on the specific period sizes.
TEST_CONFIG = EngineConfig(
    ema_fast=5, ema_slow=10, ema_trend=20,
    rsi_period=5, macd_fast=3, macd_slow=6, macd_signal=2,
    atr_period=5, bollinger_period=5, swing_lookback=1,
    timeframes_minutes=(1, 5), volume_lookback=5,
    min_confidence_to_trade=60, calendar_enabled=False,
)


def _make_candle(t: datetime, o: float, c: float, v: float = 10) -> Candle:
    # Asymmetric wick: each bar's wick pokes further in the direction it's
    # already moving. This makes fractal swing detection find a clean,
    # unambiguous local extreme at every turning point (a symmetric wick
    # ties the peak/trough bar's high or low with its immediate neighbour,
    # since they share the same transition price).
    if c >= o:
        high, low = c + 0.15, o - 0.02
    else:
        high, low = o + 0.02, c - 0.15
    return Candle(timestamp=t.isoformat().replace("+00:00", "Z"), open=o, high=high, low=low, close=c, volume=v)


def _build_pullback_series(direction: str) -> list[Candle]:
    """A staircase trend (20 cycles of an 8-bar push + 3-bar shallow
    correction, so fractal swing detection has real structure to find),
    then one clean final impulse leg, a decelerating pullback into its
    38.2-61.8% Fibonacci zone, and a strong engulfing reversal candle back
    in the trend direction -- a textbook trend-pullback-continuation entry.
    direction="down" mirrors every price move to build the equivalent
    downtrend/rally-into-a-sell-zone setup."""
    sign = 1.0 if direction == "up" else -1.0
    start = datetime(2026, 1, 5, 13, 0, tzinfo=timezone.utc)  # Monday, London/NY hours
    candles: list[Candle] = []
    price = 100.0
    t = start

    for _ in range(20):
        for _ in range(8):
            o, price, t = price, price + 1.0 * sign, t
            c = price
            candles.append(_make_candle(t, o, c))
            t += timedelta(minutes=1)
        for _ in range(3):
            o = price
            price -= 0.3 * sign
            c = price
            candles.append(_make_candle(t, o, c))
            t += timedelta(minutes=1)

    for _ in range(10):  # the final impulse leg
        o = price
        price += 2.0 * sign
        c = price
        candles.append(_make_candle(t, o, c))
        t += timedelta(minutes=1)

    for step in (-2.5, -2.0, -1.5, -1.0, -0.6, -0.3):  # decelerating pullback
        o = price
        price += step * sign
        c = price
        candles.append(_make_candle(t, o, c))
        t += timedelta(minutes=1)

    o = price  # strong reversal/confirmation candle
    price += 3.0 * sign
    candles.append(_make_candle(t, o, price))

    return candles


def _snapshot(symbol: str, candles: list[Candle], *, m1_valid: bool = True, status: str = "ok") -> SymbolSnapshot:
    return SymbolSnapshot(symbol=symbol, m1_valid=m1_valid, status=status, candles=candles)


def test_buy_signal_on_confirmed_pullback_in_uptrend():
    candles = _build_pullback_series("up")
    now = parse_timestamp(candles[-1].timestamp)
    signal = generate_signal("EURUSD", _snapshot("EURUSD", candles), TEST_CONFIG, None, now=now)

    assert signal.action == Action.BUY
    assert signal.confidence >= TEST_CONFIG.min_confidence_to_trade
    assert signal.suggested_stop_loss < signal.price < signal.suggested_take_profit
    assert any("Fibonacci pullback zone" in r for r in signal.reasons)
    assert any("engulfing" in r for r in signal.reasons)
    assert any("Bollinger" in r for r in signal.reasons)


def test_sell_signal_on_confirmed_rally_in_downtrend():
    candles = _build_pullback_series("down")
    now = parse_timestamp(candles[-1].timestamp)
    signal = generate_signal("EURUSD", _snapshot("EURUSD", candles), TEST_CONFIG, None, now=now)

    assert signal.action == Action.SELL
    assert signal.confidence >= TEST_CONFIG.min_confidence_to_trade
    assert signal.suggested_take_profit < signal.price < signal.suggested_stop_loss


def test_wait_when_m1_not_validated():
    candles = _build_pullback_series("up")
    now = parse_timestamp(candles[-1].timestamp)
    snapshot = _snapshot("EURUSD", candles, m1_valid=False, status="stale")
    signal = generate_signal("EURUSD", snapshot, TEST_CONFIG, None, now=now)

    assert signal.action == Action.WAIT
    assert "m1_valid=false" in signal.reasons[0]


def test_wait_when_no_snapshot_available():
    signal = generate_signal("EURUSD", None, TEST_CONFIG, None)
    assert signal.action == Action.WAIT
    assert "no response" in signal.reasons[0]


def test_wait_when_insufficient_history():
    candles = _build_pullback_series("up")[:10]
    now = parse_timestamp(candles[-1].timestamp)
    signal = generate_signal("EURUSD", _snapshot("EURUSD", candles), TEST_CONFIG, None, now=now)

    assert signal.action == Action.WAIT
    assert "validated M1 candles available" in signal.reasons[0]


def test_wait_when_market_is_weekend_closed():
    candles = _build_pullback_series("up")
    saturday = datetime(2026, 1, 3, 12, tzinfo=timezone.utc)
    signal = generate_signal("EURUSD", _snapshot("EURUSD", candles), TEST_CONFIG, None, now=saturday)

    assert signal.action == Action.WAIT
    assert "session closed" in signal.reasons[0]


class _FakeCalendarNear:
    last_error = None

    def events_near(self, *args, **kwargs):
        class _Event:
            currency = "USD"
            title = "Non-Farm Payrolls"
            time_utc = datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc)

        return [_Event()]


def test_wait_when_high_impact_news_is_imminent():
    candles = _build_pullback_series("up")
    now = parse_timestamp(candles[-1].timestamp)
    config = replace(TEST_CONFIG, calendar_enabled=True)
    signal = generate_signal("EURUSD", _snapshot("EURUSD", candles), config, _FakeCalendarNear(), now=now)

    assert signal.action == Action.WAIT
    assert "news risk" in signal.reasons[0]


def test_wait_when_confidence_is_below_configured_threshold():
    candles = _build_pullback_series("up")
    now = parse_timestamp(candles[-1].timestamp)
    strict_config = replace(TEST_CONFIG, min_confidence_to_trade=99.9)
    signal = generate_signal("EURUSD", _snapshot("EURUSD", candles), strict_config, None, now=now)

    assert signal.action == Action.WAIT
    assert "below the" in signal.reasons[0]
    assert len(signal.reasons) > 1  # the full technical rationale is still attached


def test_select_timeframes_falls_back_to_finest_tf_when_history_is_thin():
    config = EngineConfig(timeframes_minutes=(1, 5, 15, 60, 240), ema_trend=200)
    trend_tf, trigger_tf, quality = select_timeframes(m1_bar_count=150, config=config)
    assert trend_tf == 1
    assert quality == "thin"


def test_select_timeframes_upgrades_to_a_coarser_tf_with_more_history():
    config = EngineConfig(timeframes_minutes=(1, 5, 15, 60, 240), ema_trend=200)
    trend_tf, trigger_tf, quality = select_timeframes(m1_bar_count=100_000, config=config)
    assert trend_tf == 240
    assert trigger_tf == 60
    assert quality == "healthy"
