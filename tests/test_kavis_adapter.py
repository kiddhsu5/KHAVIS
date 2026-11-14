"""Unit tests for the K.A.V.I.S. dispatch adapter (deploy/kavis_adapter.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("kavis_adapter", ROOT / "deploy" / "kavis_adapter.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def test_flatten_messages_roles_and_parts() -> None:
    prompt = adapter._flatten_messages(
        [
            {"role": "system", "content": "你是 K.A.V.I.S."},
            {"role": "user", "content": "第一個任務"},
            {"role": "assistant", "content": "先前結論"},
            {
                "role": "user",
                "content": [{"type": "text", "text": "第二部分"}, {"type": "image_url"}],
            },
            {"role": "user", "content": ""},
            "not-a-dict",
        ]
    )
    assert "[系統指示]\n你是 K.A.V.I.S." in prompt
    assert "第一個任務" in prompt
    assert "[前次回覆]\n先前結論" in prompt
    assert "第二部分" in prompt
    # 空訊息與非 dict 被略過
    assert prompt.count("第一部分") == 0


def test_flatten_messages_empty_and_truncation() -> None:
    assert adapter._flatten_messages(None) == ""
    assert adapter._flatten_messages([]) == ""
    assert adapter._flatten_messages([{"role": "user", "content": "   "}]) == ""

    long_prompt = adapter._flatten_messages(
        [{"role": "user", "content": "x" * (adapter.MAX_PROMPT_CHARS + 5000)}]
    )
    assert len(long_prompt) == adapter.MAX_PROMPT_CHARS


def test_model_name_sanitisation() -> None:
    assert adapter._MODEL_RE.match("MiniMax-M3")
    assert adapter._MODEL_RE.match("provider/model:v2")
    assert not adapter._MODEL_RE.match("bad model; rm -rf /")
    assert not adapter._MODEL_RE.match("")
    assert not adapter._MODEL_RE.match("x" * 81)


def test_run_hermes_missing_binary() -> None:
    # 不存在的 hermes 路徑應誠實回報錯誤，不得拋例外
    result = adapter._run_hermes("ping", "bad model; rm -rf /")
    # 模型名不合法時不帶 -m；hermes 不存在 → launch failed
    assert "error" in result
    assert "launch failed" in result["error"]


@pytest.mark.parametrize("payload", [None, [], "text", 42])
def test_flatten_messages_bad_payloads(payload) -> None:
    assert adapter._flatten_messages(payload) == ""


def test_kavis_v21_dual_axis_governance_backend_and_api(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import io
    import json

    from bot.backends import kavis_tencent
    from bot.models import DispatchEnvelope

    monkeypatch.setenv("KAVIS_TENCENT_DISPATCH_URL", "http://127.0.0.1:18090/v1/chat/completions")
    monkeypatch.setenv("KAVIS_HUB_URL", "http://127.0.0.1:18090")

    class _FakeResp:
        def __init__(self, data: dict) -> None:
            self._buf = io.BytesIO(json.dumps(data).encode("utf-8"))
            self.status = 200

        def read(self) -> bytes:
            return self._buf.read()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _fake_urlopen(req, **kwargs):
        url = req.full_url
        if url.endswith("/v1/chat/completions"):
            return _FakeResp(
                {
                    "model": "Minimax-M3.1-Flash",
                    "choices": [{"message": {"content": "L2 審批掛起中"}}],
                    "kavis_risk_tier": "L2",
                    "kavis_weight_tier": "W1",
                    "kavis_status": "awaiting_approval",
                    "kavis_approval_id": "apr-12345",
                    "kavis_pool": "minimax-fast",
                }
            )
        if url.endswith("/api/v1/overview"):
            return _FakeResp(
                {
                    "ok": True,
                    "version": "2.1",
                    "active_facts_count": 42,
                    "pending_approvals": [{"id": "apr-12345", "risk_tier": "L2"}],
                    "pending_proposals": [{"id": 1, "action_type": "file_edit"}],
                }
            )
        if "/api/v1/approvals/" in url and url.endswith("/resolve"):
            return _FakeResp({"ok": True, "resolved": True})
        if "/api/v1/proposals/" in url and url.endswith("/resolve"):
            return _FakeResp({"ok": True, "resolved": True})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(kavis_tencent.urllib.request, "urlopen", _fake_urlopen)

    backend = kavis_tencent.KavisTencentBackend()
    res = asyncio.run(backend.run(DispatchEnvelope(chat_id=1, prompt="重啟服務")))
    assert res.ok is True
    assert res.extra["kavis_risk_tier"] == "L2"
    assert res.extra["kavis_weight_tier"] == "W1"
    assert res.extra["kavis_status"] == "awaiting_approval"
    assert res.extra["kavis_approval_id"] == "apr-12345"
    assert res.extra["kavis_pool"] == "minimax-fast"

    gov = kavis_tencent.fetch_kavis_governance_status()
    assert gov["ok"] is True
    assert gov["engine"] == "kavis-v2.1"
    assert len(gov["pending_approvals"]) == 1
    assert len(gov["pending_proposals"]) == 1

    r_apr = kavis_tencent.resolve_kavis_approval("apr-12345", approved=True)
    assert r_apr["ok"] is True
    assert r_apr["approval_id"] == "apr-12345"

    r_prop = kavis_tencent.resolve_kavis_proposal(1, approved=True)
    assert r_prop["ok"] is True
    assert r_prop["proposal_id"] == "1"
