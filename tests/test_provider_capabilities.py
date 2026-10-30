"""Tests for provider-reported model capabilities and attachment safety."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.provider_capabilities import (
    build_agent_capabilities,
    build_model_catalog,
    discover_provider_capabilities,
    format_model_ref,
    parse_model_ref,
)
from scripts.status_server import _store_dispatch_attachments


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = json.dumps(payload).encode()

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_capabilities_use_provider_returned_modes_and_map_active_model() -> None:
    payload = {
        "data": [
            {
                "id": "kimi-k3.1-pro",
                "supported_reasoning_efforts": ["high", "xhigh"],
                "supported_parameters": ["reasoning_effort"],
            },
            {"id": "plain-model", "supported_parameters": ["temperature"]},
        ]
    }
    specs = [
        {
            "env": "JUAPI_API_KEY",
            "label": "JUAPI",
            "base_url_env": "JUAPI_BASE_URL",
            "default_base_url": "https://provider.test/v1",
        }
    ]
    values = {"JUAPI_API_KEY": "test-key", "JUAPI_BASE_URL": "https://provider.test/v1"}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)) as open_url:
        providers = discover_provider_capabilities(specs, values)

    assert open_url.call_count == 1
    assert providers[0]["status"] == "ok"
    assert providers[0]["models"][0]["thinking_modes"] == ["high", "xhigh"]
    agents = build_agent_capabilities({"codex": "kimi-k3.1-pro", "other": "plain-model"}, providers)
    assert agents["codex"]["thinking_modes"] == ["high", "xhigh"]
    assert agents["codex"]["thinking_parameter"] == "reasoning_effort"
    assert agents["other"]["thinking_modes"] == []


def test_unconfigured_provider_is_auto_only_without_network_call() -> None:
    specs = [
        {"env": "JUAPI_API_KEY", "label": "JUAPI", "default_base_url": "https://provider.test/v1"}
    ]
    with patch("urllib.request.urlopen") as open_url:
        providers = discover_provider_capabilities(specs, {})
    assert providers[0]["status"] == "not_configured"
    assert providers[0]["models"] == []
    open_url.assert_not_called()


def test_same_model_id_keeps_provider_namespace() -> None:
    assert format_model_ref("JUAPI_API_KEY", "gemini-4-argon") == "JUAPI_API_KEY::gemini-4-argon"
    assert format_model_ref("GOOGLE_API_KEY", "gemini-4-argon") == "GOOGLE_API_KEY::gemini-4-argon"
    assert (
        format_model_ref("JUAPI_API_KEY", "models/gemini-4-argon")
        == "JUAPI_API_KEY::gemini-4-argon"
    )
    assert parse_model_ref("JUAPI_API_KEY::gemini-4-argon") == ("JUAPI_API_KEY", "gemini-4-argon")
    assert parse_model_ref("gemini-4-argon") == (None, "gemini-4-argon")


def test_model_catalog_distinguishes_duplicate_provider_models() -> None:
    providers = [
        {
            "env": "JUAPI_API_KEY",
            "label": "JUAPI",
            "status": "ok",
            "models": [{"id": "gemini-4-argon", "ref": "JUAPI_API_KEY::gemini-4-argon"}],
        },
        {
            "env": "GOOGLE_API_KEY",
            "label": "Google Gemini",
            "status": "ok",
            "models": [{"id": "gemini-4-argon", "ref": "GOOGLE_API_KEY::gemini-4-argon"}],
        },
    ]
    catalog = build_model_catalog(
        {"claude": "gemini-4-argon"},
        providers,
        agent_provider_envs={"claude": {"JUAPI_API_KEY", "GOOGLE_API_KEY"}},
    )
    assert catalog["options"]["claude"] == [
        "gemini-4-argon",
        "JUAPI_API_KEY::gemini-4-argon",
        "GOOGLE_API_KEY::gemini-4-argon",
    ]
    assert catalog["active_model_refs"]["claude"] == "gemini-4-argon"
    assert (
        catalog["catalog"]["JUAPI_API_KEY::gemini-4-argon"]["display_name"]
        == "JUAPI · gemini-4-argon"
    )


def test_model_catalog_respects_persisted_selection() -> None:
    """使用者勾選的清單是權威：供應商回傳的其他模型不得自動復活。"""
    providers = [
        {
            "env": "ZHIPUAI_API_KEY",
            "label": "智譜 GLM",
            "status": "ok",
            "models": [
                {"id": m, "ref": f"ZHIPUAI_API_KEY::{m}"}
                for m in ("glm-4.5", "glm-5", "glm-5.3-flashx", "glm-5.3")
            ],
        },
    ]
    # 使用者只勾選兩個 → 動態清單不得覆蓋
    catalog = build_model_catalog(
        {"opencode": "ZHIPUAI_API_KEY::glm-5.3-flashx"},
        providers,
        fallback_options={"opencode": ["ZHIPUAI_API_KEY::glm-5.3-flashx", "ZHIPUAI_API_KEY::glm-5"]},
        agent_provider_envs={"opencode": {"ZHIPUAI_API_KEY"}},
    )
    assert catalog["options"]["opencode"] == [
        "ZHIPUAI_API_KEY::glm-5.3-flashx",
        "ZHIPUAI_API_KEY::glm-5",
    ]
    assert "ZHIPUAI_API_KEY::glm-4.5" not in catalog["options"]["opencode"]
    assert catalog["active_model_refs"]["opencode"] == "ZHIPUAI_API_KEY::glm-5.3-flashx"

    # 當前模型被取消勾選 → 仍可運作，但 active 會被補到清單最前面
    catalog2 = build_model_catalog(
        {"opencode": "ZHIPUAI_API_KEY::glm-4.5"},
        providers,
        fallback_options={"opencode": ["ZHIPUAI_API_KEY::glm-5"]},
        agent_provider_envs={"opencode": {"ZHIPUAI_API_KEY"}},
    )
    assert catalog2["options"]["opencode"][0] == "ZHIPUAI_API_KEY::glm-4.5"

    # 無勾選紀錄 → 維持動態行為（向後相容）
    catalog3 = build_model_catalog(
        {"opencode": "glm-5"},
        providers,
        agent_provider_envs={"opencode": {"ZHIPUAI_API_KEY"}},
    )
    assert "ZHIPUAI_API_KEY::glm-4.5" in catalog3["options"]["opencode"]


def test_attachment_storage_blocks_traversal_and_sensitive_files(tmp_path: Path) -> None:
    content = base64.b64encode(b"hello attachment").decode()
    refs = _store_dispatch_attachments(
        {"attachments": [{"name": "notes.txt", "type": "text/plain", "data_base64": content}]},
        tmp_path,
    )
    assert len(refs) == 1
    assert Path(refs[0]["path"]).read_bytes() == b"hello attachment"
    assert Path(refs[0]["path"]).is_relative_to(tmp_path)

    for name in ("../escape.txt", ".env", "private.pem"):
        with pytest.raises(ValueError):
            _store_dispatch_attachments(
                {"attachments": [{"name": name, "data_base64": content}]},
                tmp_path,
            )


def test_attachment_storage_rejects_invalid_base64(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Base64"):
        _store_dispatch_attachments(
            {"attachments": [{"name": "broken.txt", "data_base64": "not-base64"}]},
            tmp_path,
        )
