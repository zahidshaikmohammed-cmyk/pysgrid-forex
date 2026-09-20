import httpx

from signal_engine.config import EngineConfig
from signal_engine.models import Action, Signal
from signal_engine.telegram_notifier import TelegramNotifier


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _signal(symbol: str, action: Action, **kw) -> Signal:
    return Signal(symbol=symbol, action=action, confidence=80.0, price=1.2345, **kw)


def test_disabled_without_both_credentials():
    config = EngineConfig(telegram_bot_token="", telegram_chat_id="")
    with TelegramNotifier(config) as t:
        assert t.enabled is False
        assert t.send("hi") is False

    config = EngineConfig(telegram_bot_token="tok", telegram_chat_id="")
    with TelegramNotifier(config) as t:
        assert t.enabled is False

    config = EngineConfig(telegram_bot_token="", telegram_chat_id="chat")
    with TelegramNotifier(config) as t:
        assert t.enabled is False


def test_disabled_notifier_never_makes_a_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    config = EngineConfig(telegram_bot_token="", telegram_chat_id="")
    with TelegramNotifier(config, client=_client(handler)) as t:
        t.send("should not be sent")

    assert calls == []


def test_send_posts_to_the_correct_bot_and_chat():
    import json as _json
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = _json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    config = EngineConfig(telegram_bot_token="12345:ABCDEF", telegram_chat_id="999")
    with TelegramNotifier(config, client=_client(handler)) as t:
        ok = t.send("hello world")

    assert ok is True
    assert captured["url"] == "https://api.telegram.org/bot12345:ABCDEF/sendMessage"
    assert captured["payload"]["chat_id"] == "999"
    assert captured["payload"]["text"] == "hello world"


def test_send_returns_false_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    config = EngineConfig(telegram_bot_token="bad", telegram_chat_id="999")
    with TelegramNotifier(config, client=_client(handler)) as t:
        assert t.send("hi") is False


def test_send_returns_false_on_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    config = EngineConfig(telegram_bot_token="tok", telegram_chat_id="999")
    with TelegramNotifier(config, client=_client(handler)) as t:
        assert t.send("hi") is False


def test_notify_signals_is_noop_for_wait_only():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    config = EngineConfig(telegram_bot_token="tok", telegram_chat_id="999")
    with TelegramNotifier(config, client=_client(handler)) as t:
        result = t.notify_signals([_signal("XAUUSD", Action.WAIT)])

    assert result is False
    assert calls == []


def test_notify_signals_formats_buy_with_sl_tp_and_reason():
    import json as _json
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = _json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    config = EngineConfig(telegram_bot_token="tok", telegram_chat_id="999")
    sig = _signal(
        "XAUUSD", Action.BUY,
        suggested_stop_loss=1.20, suggested_take_profit=1.30,
        reasons=["UP trend confirmed"],
    )
    with TelegramNotifier(config, client=_client(handler)) as t:
        ok = t.notify_signals([sig])

    assert ok is True
    text = captured["payload"]["text"]
    assert "XAUUSD" in text and "BUY" in text
    assert "SL 1.20000" in text and "TP 1.30000" in text
    assert "UP trend confirmed" in text
