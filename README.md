# Pysgrid Forex

Production-ready 1-minute OHLCV ingestion for Pysgrid using RealMarketAPI,
plus a signal engine that turns that feed into BUY/SELL/WAIT decision
support.

## Components

1. **`pysgrid_forex/`** -- the data-feed service. RealMarketAPI `/candles`
   WebSocket -> engine resync/contiguity gate -> store-level write-time M1
   enforcement -> durable local state -> FastAPI JSON endpoints ->
   Oracle VM/systemd -> GitHub Actions deployment (with an independent
   on-the-wire M1 cadence probe as a deploy gate).
2. **`signal_engine/`** -- consumes that feed over HTTP and generates
   BUY/SELL/WAIT calls with a full itemized rationale (trend, structure,
   momentum, pattern, session, news-risk). See
   [`docs/SIGNAL_ENGINE.md`](docs/SIGNAL_ENGINE.md) for its architecture,
   exact run command, and honest limitations -- read the limitations
   section before trusting a signal.

The provider API key is never committed. Set `REALMARKET_API_KEY` in the runtime environment.

## Default symbols

XAUUSD, EURUSD, GBPUSD, USDJPY, GBPJPY, AUDUSD, USDCAD, NZDUSD, XAGUSD, USOIL.

The list is configurable through `PYSGRID_SYMBOLS`.

## Data-integrity rules

