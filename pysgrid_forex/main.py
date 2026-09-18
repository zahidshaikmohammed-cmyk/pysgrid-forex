from __future__ import annotations

import argparse
import asyncio
import logging

from .config import Settings
from .engine import Engine


async def run_once(dry_run: bool) -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if dry_run:
        print("Pysgrid Forex dry-run OK")
        print(f"symbols={','.join(settings.symbols)}")
        print(f"timeframe={settings.timeframe}")
        print("api_key_configured=False (intentional)")
        return
    engine = Engine(settings)
    try:
        await engine.start()
    finally:
        await engine.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run_once(args.dry_run))


if __name__ == "__main__":
    main()
