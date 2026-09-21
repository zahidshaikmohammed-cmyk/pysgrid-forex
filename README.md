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
- The provider's `/candles` WebSocket (`timeFrame=M1`) is the live OHLCV source. **Confirmed against a real,
  open-market feed on 2026-09-21: this endpoint delivers exactly 300-second-spaced bars, not 60-second
  ones, despite `timeFrame=M1`** -- the same mislabeling already known on the REST `/candle` endpoint (see
  below) is present on the WebSocket too. `m1_valid` correctly reports `false` for this; no client-side code
  can produce genuine M1 bars a provider never sends. This needs resolving with RealMarketAPI directly
  (confirm plan/entitlement, or find a true M1 or raw-tick endpoint) before this feed can be trusted for
  anything that assumes 1-minute resolution. `tools/verify_m1_provider.py` is available to re-check this
  once that's addressed. The deploy pipeline runs it automatically on every deploy and reports the result
  loudly (a GitHub Actions warning annotation), but does NOT fail the deploy on it -- a provider-side
  problem must never block shipping a safety fix that makes this exact failure mode visible instead of
  silently accepted, which is what would happen if this were a hard gate.
- **Separately discovered while deploying this fix**: RealMarketAPI plans cap the number of concurrent
  WebSocket connections per API key. The live pysgrid-forex service holds one connection per configured
  symbol continuously, so `tools/verify_m1_provider.py`, run with the SAME key while the service is up,
  competes for that same limited pool and gets rejected with `ERR_0018_WEBSOCKET_CONCURRENT_LIMIT_EXCEEDED`.
  That is a connection-limit collision, not evidence about cadence, and the tool reports it as `BLOCKED`
  (exit code 2), distinct from a confirmed `FAIL` (exit code 1) -- the two must never be read as the same
  thing. Get a conclusive probe result either with a second API key, or by stopping the service first.
- REST `/candle` data is accepted only when a returned multi-bar series has exact 60-second spacing.
- Observed non-M1 REST data is rejected instead of being relabeled as M1.
- REST recovery is disabled because the observed `/candle?timeFrame=M1` response was 5-minute spaced -- a
  concrete, previously-observed instance of this provider mislabeling non-M1 data as M1, which is why
  `timeFrame=M1` on any endpoint (REST or WebSocket) is never treated as sufficient proof of resolution.
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

## Native M5 pipeline

Since RealMarketAPI's WebSockets deliver a genuine, confirmed 300-second cadence under `timeFrame=M1` (see
above), that data is honestly exposed as what it actually is -- M5 -- rather than only rejected as invalid
M1:

- The M1 pipeline is completely unchanged: `candles_1m`, `m1_valid`, and every M1 endpoint behave exactly as
  before. The M5 pipeline is purely additive, running alongside it.
- No second WebSocket connection is opened. `Engine._dispatch()` feeds the exact same raw candle stream, from
  the same one-connection-per-symbol pool already used by M1, to both the M1 acceptance path and the M5 one
  (`M5Engine`). This matters because RealMarketAPI plans cap concurrent connections per key, and that pool is
  already fully used by the M1 pipeline.
- `M5Engine` applies the identical discipline as the M1 engine, at a 300-second period instead of 60: a
  candle is only ever persisted once a *following* candle confirms it sits on an exact 300-second cadence;
  gaps are held as unconfirmed anchors, never fabricated; `CandleStore`'s write-time contiguity check enforces
  this at the persistence layer too (`period_seconds=300`, `candles_5m` key).
- `m5_max_candles` (default 300) retains roughly 24 hours of history (288 five-minute candles/day) with a
  margin.
- `m5_valid`/`M5Engine.is_valid` requires a fresh (`m5_stale_seconds`, default 600s), uninterrupted
  300-second-spaced tail, exactly mirroring `is_valid_m1`'s rigor.

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
- `GET /metrics`

The JSON contains only completed M1 candles. Provider timestamps are normalized to UTC ISO-8601 strings.

### Native M5 endpoints

- `GET /public/m5-live.json`
- `GET /public/m5-forex.json`
- `GET /public/m5/{symbol}.json`

Same shape as their M1 counterparts, but `timeframe: "M5"`, `m5_valid`, and a `candles_5m` array of
completed, genuinely 300-second-spaced candles. `/health` and `/metrics` also report M5 status per symbol
(`m5_status`, `m5_live_symbols`, `all_m5_live`, `symbols_m5`).

## Security

Do not put API keys or SSH private keys in Git. GitHub Actions receives the Oracle SSH key through a repository secret. The market-data key belongs on the Oracle host as `REALMARKET_API_KEY`.
