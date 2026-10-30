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
            {"role": "user", "content": [{"type": "text", "text": "第二部分"}, {"type": "image_url"}]},
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
