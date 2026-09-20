# Signal Engine

A decision-support engine that reads the validated M1 feed pysgrid-forex
produces and turns it into BUY/SELL/WAIT calls, with an explicit, auditable
rationale for every call. It does not place trades and does not touch a
broker -- it is analysis, not execution.

**This is not "the best engine ever built" and it is not smarter than a
human.** It is a rules-based, transparent system: multi-timeframe EMA trend
alignment, fractal swing structure, Fibonacci pullback zones, RSI/MACD
momentum confirmation, candlestick confirmation, session-liquidity
awareness, and an economic-calendar news-risk filter, combined into one
scored decision. Every signal lists exactly which of those checks passed.
Treat it as a second opinion that never gets tired or emotional, not as an
oracle -- back-test and paper-trade before risking real capital.

## Architecture

```
pysgrid-forex /public/{symbol}.json  (validated M1, m1_valid flag)
        |
  signal_engine/feed_client.py   -- HTTP client; refuses unvalidated data
        |
  signal_engine/resample.py      -- gap-aware M1 -> M5/M15/H1/H4 aggregation
        |
  signal_engine/indicators.py    -- EMA, RSI, MACD, ATR, Bollinger, vol z-score
  signal_engine/structure.py     -- swing points, trend bias, Fibonacci zone,
                                     candlestick confirmation patterns
  signal_engine/sessions.py      -- FX session clock, per-symbol liquidity weight
  signal_engine/calendar_feed.py -- economic calendar, news-risk blackout window
        |
  signal_engine/strategy.py      -- fuses all of the above into one Signal
        |
  signal_engine/reporter.py      -- terminal table + daily JSON log
  signal_engine/main.py          -- CLI entry point
```

## Running it

From the `pysgrid-forex` repo root, in PowerShell:

```powershell
$env:PYSGRID_API_BASE = "https://your-oracle-host-or-domain"   # your deployed feed
python -m signal_engine.main
```

For a local dev feed instead of the deployed one, leave `PYSGRID_API_BASE`
unset (it defaults to `http://127.0.0.1:8080`) and run pysgrid-forex's own
`uvicorn pysgrid_forex.api:app` alongside it.

Useful flags:

- `--once` -- one pass, then exit (good for a scheduled task / cron-style run)
- `--detail` -- print the full itemized rationale, not just a summary table
- `--no-color` -- disable ANSI colors (some PowerShell hosts render them oddly)
- `--symbols XAUUSD,EURUSD` -- override the symbol list for this run

Every generated signal (BUY, SELL, or WAIT, with its full rationale) is also
appended to `signals/YYYY-MM-DD.jsonl` so a day's calls can be reviewed or
back-tested against afterwards.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `PYSGRID_API_BASE` | `http://127.0.0.1:8080` | Base URL of the pysgrid-forex feed |
| `SIGNAL_SYMBOLS` | same 10 as pysgrid-forex | Comma-separated symbols to evaluate |
| `SIGNAL_POLL_SECONDS` | `60` | Seconds between passes in continuous mode |
| `SIGNAL_CALENDAR_URL` | ForexFactory mirror | Economic calendar JSON feed |
| `SIGNAL_CALENDAR_ENABLED` | `true` | Set `false` to disable the news-risk filter entirely |
| `SIGNAL_MIN_CONFIDENCE` | `60` | Minimum score (0-100) required to output BUY/SELL instead of WAIT |
| `SIGNAL_LOG_DIR` | `./signals` | Where the daily JSONL signal log is written |
| `SIGNAL_LOG_LEVEL` | `INFO` | Python logging level |

## Honest limitations -- read this before trusting a signal

**1. Multi-timeframe "trend" is capped by pysgrid-forex's own retention window.**
pysgrid-forex's `CandleStore` keeps only the most recent `PYSGRID_MAX_CANDLES`
M1 bars (1500 by default, ~25 hours). A textbook "H4 EMA200" trend filter
needs roughly 33 days of H4 bars, which simply does not exist in a 25-hour
window. `select_timeframes()` in `strategy.py` picks the *largest* timeframe
that actually has enough resampled bars for a meaningful EMA-trend period,
and every signal's rationale states which timeframe it used and how many
hours of history that represents (e.g. "trend timeframe=5m (healthy history,
~8.3h span)"). With the default 25-hour window, expect an intraday (M1-M15
class) trend filter, not a multi-day swing-trade one. If you want genuine
H1/H4 trend confirmation, raise `PYSGRID_MAX_CANDLES` on the feed service
(e.g. to 43200 for ~30 days of M1 history) -- the engine will automatically
use a coarser timeframe once enough history exists, no code change needed.

**2. The economic calendar is a free, unofficial, keyless mirror.**
`SIGNAL_CALENDAR_URL` defaults to a community redistribution of the
ForexFactory calendar. It is not a contractually guaranteed feed, its schema
has drifted before, and its reachability from wherever you run this was
**not verified** from the sandboxed environment this engine was built in
(no outbound access to arbitrary third-party hosts there). If it's
unreachable or empty, the engine does not pretend the coast is clear -- it
attaches a `"news calendar unavailable"` note to every signal so you know
the event-risk filter isn't actually active, rather than silently trading
through an NFP or FOMC release. Verify it works from your machine; swap the
URL if it doesn't.

**3. "Volume" is tick volume, not traded size.**
Forex is an OTC market with no central tape. RealMarketAPI (like virtually
every retail forex feed) reports the count of price updates per candle, not
literal traded volume. `volume_zscore` is a genuine signal of "how much
price-update activity is happening right now relative to its own recent
history," which correlates with real activity, but it is not a substitute
for a real order-book/traded-volume feed.

**4. This has not been back-tested or paper-traded.**
Every rule here (EMA stack alignment, Fibonacci 38.2-61.8% retracement,
RSI/MACD confirmation, engulfing/pin-bar patterns) is standard, widely
documented technical analysis, chosen because it's auditable, not because
it's been proven to have a statistical edge in this specific configuration
on this specific instrument set. Nothing here is investment advice. Back-test
against your own historical data and paper-trade before risking real money.

**5. Gold/oil-specific event risk beyond the general calendar isn't covered.**
XAUUSD/XAGUSD/USOIL are mapped to USD for calendar-gating purposes (their
biggest scheduled-event driver), but OPEC decisions, EIA inventory reports,
and general risk-off/geopolitical moves are not on a generic FX economic
calendar and are not detected by this engine at all.