- `candles_1m` contains completed candles only.
- **History**: from roughly mid-September 2026 through 2026-09-21, RealMarketAPI's `/candles` WebSocket and
  REST `/candle` endpoint both delivered candles spaced exactly 300 seconds apart despite `timeFrame=M1`
  (confirmed against live, open-market data). `m1_valid` correctly reported `false` throughout that period --
  no client-side code can produce genuine M1 bars a provider isn't sending. RealMarketAPI's support team
  confirmed on 2026-09-21 that this was a defect on their side and has been fixed ("XAUUSD M1 is now working
  correctly on both REST and WebSocket. No changes are needed on your side."). The Oracle's WebSocket
  subscription was never changed during that period and still requests `timeFrame=M1` today -- it now
  receives a genuine 1-minute cadence again. `tools/verify_m1_provider.py` remains available to independently
  re-verify this on the wire at any time, and the deploy pipeline still runs it automatically on every deploy,
  reporting the result loudly (a GitHub Actions annotation) without ever failing the deploy on it -- a
  provider-side regression must never block shipping a safety fix, which is why this stays informational
  rather than a hard gate.
- RealMarketAPI plans cap the number of concurrent WebSocket connections per API key. The live pysgrid-forex
  service holds one connection per configured symbol continuously, so `tools/verify_m1_provider.py`, run with
  the SAME key while the service is up, competes for that same limited pool and gets rejected with
  `ERR_0018_WEBSOCKET_CONCURRENT_LIMIT_EXCEEDED`. That is a connection-limit collision, not evidence about
  cadence, and the tool reports it as `BLOCKED` (exit code 2), distinct from a confirmed `FAIL` (exit code 1).
  Get a conclusive probe result either with a second API key, or by stopping the service first.
- REST `/candle` data is accepted only when a returned multi-bar series has exact 60-second spacing.
- Observed non-M1 REST data is rejected instead of being relabeled as M1.
- `RealMarketAPI.recover()` (REST catch-up) is implemented but not wired into the running `Engine` -- it was
  left disabled during the mislabeling period above and has not been re-enabled now that RealMarketAPI has
  confirmed the fix; re-enabling it is out of scope for the M1->M5 aggregation fix below.
- Every candle that reaches `candles_1m` is verified, at write time, to be either the first candle in an
  empty series or exactly 60 seconds after the immediately preceding stored candle
  (`CandleStore.append_candle`). A candle that doesn't satisfy this is never silently stored: it is held
  in memory as an unconfirmed resync anchor until a *following* candle proves it sits on a genuine 60-second
  cadence, or it is discarded (`rejected_count`). This holds regardless of gap size (2 minutes, 5 minutes, or
  longer) and is enforced independently at both the engine (ingestion) and store (persistence) layers.
- The system never fabricates M1 bars to fill a gap. A legitimate gap (provider outage, weekend closure) is
  recorded as a gap (`gap_recoveries`) once a new run resumes and is confirmed genuine -- the missing minutes
  are never synthesized.
- `is_valid_m1`/`m1_valid` requires the tail of a symbol's series to be a fresh, uninterrupted 60-second-spaced
  run, and defensively re-scans the whole series for any impossible (non-positive) spacing. It is exposed
  per-symbol on `/health` (`m1_status`, `all_m1_live`), `/metrics`, `/public/{symbol}.json`, and now also on
  the bulk `/public/live.json` and `/public/forex.json` feeds.
- A single bad/out-of-sequence candle never invalidates existing history. Only a genuinely unreadable stored
  file (e.g. from a pre-v4 schema version, before write-time contiguity enforcement existed) is discarded, and
  only once, on first load.

## M1 -> M5 aggregation pipeline

With RealMarketAPI's M1 feed confirmed genuine again, the M5 feed is built by aggregating real M1 candles --
never by treating raw provider data as already-M5 (that was a temporary, honestly-labeled workaround during
the mislabeling period above, and has been replaced now that the underlying defect is fixed):

- The M1 pipeline is unchanged: `candles_1m`, `m1_valid`, and every M1 endpoint behave exactly as before. The
  Oracle's WebSocket subscription still requests `timeFrame=M1` -- it is never changed to M5.
- No second WebSocket connection is opened for M5. `Engine._store_m1()` is the single choke point through
  which a candle actually becomes part of the trusted M1 series (`candles_1m`); every candle that passes
  through it is also forwarded, in the same order, to `M5Engine.on_m1_candle()` -- the M1->M5 aggregator.
  M5Engine never sees the raw, unvalidated provider stream.
- `M5Engine` buckets five contiguous, 5-minute-aligned M1 candles into one completed M5 OHLCV bar:
  `open`=first M1 open, `high`=max of the five M1 highs, `low`=min of the five M1 lows, `close`=last M1
  close, `volume`=sum of the five M1 volumes.
- A bucket only ever becomes an M5 candle once all five of its M1 candles have arrived, contiguously, in
  order. A missing, duplicate, out-of-order, or delayed M1 candle discards the in-progress bucket outright --
  nothing is ever fabricated, forward-filled, interpolated, or duplicated to complete it. A same-timestamp
  redelivery of the most recently accepted M1 slot updates it in place (mirroring `CandleStore`'s own
  idempotent-correction handling) rather than being treated as a break.
- `CandleStore`'s write-time contiguity check still enforces the M5 store's own 300-second-spacing invariant
  at the persistence layer (`period_seconds=300`, `candles_5m` key) -- the aggregator and the store are two
  independent layers of the same guarantee, exactly as M1 already does.
- `m5_max_candles` (default 300) retains roughly 24 hours of history (288 five-minute candles/day) with a
  margin.
- `m5_valid`/`M5Engine.is_valid` requires a fresh (`m5_stale_seconds`, default 600s), uninterrupted
  300-second-spaced tail, exactly mirroring `is_valid_m1`'s rigor -- unchanged by this rewrite.

## Local test

Python 3.11+:

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux: source .venv/bin/activate
pip install -r requirements.txt
pytest -q
python -m pysgrid_forex.main --dry-run
```

Dry-run does not require an API key.

## Runtime

```bash
export REALMARKET_API_KEY='...'
uvicorn pysgrid_forex.api:app --host 0.0.0.0 --port 8080
```

## Running the signal engine

From a fresh PowerShell terminal, against your deployed feed:

```powershell
$env:PYSGRID_API_BASE = "https://your-oracle-host-or-domain"
python -m signal_engine.main
```

See [`docs/SIGNAL_ENGINE.md`](docs/SIGNAL_ENGINE.md) for flags, configuration,
and -- importantly -- what it can't actually do yet.

## Endpoints

- `GET /health`
- `GET /public/live.json`
- `GET /public/forex.json`
- `GET /public/{symbol}.json`
- `GET /public/m1-live.json`
- `GET /metrics`

The JSON contains only completed M1 candles. Provider timestamps are normalized to UTC ISO-8601 strings.

`/public/m1-live.json` is the same trusted `engine.store` data as `/public/live.json`, under a name that
mirrors `/public/m5-live.json` for symmetry, plus two explicit provenance fields: `candle_source:
"provider_native"` and `synthetic_candles: false` -- so a consumer can tell from the schema alone that these
are exactly the candles RealMarketAPI sent, never derived from M5 or fabricated.

### Native M5 endpoints

- `GET /public/m5-live.json`
- `GET /public/m5-forex.json`
- `GET /public/m5/{symbol}.json`

Same shape as their M1 counterparts, but `timeframe: "M5"`, `m5_valid`, and a `candles_5m` array of
completed, genuinely 300-second-spaced candles. `/health` and `/metrics` also report M5 status per symbol
(`m5_status`, `m5_live_symbols`, `all_m5_live`, `symbols_m5`).

## Security

Do not put API keys or SSH private keys in Git. GitHub Actions receives the Oracle SSH key through a repository secret. The market-data key belongs on the Oracle host as `REALMARKET_API_KEY`.
