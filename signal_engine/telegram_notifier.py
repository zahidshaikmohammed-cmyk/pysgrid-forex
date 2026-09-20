"""Optional Telegram alert channel.

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID set in the environment; if
either is missing, this channel is simply inactive (the terminal bell and
optional desktop toast in reporter.py still work on their own). Setup:

1. Message @BotFather on Telegram, send /newbot, follow the prompts. It
   gives you a bot token that looks like `123456789:AAExampleTokenValue`.
2. Send your new bot any message (e.g. "hi") so it has a chat to talk back to.
3. Visit https://api.telegram.org/bot<your-token>/getUpdates in a browser
   and find "chat":{"id": ...} in the response -- that number is your
   TELEGRAM_CHAT_ID. (For a group, add the bot to the group first, send a
   message there, then look for the group's negative chat id the same way.)
4. Set both as environment variables before running the engine:
       $env:TELEGRAM_BOT_TOKEN = "123456789:AAExampleTokenValue"
       $env:TELEGRAM_CHAT_ID = "987654321"
5. Verify it actually works before relying on it:
       python -m signal_engine.main --test-telegram

This was never tested end-to-end against Telegram's live API from the
environment this was built in (outbound network access to arbitrary third
-party hosts was not available there) -- step 5 exists specifically so you
can confirm it yourself before trusting it during a real trading session.
"""
from __future__ import annotations

import logging

import httpx

from .config import EngineConfig
from .models import Action, Signal

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, config: EngineConfig, client: httpx.Client | None = None):
        self._token = config.telegram_bot_token
        self._chat_id = config.telegram_chat_id
        self._client = client or httpx.Client(timeout=10.0)
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "TelegramNotifier":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def send(self, text: str) -> bool:
        if not self.enabled:
            log.debug("Telegram not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing); skipping")
            return False

        url = f"{_API_BASE}/bot{self._token}/sendMessage"
        try:
            response = self._client.post(
                url, json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"}
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.warning("Telegram notification failed: %s", exc)
            return False

    def notify_signals(self, signals: list[Signal]) -> bool:
        actionable = [s for s in signals if s.action != Action.WAIT]
        if not actionable:
            return False

        lines = ["<b>pysgrid-forex signal</b>"]
        for s in actionable:
            price = f" @ {s.price:.5f}" if s.price is not None else ""
            lines.append(f"{s.symbol}: <b>{s.action.value}</b> ({s.confidence:.0f}%){price}")
            if s.suggested_stop_loss is not None and s.suggested_take_profit is not None:
                lines.append(f"  SL {s.suggested_stop_loss:.5f} / TP {s.suggested_take_profit:.5f}")
            if s.reasons:
                lines.append(f"  {s.reasons[0]}")
        return self.send("\n".join(lines))
