# Pysgrid Forex

Production-ready 1-minute OHLCV ingestion for Pysgrid using RealMarketAPI.

## Architecture

RealMarketAPI `/candles` WebSocket -> engine resync/contiguity gate -> store-level write-time M1 enforcement -> durable local state -> FastAPI JSON endpoints -> Oracle VM/systemd -> GitHub Actions deployment (with an independent on-the-wire M1 cadence probe as a deploy gate).

The provider API key is never committed. Set `REALMARKET_API_KEY` in the runtime environment.

## Default symbols

XAUUSD, EURUSD, GBPUSD, USDJPY, GBPJPY, AUDUSD, USDCAD, NZDUSD, XAGUSD, USOIL.

The list is configurable through `PYSGRID_SYMBOLS`.

## Data-integrity rules

- `candles_1m` contains completed candles only.
- The provider's `/candles` WebSocket (`timeFrame=M1`) is the live OHLCV source. **This endpoint's true bar
  resolution has not been independently confirmed with production credentials from within this repository's
  environment** (no outbound network access to RealMarketAPI and no API key are available there). Run
  `tools/verify_m1_provider.py` against a real key before trusting the feed; the deploy pipeline runs it
  automatically on every deploy and fails closed if it doesn't observe a genuine 60-second cadence.
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

## Endpoints

- `GET /health`
- `GET /public/live.json`
- `GET /public/forex.json`
- `GET /public/{symbol}.json`
- `GET /metrics`

The JSON contains only completed M1 candles. Provider timestamps are normalized to UTC ISO-8601 strings.

## Security

Do not put API keys or SSH private keys in Git. GitHub Actions receives the Oracle SSH key through a repository secret. The market-data key belongs on the Oracle host as `REALMARKET_API_KEY`.
