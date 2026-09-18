# Pysgrid Forex

Production-ready 1-minute OHLCV ingestion for Pysgrid using RealMarketAPI.

## Architecture

RealMarketAPI completed-candle WebSocket -> strict M1 validation -> durable local state -> FastAPI JSON endpoints -> Oracle VM/systemd -> GitHub Actions deployment.

The provider API key is never committed. Set `REALMARKET_API_KEY` in the runtime environment.

## Default symbols

XAUUSD, EURUSD, GBPUSD, USDJPY, GBPJPY, AUDUSD, USDCAD, NZDUSD, XAGUSD, USOIL.

The list is configurable through `PYSGRID_SYMBOLS`.

## Data-integrity rules

- `candles_1m` contains completed candles only.
- The dedicated RealMarketAPI candle WebSocket is the live OHLCV source.
- REST `/candle` data is accepted only when a returned multi-bar series has exact 60-second spacing.
- Non-M1 REST data is rejected instead of being relabeled as M1.
- Existing pre-fix state is invalidated once by the internal data schema migration.
- Missing live candles are reported as gaps; the system never fabricates M1 bars from 5-minute data.

RealMarketAPI documents WebSocket streaming for Plus plans and recommends persistent WebSocket delivery for live candles. The provider's current documentation also exposes M1 as a supported timeframe. The implementation deliberately keeps REST recovery fail-safe because observed `/candle?timeFrame=M1` responses were 5-minute spaced.

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
