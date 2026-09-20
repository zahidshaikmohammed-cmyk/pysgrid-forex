"""Minimal, dependency-free .env loader.

Not python-dotenv -- just enough to stop retyping TELEGRAM_BOT_TOKEN and
friends in every fresh PowerShell window. Real environment variables always
win: anything already set with `$env:X = ...` (or `export X=...`) in your
current shell is left alone, so this only fills in what isn't already set.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return

    for raw_line in file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
