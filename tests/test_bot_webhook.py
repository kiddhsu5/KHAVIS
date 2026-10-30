"""Webhook security + background dispatch for the Telegram bot.

Covers: WEBHOOK_SECRET validation at startup, the empty-secret bypass in the
route (``compare_digest(b"", b"")`` is True), constant-time path-token check,
immediate 200 + background processing, and ``update_id`` de-duplication.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from bot.background import BackgroundDispatcher, UpdateDeduper  # noqa: E402
from bot.models import IncomingMessage  # noqa: E402
from bot.secrets import BotSecrets, validate_webhook_secret  # noqa: E402
from bot.webhook import webhook_route  # noqa: E402

GOOD_SECRET = "s3cr3t_Token-abc"


def _update(update_id: int = 1, text: str = "hello") -> dict:
    return {
        "update_id": update_id,
        "message": {"message_id": 7, "chat": {"id": 42}, "from": {"id": 9}, "text": text},
    }


# ---------------------------------------------------------------------------
# BotSecrets.from_env
# ---------------------------------------------------------------------------
class TestSecretsFromEnv:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "123456:ABC-def")
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("BOT_MODE", raising=False)

    def test_webhook_mode_requires_secret(self):
        with pytest.raises(RuntimeError, match="WEBHOOK_SECRET is required"):
            BotSecrets.from_env(mode="webhook")

    def test_default_mode_is_webhook(self):
        with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
            BotSecrets.from_env()

    def test_no_longer_falls_back_to_bot_token(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SECRET", "")
        with pytest.raises(RuntimeError):
            BotSecrets.from_env(mode="webhook")

    def test_polling_mode_does_not_need_secret(self):
        s = BotSecrets.from_env(mode="polling")
        assert s.webhook_secret == ""

    def test_bot_mode_env_is_honoured(self, monkeypatch):
        monkeypatch.setenv("BOT_MODE", "polling")
        assert BotSecrets.from_env().webhook_secret == ""

    def test_valid_secret_accepted(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SECRET", GOOD_SECRET)
        assert BotSecrets.from_env(mode="webhook").webhook_secret == GOOD_SECRET

    @pytest.mark.parametrize(
        "bad",
        [
            "123456:ABC",  # bot-token shaped — ':' is not allowed by Telegram
            "has space",
            "# inline comment",
            '"quoted"',
            "x" * 257,
        ],
    )
    def test_invalid_secret_rejected(self, monkeypatch, bad):
        monkeypatch.setenv("WEBHOOK_SECRET", bad)
        with pytest.raises(RuntimeError, match=r"\[A-Za-z0-9_-\]"):
            BotSecrets.from_env(mode="webhook")

    def test_validate_boundaries(self):
        assert validate_webhook_secret("a") == "a"
        assert validate_webhook_secret("A" * 256) == "A" * 256


# ---------------------------------------------------------------------------
# webhook_route
# ---------------------------------------------------------------------------
def _app(secret: str, handler, *, background: bool = False) -> tuple[FastAPI, dict]:
    secrets = BotSecrets(bot_token="t", webhook_secret=secret)
    app = FastAPI()
    holder: dict = {"deduper": UpdateDeduper()}

    @app.post("/webhook")
    async def hook(request: Request):
        kwargs = {}
        if background:
            if "dispatcher" not in holder:
                holder["dispatcher"] = BackgroundDispatcher(2)
            kwargs = {"dispatcher": holder["dispatcher"], "deduper": holder["deduper"]}
        return await webhook_route(request, secrets, handler, **kwargs)

    return app, holder


class TestWebhookRoute:
    def test_empty_expected_secret_rejects_even_without_header(self):
        calls: list[IncomingMessage] = []

        async def handler(m):
            calls.append(m)

        app, _ = _app("", handler)
        with TestClient(app) as c:
            r = c.post("/webhook", json=_update())
            assert r.status_code == 403
            r = c.post("/webhook", json=_update(), headers={"X-Telegram-Bot-Api-Secret-Token": ""})
            assert r.status_code == 403
        assert calls == []

    def test_wrong_secret_is_403(self):
        async def handler(m):
            raise AssertionError("must not be called")

        app, _ = _app(GOOD_SECRET, handler)
        with TestClient(app) as c:
            r = c.post(
                "/webhook", json=_update(), headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}
            )
        assert r.status_code == 403

    def test_correct_secret_dispatches(self):
        calls: list[IncomingMessage] = []

        async def handler(m):
            calls.append(m)

        app, _ = _app(GOOD_SECRET, handler)
        with TestClient(app) as c:
            r = c.post(
                "/webhook",
                json=_update(),
                headers={"X-Telegram-Bot-Api-Secret-Token": GOOD_SECRET},
            )
        assert r.status_code == 200
        assert [m.chat_id for m in calls] == [42]

    def test_returns_before_slow_handler_finishes_and_dedupes(self):
        started = []
        release = asyncio.Event()

        async def slow_handler(m):
            started.append(m.update_id)
            await release.wait()

        app, holder = _app(GOOD_SECRET, slow_handler, background=True)
        headers = {"X-Telegram-Bot-Api-Secret-Token": GOOD_SECRET}
        with TestClient(app) as c:
            # If the route awaited the handler, this would hang forever.
            r1 = c.post("/webhook", json=_update(5), headers=headers)
            r2 = c.post("/webhook", json=_update(5), headers=headers)  # redelivery
            r3 = c.post("/webhook", json=_update(6), headers=headers)
            assert (r1.status_code, r2.status_code, r3.status_code) == (200, 200, 200)
            dispatcher: BackgroundDispatcher = holder["dispatcher"]

            async def _finish():
                await asyncio.sleep(0.05)
                release.set()
                await dispatcher.drain(timeout_s=2)

            c.portal.call(_finish)
        assert sorted(started) == [5, 6], "duplicate update_id must be dropped"


class TestPathTokenRoute:
    def test_path_token_requires_exact_secret(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_PAIRING_PATH", "/nonexistent/pairing.json")
        from bot.main import _make_app

        app = _make_app(BotSecrets(bot_token="t", webhook_secret=GOOD_SECRET))
        c = TestClient(app)  # no context manager: skip lifespan (no pool discovery)
        assert c.post("/webhook/wrong", json=_update()).status_code == 404
        assert c.post(f"/webhook/{GOOD_SECRET}x", json=_update()).status_code == 404
        r = c.post(f"/webhook/{GOOD_SECRET}", json={"update_id": 1})
        assert r.status_code == 403  # path ok, header missing

    def test_path_token_rejected_when_secret_empty(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_PAIRING_PATH", "/nonexistent/pairing.json")
        from bot.main import _make_app

        app = _make_app(BotSecrets(bot_token="t", webhook_secret=""))
        c = TestClient(app)
        assert c.post("/webhook/x", json=_update()).status_code == 404


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------
class TestUpdateDeduper:
    def test_seen_twice(self):
        d = UpdateDeduper()
        assert d.seen(1) is False
        assert d.seen(1) is True
        assert d.seen(2) is False

    def test_size_bound(self):
        d = UpdateDeduper(max_entries=3)
        for i in range(10):
            d.seen(i)
        assert len(d._seen) <= 3
        assert d.seen(0) is False  # evicted long ago

    def test_ttl_expiry(self, monkeypatch):
        import bot.background as bg

        now = [1000.0]
        monkeypatch.setattr(bg.time, "monotonic", lambda: now[0])
        d = UpdateDeduper(ttl_s=10)
        d.seen(1)
        now[0] += 11
        assert d.seen(1) is False


class TestBackgroundDispatcher:
    def test_concurrency_cap_and_error_isolation(self):
        async def scenario():
            d = BackgroundDispatcher(max_concurrency=2)
            running = 0
            peak = 0

            async def job(i):
                nonlocal running, peak
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.01)
                running -= 1
                if i == 0:
                    raise RuntimeError("boom")

            for i in range(6):
                d.submit(job(i))
            await d.drain(timeout_s=2)
            return peak, d.pending

        peak, pending = asyncio.run(scenario())
        assert peak == 2
        assert pending == 0

    def test_aclose_cancels_pending(self):
        async def scenario():
            d = BackgroundDispatcher(1)
            d.submit(asyncio.sleep(10))
            d.submit(asyncio.sleep(10))  # still waiting on the semaphore
            await asyncio.sleep(0)
            await d.aclose()
            return d.pending

        assert asyncio.run(scenario()) == 0


class TestPollingBackground:
    def test_polling_submits_to_dispatcher(self, monkeypatch):
        import bot.polling as polling

        class _Resp:
            status_code = 200

            def json(self):
                return {"ok": True, "result": [_update(11), _update(12)]}

        stop = asyncio.Event()

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, params=None):
                stop.set()  # one round only
                return _Resp()

        monkeypatch.setattr(polling.httpx, "AsyncClient", _Client)
        seen: list[int] = []
        gate = asyncio.Event()

        async def handler(m):
            await gate.wait()
            seen.append(m.update_id)

        async def scenario():
            d = BackgroundDispatcher(4)
            secrets = BotSecrets(bot_token="t", webhook_secret="")
            # Returns even though every handler is blocked on ``gate``.
            await asyncio.wait_for(
                polling.long_poll_loop(secrets, handler, stop=stop, dispatcher=d), 2
            )
            assert seen == []
            gate.set()
            await d.drain(timeout_s=2)

        asyncio.run(scenario())
        assert sorted(seen) == [11, 12]


class TestIncomingLog:
    def test_message_text_is_not_printed(self, monkeypatch, capsys):
        from bot.handlers import HandlerDeps
        from bot.main import _build_handler

        deps = HandlerDeps(telegram=object(), allowlist=[], backends={})
        handle = _build_handler(router=None, deps=deps)  # type: ignore[arg-type]
        msg = IncomingMessage(update_id=1, chat_id=42, text="/nosuchcommand my-secret-prompt")
        asyncio.run(handle(msg))
        out = capsys.readouterr().out
        assert "chat_id=42" in out
        assert "my-secret-prompt" not in out
        assert f"len={len(msg.text)}" in out


class TestLazyApp:
    def test_import_does_not_require_webhook_secret(self, monkeypatch):
        """``python -m bot.main --mode polling`` must not die at import time."""
        import importlib

        monkeypatch.setenv("BOT_TOKEN", "1:x")
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("BOT_MODE", raising=False)
        monkeypatch.setenv("TELEGRAM_PAIRING_PATH", "/nonexistent/pairing.json")
        import bot.main as main_mod

        main_mod = importlib.reload(main_mod)
        with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
            _ = main_mod.app
        monkeypatch.setenv("WEBHOOK_SECRET", GOOD_SECRET)
        assert main_mod.app is main_mod.app  # built once, cached
