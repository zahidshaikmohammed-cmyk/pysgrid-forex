# Oracle deployment

## 1. GitHub

Repository secret already required:

- `ORACLE_SSH_KEY`

Create repository variables:

- `ORACLE_HOST` = Oracle public IP
- `ORACLE_USER` = `ubuntu`
- `ORACLE_PORT` = `22`

Do not put the private key in variables or files.

## 2. Oracle

The workflow installs the application at:

`/opt/pysgrid-forex`

Create the runtime environment:

```bash
sudo mkdir -p /etc/pysgrid-forex
sudo nano /etc/pysgrid-forex/pysgrid-forex.env
```

Minimum:

```text
REALMARKET_API_KEY=YOUR_KEY
PYSGRID_SYMBOLS=XAUUSD,EURUSD,GBPUSD,USDJPY,GBPJPY,AUDUSD,USDCAD,NZDUSD,XAGUSD,USOIL
PYSGRID_PORT=8080
```

Then:

```bash
sudo chmod 600 /etc/pysgrid-forex/pysgrid-forex.env
sudo systemctl restart pysgrid-forex
sudo journalctl -u pysgrid-forex -f
```

## 3. Oracle networking

Allow TCP 8080 in the Oracle VCN/security list or, preferably, put Nginx on 80/443 and keep the application bound to localhost.

The service itself listens on 8080 for the first deployment.

## 4. Verify

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/public/XAUUSD.json
```

## 5. Important

The first deployment can run without a RealMarketAPI key. The service will report degraded/no-data rather than crash. After purchasing Plus, add the key to the Oracle environment and restart the service.
