#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/pysgrid-forex
ENV_DIR=/etc/pysgrid-forex

sudo mkdir -p "$APP_DIR" "$ENV_DIR"
sudo chown -R ubuntu:ubuntu "$APP_DIR"

cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
sudo cp deploy/systemd/pysgrid-forex.service /etc/systemd/system/pysgrid-forex.service

if [ ! -f "$ENV_DIR/pysgrid-forex.env" ]; then
  sudo touch "$ENV_DIR/pysgrid-forex.env"
  sudo chmod 600 "$ENV_DIR/pysgrid-forex.env"
fi

sudo systemctl daemon-reload
sudo systemctl enable pysgrid-forex
sudo systemctl restart pysgrid-forex
sudo systemctl --no-pager --full status pysgrid-forex || true
