# Pysgrid Forex

Production-ready 1-minute OHLCV ingestion for Pysgrid using RealMarketAPI.

## Architecture

RealMarketAPI WebSocket -> M1 candle validation -> durable local state -> REST recovery -> FastAPI JSON endpoints -> Oracle VM/systemd -> GitHub Actions deployment.

The provider API key is never committed. Set `REALMARKET_API_KEY` in the runtime environment.

## Default symbols

XAUUSD, EURUSD, GBPUSD, USDJPY, GBPJPY, AUDUSD, USDCAD, NZDUSD, XAGUSD, USOIL.

The list is configurable through `PYSGRID_SYMBOLS`.

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
