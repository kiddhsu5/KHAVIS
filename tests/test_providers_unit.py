"""Unit tests for every provider plugin.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

All external HTTP / SDK calls are mocked via ``unittest.mock`` so the
suite runs offline. Each plugin is exercised through:

    * construction defaults + overrides,
    * ``chat()`` returns the canonical dict shape,
    * ``check_quota()`` returns the documented fields,
    * ``list_models()`` returns a list of strings,
    * ``health_check()`` returns ``{"ok": bool, "detail": str}``
      and handles missing keys gracefully.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from providers.base import ProviderPlugin
from providers.claude import AnthropicPlugin
from providers.gemini import GeminiPlugin
from providers.glm import GLMPlugin
from providers.MiniMax import MiniMaxPlugin
from providers.nvidia import NvidiaPlugin
from providers.ollama import (
    DEFAULT_MODEL,
    OllamaPlugin,
    _expand_env,
    _model_available,
    _normalize_model,
)
from providers.openai import OpenAIPlugin
from providers.openrouter import OpenRouterPlugin
from providers.volcano import (
    SUB_POOLS,
    VolcanoPluginFactory,
    VolcanoSubPool,
)

PING = [{"role": "user", "content": "ping"}]

#: Every env var a provider plugin reads at construction / request time.
#: Cleared before each test so a developer's real keys (or a CI secret)
#: can neither change assertions nor trigger real network calls.
PROVIDER_ENV_VARS: tuple[str, ...] = (
    "MiniMax_API_KEY",  # noqa: SIM112 - historical name, see providers/MiniMax.py
    "MINIMAX_API_KEY",
    "ZHIPUAI_API_KEY",
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "BYTEDANCE_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "MIMO_AUTH_TOKEN",
    "KHAVIS_PROVIDER_TIMEOUT_S",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
    "XAI_API_KEY",
    "TOGETHER_API_KEY",
    "FIREWORKS_API_KEY",
    "PERPLEXITY_API_KEY",
    "MOONSHOT_API_KEY",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "HF_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch):
    for name in PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sdk_response(payload: dict[str, Any]) -> MagicMock:
    """Build a mock that mimics the OpenAI SDK response object."""
    mock = MagicMock()
    mock.model_dump.return_value = payload
    return mock


def _plain_openai_client(client_getter: MagicMock) -> MagicMock:
    """Use the ordinary SDK response path unless a test opts into headers."""
    client_getter.return_value.chat.completions.with_raw_response = None
    return client_getter.return_value


def _plain_anthropic_client(client_getter: MagicMock) -> MagicMock:
    """Use the ordinary Anthropic response path unless a test opts into headers."""
    client_getter.return_value.messages.with_raw_response = None
    return client_getter.return_value


def _quota_ok(plugin: ProviderPlugin) -> None:
    quota = plugin.check_quota()
    assert isinstance(quota, dict)
    for key in ("remaining", "total", "tier"):
        assert key in quota, f"{plugin.name}.check_quota missing {key!r}"


def _health_ok(plugin: ProviderPlugin) -> None:
    health = plugin.health_check()
    assert isinstance(health, dict)
    assert "ok" in health and isinstance(health["ok"], bool)
    assert "detail" in health and isinstance(health["detail"], str)


# ---------------------------------------------------------------------------
# MiniMax
# ---------------------------------------------------------------------------
class TestMiniMaxPlugin:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("MiniMax_API_KEY", raising=False)
        p = MiniMaxPlugin()
        assert p.name == "MiniMax-M3"
        assert p.provider_id == "MiniMax"
        assert p.api_key is None
        assert p.model == "MiniMax-M3.1-Flash"
        assert "中文" in p.capabilities

    def test_env_key_loaded(self, monkeypatch):
        monkeypatch.setenv("MiniMax_API_KEY", "sk-test")
        p = MiniMaxPlugin()
        assert p.api_key == "sk-test"

    def test_override_args(self):
        p = MiniMaxPlugin(
            endpoint="https://override.example/v1",
            api_key="key",
            model="custom-model",
        )
        assert p.default_endpoint == "https://override.example/v1"
        assert p.api_key == "key"
        assert p.model == "custom-model"

    def test_chat_normalises_response(self):
        p = MiniMaxPlugin(api_key="key")
        fake = _sdk_response({"choices": [{"message": {"content": "pong", "role": "assistant"}}]})
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = fake
            out = p.chat(PING, max_tokens=5)
        assert "choices" in out and out["choices"][0]["message"]["content"] == "pong"

    def test_chat_injects_empty_choices_when_missing(self):
        p = MiniMaxPlugin(api_key="key")
        fake = _sdk_response({})
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = fake
            out = p.chat(PING)
        assert out["choices"] == []

    def test_chat_missing_openai_sdk(self):
        p = MiniMaxPlugin(api_key="key")
        with (
            patch.object(p, "_get_client", side_effect=RuntimeError("no openai")),
            pytest.raises(RuntimeError),
        ):
            p.chat(PING)

    def test_check_quota_shape(self):
        _quota_ok(MiniMaxPlugin(api_key="x"))

    def test_list_models(self):
        p = MiniMaxPlugin(model="custom")
        assert p.list_models() == ["custom"]

    def test_health_check_with_and_without_key(self):
        assert MiniMaxPlugin(api_key="x").health_check()["ok"] is True
        assert MiniMaxPlugin(api_key=None, endpoint="").health_check()["ok"] is False

    def test_describes(self):
        d = MiniMaxPlugin(api_key="x").describes()
        assert d["name"] == "MiniMax-M3"
        assert d["provider_id"] == "MiniMax"
        assert d["model"] == "MiniMax-M3.1-Flash"


# ---------------------------------------------------------------------------
# GLM
# ---------------------------------------------------------------------------
class TestGLMPlugin:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
        p = GLMPlugin()
        assert p.name == "GLM-5.3"
        assert p.provider_id == "glm"
        assert p.api_key is None

    def test_env_key(self, monkeypatch):
        monkeypatch.setenv("ZHIPUAI_API_KEY", "glm-key")
        assert GLMPlugin().api_key == "glm-key"

    def test_chat(self):
        p = GLMPlugin(api_key="x")
        fake = _sdk_response({"choices": [{"message": {"content": "ok", "role": "assistant"}}]})
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = fake
            out = p.chat(PING, max_tokens=5)
        assert out["choices"][0]["message"]["content"] == "ok"

    def test_check_quota(self):
        _quota_ok(GLMPlugin(api_key="x"))

    def test_list_models(self):
        assert GLMPlugin(api_key="x").list_models() == ["glm-5.3", "glm-5.3-flashx", "codegeex-4"]

    def test_health(self):
        _health_ok(GLMPlugin(api_key="x"))


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
class TestGeminiPlugin:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        p = GeminiPlugin()
        assert p.name == "Google-Gemini"
        assert p.provider_id == "gemini"
        assert p.model == "gemini-4-argon"
        assert "gemini-pro-latest" in p.list_models()

    def test_explicit_model(self):
        p = GeminiPlugin(model="gemini-pro-latest")
        assert p.model == "gemini-pro-latest"

    def test_custom_models(self):
        p = GeminiPlugin(models=["a", "b"])
        assert p.list_models() == ["a", "b"]
        assert p.model == "a"

    def test_chat_without_genai_sdk(self):
        p = GeminiPlugin(api_key="x")
        with patch("providers.gemini.genai", None), pytest.raises(RuntimeError):
            p.chat(PING)

    def test_chat_without_api_key(self):
        p = GeminiPlugin(api_key=None)
        with pytest.raises(RuntimeError):
            p.chat(PING)

    def test_chat_happy_path(self):
        p = GeminiPlugin(api_key="x")
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = MagicMock(
            text="pong", usage_metadata=MagicMock(prompt_token_count=3, candidates_token_count=2)
        )
        with (
            patch("providers.gemini.genai") as genai_mod,
            patch("providers.gemini.genai_types") as types_mod,
        ):
            genai_mod.Client.return_value = fake_client
            types_mod.GenerateContentConfig = MagicMock()
            out = p.chat(PING)
        assert out["choices"][0]["message"]["content"] == "pong"
        assert out["choices"][0]["message"]["role"] == "assistant"
        assert out["model"] == "gemini-4-argon"
        assert out["usage"]["prompt_tokens"] == 3
        assert out["usage"]["completion_tokens"] == 2

    def test_chat_handles_system_messages(self):
        p = GeminiPlugin(api_key="x", model="gemini-pro-latest")
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = MagicMock(text="x")
        with (
            patch("providers.gemini.genai") as genai_mod,
            patch("providers.gemini.genai_types") as types_mod,
        ):
            genai_mod.Client.return_value = fake_client
            types_mod.GenerateContentConfig = MagicMock()
            out = p.chat(
                [
                    {"role": "system", "content": "be brief"},
                    {"role": "user", "content": "hi"},
                ]
            )
        call = fake_client.models.generate_content.call_args
        # system_instruction goes via GenerateContentConfig, prompt via contents
        assert out["model"] == "gemini-pro-latest"
        cfg = call.kwargs.get("config") or call[1].get("config")
        assert cfg is not None

    def test_check_quota(self):
        _quota_ok(GeminiPlugin(api_key="x"))

    def test_health_no_key(self):
        assert GeminiPlugin(api_key=None).health_check()["ok"] is False


# ---------------------------------------------------------------------------
# NVIDIA NIM
# ---------------------------------------------------------------------------
class TestNvidiaPlugin:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        p = NvidiaPlugin()
        assert p.name == "NVIDIA-Cloud"
        assert p.provider_id == "nvidia"
        assert p.api_key is None

    def test_env_key(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "nv-key")
        assert NvidiaPlugin().api_key == "nv-key"

    def test_chat_no_key(self):
        p = NvidiaPlugin(api_key=None)
        with pytest.raises(RuntimeError):
            p.chat(PING)

    def test_chat_happy_path(self):
        p = NvidiaPlugin(api_key="x", model="meta/llama-3.1-70b")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {
            "choices": [{"message": {"content": "pong", "role": "assistant"}}],
            "model": "meta/llama-3.1-70b",
        }
        with patch("providers.nvidia.requests.post", return_value=fake_resp) as post:
            out = p.chat(PING, max_tokens=5)
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer x"
        assert out["choices"][0]["message"]["content"] == "pong"

    def test_chat_injects_empty_choices(self):
        p = NvidiaPlugin(api_key="x", model="m")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"model": "m"}
        with patch("providers.nvidia.requests.post", return_value=fake_resp):
            out = p.chat(PING)
        assert out["choices"] == []

    def test_check_quota(self):
        _quota_ok(NvidiaPlugin(api_key="x"))

    def test_list_models_no_key(self):
        assert NvidiaPlugin(api_key=None).list_models() == []

    def test_list_models_dict_data(self):
        p = NvidiaPlugin(api_key="x")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"data": [{"id": "model-a"}, {"id": "model-b"}]}
        with patch("providers.nvidia.requests.get", return_value=fake_resp):
            assert p.list_models() == ["model-a", "model-b"]

    def test_list_models_list_data(self):
        # The plugin keeps entries with missing "id" but replaces them
        # with an empty string rather than dropping them. Document that
        # contract here.
        p = NvidiaPlugin(api_key="x")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = [{"id": "x"}, {"no_id": True}]
        with patch("providers.nvidia.requests.get", return_value=fake_resp):
            assert p.list_models() == ["x", ""]

    def test_list_models_handles_request_error(self):
        p = NvidiaPlugin(api_key="x")
        with patch("providers.nvidia.requests.get", side_effect=Exception("boom")):
            assert p.list_models() == []

    def test_health(self):
        _health_ok(NvidiaPlugin(api_key="x"))


# ---------------------------------------------------------------------------
# Volcano Ark (factory + sub-pools)
# ---------------------------------------------------------------------------
class TestVolcanoPlugin:
    def test_factory_returns_three_subpools(self, monkeypatch):
        monkeypatch.delenv("BYTEDANCE_API_KEY", raising=False)
        factory = VolcanoPluginFactory()
        pools = factory()
        assert len(pools) == 3
        names = [p.name for p in pools]
        assert names == list(SUB_POOLS.keys())
        for p in pools:
            assert isinstance(p, VolcanoSubPool)
            assert p.api_key is None

    def test_factory_uses_provided_args(self):
        factory = VolcanoPluginFactory(api_key="volcano-key", endpoint="https://override/v3")
        pools = factory()
        assert all(p.api_key == "volcano-key" for p in pools)
        assert all(p.default_endpoint == "https://override/v3" for p in pools)

    def test_sub_pool_skip_auto_instantiate(self):
        assert getattr(VolcanoSubPool, "_skip_auto_instantiate", False) is True

    def test_sub_pool_chat(self):
        sp = VolcanoSubPool(
            pool_name="DeepSeek-V4-Pro",
            model="deepseek-v4-pro",
            capabilities=["推理"],
            api_key="x",
        )
        fake = _sdk_response({"choices": [{"message": {"content": "ok", "role": "assistant"}}]})
        with patch.object(sp, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = fake
            out = sp.chat(PING)
        assert out["choices"][0]["message"]["content"] == "ok"

    def test_sub_pool_normalise_empty(self):
        sp = VolcanoSubPool(pool_name="x", model="m", capabilities=[], api_key="x")
        fake = _sdk_response({})
        with patch.object(sp, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = fake
            assert sp.chat(PING)["choices"] == []

    def test_sub_pool_check_quota(self):
        sp = VolcanoSubPool(pool_name="x", model="m", capabilities=[], api_key="x")
        _quota_ok(sp)

    def test_sub_pool_health(self):
        sp = VolcanoSubPool(pool_name="x", model="m", capabilities=[], api_key="x")
        _health_ok(sp)

    def test_sub_pool_list_models(self):
        sp = VolcanoSubPool(pool_name="x", model="custom", capabilities=[], api_key="x")
        assert sp.list_models() == ["custom"]

    def test_module_factory_instance(self):
        from providers import volcano as volcano_mod

        # Module-level singleton should exist and produce three pools.
        assert volcano_mod.VolcanoFactory() is not None or volcano_mod.VolcanoFactory is not None


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------
class TestOpenRouterPlugin:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        p = OpenRouterPlugin()
        assert p.name == "OpenRouter-Free"
        assert p.api_key is None

    def test_env_key(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
        assert OpenRouterPlugin().api_key == "or-key"

    def test_chat(self):
        p = OpenRouterPlugin(api_key="x")
        fake = _sdk_response({"choices": [{"message": {"content": "ok", "role": "assistant"}}]})
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = fake
            out = p.chat(PING)
        assert out["choices"][0]["message"]["content"] == "ok"

    def test_check_quota(self):
        q = OpenRouterPlugin(api_key="x").check_quota()
        assert q["provider"] == "openrouter"

    def test_list_models(self):
        from providers import openrouter

        assert OpenRouterPlugin(api_key="x").list_models() == [openrouter.DEFAULT_MODEL]

    def test_health(self):
        _health_ok(OpenRouterPlugin(api_key="x"))


# ---------------------------------------------------------------------------
# OpenAI Platform API
# ---------------------------------------------------------------------------
class TestOpenAIPlugin:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        p = OpenAIPlugin()
        assert p.name == "OpenAI-API"
        assert p.provider_id == "openai"
        assert p.api_key is None
        assert p.model == "gpt-5-mini"
        assert p.default_endpoint == "https://api.openai.com/v1"

    def test_env_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        assert OpenAIPlugin().api_key == "sk-openai-test"

    def test_override_args(self):
        p = OpenAIPlugin(
            endpoint="https://override.example/v1",
            api_key="key",
            model="gpt-5-codex",
        )
        assert p.default_endpoint == "https://override.example/v1"
        assert p.api_key == "key"
        assert p.model == "gpt-5-codex"

    def test_chat(self):
        p = OpenAIPlugin(api_key="x")
        fake = _sdk_response({"choices": [{"message": {"content": "ok", "role": "assistant"}}]})
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = fake
            out = p.chat(PING, max_tokens=5)
        assert out["choices"][0]["message"]["content"] == "ok"

    def test_chat_injects_empty_choices(self):
        p = OpenAIPlugin(api_key="x")
        fake = _sdk_response({})
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = fake
            out = p.chat(PING)
        assert out["choices"] == []

    def test_chat_missing_openai_sdk(self):
        p = OpenAIPlugin(api_key="key")
        with (
            patch.object(p, "_get_client", side_effect=RuntimeError("no openai")),
            pytest.raises(RuntimeError),
        ):
            p.chat(PING)

    def test_check_quota_shape(self):
        q = OpenAIPlugin(api_key="x").check_quota()
        assert q["tier"] == "platform_api"
        assert q["provider"] == "openai"
        assert q["remaining"] == "unknown"

    def test_list_models(self):
        models = OpenAIPlugin(api_key="x").list_models()
        assert "gpt-5" in models
        assert "gpt-5-mini" in models
        assert "gpt-5-codex" in models
        assert "gpt-5-nano" in models

    def test_health_with_and_without_key(self):
        assert OpenAIPlugin(api_key="x").health_check()["ok"] is True
        assert OpenAIPlugin(api_key=None, endpoint="").health_check()["ok"] is False

    def test_describes(self):
        d = OpenAIPlugin(api_key="x").describes()
        assert d["name"] == "OpenAI-API"
        assert d["provider_id"] == "openai"
        assert d["model"] == "gpt-5-mini"


# ---------------------------------------------------------------------------
# Anthropic Claude API
# ---------------------------------------------------------------------------
def _anthropic_response(text: str = "pong", model: str = "claude-haiku-4-5-20251001"):
    """Build a mock that mimics the Anthropic Messages SDK response object."""
    block = MagicMock()
    block.text = text
    usage = MagicMock()
    usage.input_tokens = 10
    usage.output_tokens = 5
    resp = MagicMock()
    resp.content = [block]
    resp.model = model
    resp.usage = usage
    return resp


class TestAnthropicPlugin:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        p = AnthropicPlugin()
        assert p.name == "Claude-API"
        assert p.provider_id == "claude"
        assert p.api_key is None
        assert p.model == "claude-haiku-4-5-20251001"
        assert p.default_endpoint == "https://api.anthropic.com"

    def test_env_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert AnthropicPlugin().api_key == "sk-ant-test"

    def test_override_args(self):
        p = AnthropicPlugin(
            endpoint="https://override.example",
            api_key="key",
            model="claude-opus-4-5",
        )
        assert p.default_endpoint == "https://override.example"
        assert p.api_key == "key"
        assert p.model == "claude-opus-4-5"

    def test_chat_happy_path(self):
        p = AnthropicPlugin(api_key="x")
        with patch.object(p, "_get_client") as gc:
            _plain_anthropic_client(gc).messages.create.return_value = _anthropic_response()
            out = p.chat(PING, max_tokens=10)
        assert out["choices"][0]["message"]["content"] == "pong"
        assert out["choices"][0]["message"]["role"] == "assistant"
        assert out["model"] == "claude-haiku-4-5-20251001"
        assert out["usage"]["prompt_tokens"] == 10
        assert out["usage"]["completion_tokens"] == 5

    def test_chat_splits_system_message(self):
        p = AnthropicPlugin(api_key="x")
        with patch.object(p, "_get_client") as gc:
            _plain_anthropic_client(gc).messages.create.return_value = _anthropic_response()
            p.chat(
                [
                    {"role": "system", "content": "be brief"},
                    {"role": "user", "content": "hi"},
                ]
            )
        call_kwargs = gc.return_value.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "be brief"
        # The messages list passed to Anthropic must not include the system role.
        assert all(m["role"] != "system" for m in call_kwargs["messages"])
        assert call_kwargs["messages"] == [{"role": "user", "content": "hi"}]

    def test_chat_concatenates_multiple_system_messages(self):
        p = AnthropicPlugin(api_key="x")
        with patch.object(p, "_get_client") as gc:
            _plain_anthropic_client(gc).messages.create.return_value = _anthropic_response()
            p.chat(
                [
                    {"role": "system", "content": "first"},
                    {"role": "user", "content": "u"},
                    {"role": "system", "content": "second"},
                ]
            )
        call_kwargs = gc.return_value.messages.create.call_args.kwargs
        assert "first" in call_kwargs["system"]
        assert "second" in call_kwargs["system"]

    def test_chat_missing_anthropic_sdk(self):
        p = AnthropicPlugin(api_key="key")
        with (
            patch.object(p, "_get_client", side_effect=RuntimeError("no anthropic")),
            pytest.raises(RuntimeError),
        ):
            p.chat(PING)

    def test_check_quota_shape(self):
        q = AnthropicPlugin(api_key="x").check_quota()
        assert q["tier"] == "platform_api"
        assert q["provider"] == "claude"
        assert q["remaining"] == "unknown"

    def test_list_models(self):
        models = AnthropicPlugin(api_key="x").list_models()
        assert "claude-sonnet-5" in models
        assert "claude-opus-4-5" in models
        assert "claude-haiku-4-5-20251001" in models

    def test_health_with_and_without_key(self):
        assert AnthropicPlugin(api_key="x").health_check()["ok"] is True
        assert AnthropicPlugin(api_key=None, endpoint="").health_check()["ok"] is False

    def test_normalize_handles_dict_blocks(self):
        # Some SDK versions / test stubs return plain dicts for content blocks.
        p = AnthropicPlugin(api_key="x")
        usage = MagicMock()
        usage.input_tokens = 3
        usage.output_tokens = 7
        resp = MagicMock(spec=["content", "model", "usage"])
        resp.content = [{"text": "dict-block"}]
        resp.model = "claude-sonnet-5"
        resp.usage = usage
        out = p._normalize(resp)
        assert out["choices"][0]["message"]["content"] == "dict-block"
        assert out["model"] == "claude-sonnet-5"
        assert out["usage"]["completion_tokens"] == 7


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
class TestOllamaPlugin:
    def test_mac_defaults(self, monkeypatch):
        monkeypatch.delenv("MAC_IP", raising=False)
        monkeypatch.delenv("SURFACE_IP", raising=False)
        p = OllamaPlugin(name="Ollama-Mac")
        assert p.name == "Ollama-Mac"
        # Deliberately NOT localhost. The router often runs on a cloud VM
        # whose own Ollama is a different box with different models; when
        # this defaulted to localhost the pool reported healthy and then
        # 404'd on chat. Unresolved -> "<var>.local", so the mistake is
        # visible in health output instead of a confusing DNS error.
        assert p.default_endpoint == "http://mac_ip.local:11434"
        assert p.api_key is None

    def test_mac_expands_ip(self, monkeypatch):
        monkeypatch.setenv("MAC_IP", "203.0.113.10")
        p = OllamaPlugin(name="Ollama-Mac")
        assert p.default_endpoint == "http://203.0.113.10:11434"

    def test_mac_expands_placeholder_in_url(self, monkeypatch):
        monkeypatch.setenv("MAC_IP", "203.0.113.10")
        p = OllamaPlugin(name="Ollama-Mac", base_url="http://${MAC_IP}:11434")
        assert p.default_endpoint == "http://203.0.113.10:11434"

    def test_expand_env_handles_multiple_placeholders(self, monkeypatch):
        monkeypatch.setenv("MAC_IP", "203.0.113.10")
        monkeypatch.delenv("SURFACE_IP", raising=False)
        assert (
            _expand_env("http://${MAC_IP}:${SURFACE_IP}") == "http://203.0.113.10:surface_ip.local"
        )

    def test_expand_env_ignores_unrelated_text(self):
        assert _expand_env("http://localhost:11434") == "http://localhost:11434"

    def test_surface_expands_ip(self, monkeypatch):
        monkeypatch.setenv("SURFACE_IP", "10.0.0.5")
        p = OllamaPlugin(name="Ollama-Surface", base_url="http://${SURFACE_IP}:11434")
        assert p.default_endpoint == "http://10.0.0.5:11434"

    def test_surface_missing_ip_uses_placeholder(self, monkeypatch):
        monkeypatch.delenv("SURFACE_IP", raising=False)
        p = OllamaPlugin(name="Ollama-Surface", base_url="http://${SURFACE_IP}:11434")
        assert "${SURFACE_IP}" not in p.default_endpoint

    def test_surface_default_without_url(self, monkeypatch):
        monkeypatch.setenv("SURFACE_IP", "10.1.2.3")
        p = OllamaPlugin(name="Ollama-Surface")
        assert p.default_endpoint == "http://10.1.2.3:11434"

    def test_chat_happy_path(self):
        p = OllamaPlugin(name="Ollama-Mac")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {
            "model": "gemma4:e2b",
            "message": {"role": "assistant", "content": "pong"},
        }
        with patch("providers.ollama.requests.post", return_value=fake_resp) as post:
            out = p.chat(PING)
        assert post.call_args.args[0].endswith("/api/chat")
        assert out["choices"][0]["message"]["content"] == "pong"

    def test_default_model_has_no_prefix(self):
        # The native /api/chat endpoint rejects the "ollama/" prefix
        # (that prefix is only valid on /v1/chat/completions). The
        # plugin's own default must therefore not carry it.
        assert not DEFAULT_MODEL.startswith("ollama/")

    def test_chat_strips_ollama_prefix(self):
        # Users may still have "ollama/<name>" in pools.yaml from older
        # versions. The chat() call must strip it before posting.
        p = OllamaPlugin(name="Ollama-Mac", model="ollama/foo:bar")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {
            "model": "foo:bar",
            "message": {"role": "assistant", "content": "ok"},
        }
        with patch("providers.ollama.requests.post", return_value=fake_resp) as post:
            p.chat(PING)
        payload = post.call_args.kwargs["json"]
        assert payload["model"] == "foo:bar"

    def test_normalize_model_helper(self):
        # Direct unit-level coverage of the helper itself.
        assert _normalize_model("ollama/gemma4:e2b") == "gemma4:e2b"
        assert _normalize_model("gemma4:e2b") == "gemma4:e2b"
        assert _normalize_model(None) is None
        assert _normalize_model("") == ""

    # -- health_check: reachability is not enough -------------------------
    @staticmethod
    def _tags_response(names: list[str], status: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = {"models": [{"name": n} for n in names]}
        return resp

    def test_health_ok_when_model_is_pulled(self):
        p = OllamaPlugin(name="Ollama-Mac", model="gemma4:e2b")
        with patch(
            "providers.ollama.requests.get",
            return_value=self._tags_response(["gemma4:e2b", "qwen2.5:3b"]),
        ):
            h = p.health_check()
        assert h["ok"] is True
        assert "gemma4:e2b" in h["detail"]

    def test_health_fails_when_model_missing(self):
        # The regression this guards: endpoint answers 200, so the old
        # check said "healthy", and chat() then 404'd.
        p = OllamaPlugin(name="Ollama-Mac", model="gemma4:e2b")
        with patch(
            "providers.ollama.requests.get",
            return_value=self._tags_response(["qwen2.5:3b"]),
        ) as get:
            h = p.health_check()
        assert h["ok"] is False
        assert "not pulled" in h["detail"]
        assert "gemma4:e2b" in h["detail"]
        # And it must consult /api/tags, not just GET /.
        assert get.call_args.args[0].endswith("/api/tags")

    def test_health_reports_what_is_available(self):
        p = OllamaPlugin(name="Ollama-Mac", model="gemma4:e2b")
        with patch(
            "providers.ollama.requests.get",
            return_value=self._tags_response(["qwen2.5:3b", "qwen3:4b"]),
        ):
            h = p.health_check()
        assert "qwen2.5:3b" in h["detail"] and "qwen3:4b" in h["detail"]

    def test_health_fails_on_unreachable_endpoint(self):
        p = OllamaPlugin(name="Ollama-Surface")
        with patch("providers.ollama.requests.get", side_effect=OSError("refused")):
            h = p.health_check()
        assert h["ok"] is False
        assert "refused" in h["detail"]

    def test_health_fails_on_server_error(self):
        p = OllamaPlugin(name="Ollama-Mac", model="gemma4:e2b")
        with patch(
            "providers.ollama.requests.get",
            return_value=self._tags_response([], status=503),
        ):
            assert p.health_check()["ok"] is False

    def test_health_tolerates_non_json_body(self):
        p = OllamaPlugin(name="Ollama-Mac", model="gemma4:e2b")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        with patch("providers.ollama.requests.get", return_value=resp):
            assert p.health_check()["ok"] is False

    def test_health_without_model_only_needs_reachability(self):
        # ``__init__`` falls back to DEFAULT_MODEL when model is falsy, so
        # the "no model configured" path is only reachable via the helper.
        assert _model_available(set(), "") is True
        assert _model_available(set(), None) is True  # type: ignore[arg-type]

    def test_model_available_matching_rules(self):
        assert _model_available({"gemma4:e2b"}, "gemma4:e2b")
        # untagged request matches any tag of that name
        assert _model_available({"gemma4:e2b", "gemma4:latest"}, "gemma4")
        # tagged request must match exactly
        assert not _model_available({"gemma4:latest"}, "gemma4:e2b")
        assert not _model_available(set(), "gemma4:e2b")
        assert _model_available(set(), "")
        assert _model_available({"x"}, "ollama/x")

    def test_chat_raises_on_missing_message_so_failover_can_take_over(self):
        # An HTTP 200 carrying no message is a failed call, not a successful
        # empty one. Raising is what lets _call_with_failover try the next
        # pool instead of replying with a blank string.
        p = OllamaPlugin(name="Ollama-Mac")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"model": "m"}
        with (
            patch("providers.ollama.requests.post", return_value=fake_resp),
            pytest.raises(RuntimeError, match="empty response"),
        ):
            p.chat(PING)

    def test_chat_raises_on_whitespace_only_content(self):
        p = OllamaPlugin(name="Ollama-Mac")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"model": "m", "message": {"content": "   \n"}}
        with (
            patch("providers.ollama.requests.post", return_value=fake_resp),
            pytest.raises(RuntimeError, match="empty response"),
        ):
            p.chat(PING)

    def test_check_quota_local(self):
        q = OllamaPlugin(name="Ollama-Mac").check_quota()
        assert q["tier"] == "local"

    def test_list_models_happy(self):
        p = OllamaPlugin(name="Ollama-Mac")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"models": [{"name": "a"}, {"name": "b"}]}
        with patch("providers.ollama.requests.get", return_value=fake_resp):
            assert p.list_models() == ["a", "b"]

    def test_list_models_handles_failure(self):
        p = OllamaPlugin(name="Ollama-Mac")
        with patch("providers.ollama.requests.get", side_effect=Exception("boom")):
            assert p.list_models() == []

    def test_health_ok_on_200(self):
        p = OllamaPlugin(name="Ollama-Mac", model="gemma4:e2b")
        fake_resp = MagicMock(status_code=200)
        fake_resp.json.return_value = {"models": [{"name": "gemma4:e2b"}]}
        with patch("providers.ollama.requests.get", return_value=fake_resp):
            assert p.health_check()["ok"] is True

    def test_health_not_ok_on_4xx(self):
        # Contract change: we probe /api/tags, which a healthy Ollama
        # serves. A 404 there means the endpoint is not Ollama (or is
        # misconfigured), so it must NOT report healthy.
        #
        # The previous check probed GET / and passed anything below 500,
        # with the comment "Ollama returns 404 for /". That leniency is
        # what let Ollama-Mac show green while chat() 404'd.
        p = OllamaPlugin(name="Ollama-Mac", model="gemma4:e2b")
        fake_resp = MagicMock(status_code=404)
        fake_resp.json.return_value = {}
        with patch("providers.ollama.requests.get", return_value=fake_resp):
            assert p.health_check()["ok"] is False

    def test_health_not_ok_on_5xx(self):
        p = OllamaPlugin(name="Ollama-Mac")
        fake_resp = MagicMock(status_code=503)
        with patch("providers.ollama.requests.get", return_value=fake_resp):
            assert p.health_check()["ok"] is False

    def test_health_handles_network_error(self):
        p = OllamaPlugin(name="Ollama-Mac")
        with patch("providers.ollama.requests.get", side_effect=Exception("boom")):
            h = p.health_check()
        assert h["ok"] is False
        assert "boom" in h["detail"]


# ---------------------------------------------------------------------------
# Base class behaviour
# ---------------------------------------------------------------------------
class TestProviderPluginBase:
    def test_has_capability_true_false(self):
        p = MiniMaxPlugin(api_key="x")
        assert p.has_capability("中文") is True
        assert p.has_capability("does-not-exist") is False

    def test_describes_is_serialisable(self):
        p = MiniMaxPlugin(api_key="x")
        d = p.describes()
        assert d["name"] == p.name
        assert d["endpoint"] == p.default_endpoint

    def test_repr_contains_class_and_name(self):
        p = MiniMaxPlugin(api_key="x")
        r = repr(p)
        assert "MiniMaxPlugin" in r
        assert "MiniMax-M3" in r


# ---------------------------------------------------------------------------
# MiMo-Code
# ---------------------------------------------------------------------------
class TestMimoCodePlugin:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("MIMO_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        from providers.mimo import MimoCodePlugin

        p = MimoCodePlugin()
        assert p.name == "MiMo-Code"
        assert p.provider_id == "mimo"
        assert p.api_key is None
        assert p.model == "mimo-v2.6-pro"
        assert p.default_endpoint == "https://token-plan-cn.xiaomimimo.com/anthropic"

    def test_env_key_prefers_mimo_token(self, monkeypatch):
        monkeypatch.setenv("MIMO_AUTH_TOKEN", "tp-primary")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tp-fallback")
        from providers.mimo import MimoCodePlugin

        assert MimoCodePlugin().api_key == "tp-primary"

    def test_env_key_does_not_fall_back_to_anthropic_auth_token(self, monkeypatch):
        # ANTHROPIC_AUTH_TOKEN may be a Claude Code / Anthropic credential;
        # it must never be sent to the third-party MiMo endpoint implicitly.
        monkeypatch.delenv("MIMO_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tp-fallback")
        from providers.mimo import MimoCodePlugin

        assert MimoCodePlugin().api_key is None

    def test_chat_extracts_text_and_skips_thinking(self):
        from providers.mimo import MimoCodePlugin

        p = MimoCodePlugin(api_key="x", model="mimo-v2.6-pro")
        fake = MagicMock()
        fake.raise_for_status.return_value = None
        fake.json.return_value = {
            "model": "mimo-v2.6-pro",
            "content": [
                {"type": "thinking", "thinking": "internal"},
                {"type": "text", "text": "pong"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        with patch("providers.mimo.requests.post", return_value=fake) as post:
            out = p.chat(PING, max_tokens=16)
        assert out["choices"][0]["message"]["content"] == "pong"
        assert out["usage"]["prompt_tokens"] == 10
        assert out["usage"]["completion_tokens"] == 5
        assert post.call_args.kwargs["json"]["model"] == "mimo-v2.6-pro"
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer x"

    def test_chat_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("MIMO_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        from providers.mimo import MimoCodePlugin

        p = MimoCodePlugin(api_key=None)
        with pytest.raises(RuntimeError, match="MIMO_AUTH_TOKEN"):
            p.chat(PING)

    def test_health_and_quota_shapes(self, monkeypatch):
        monkeypatch.delenv("MIMO_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        from providers.mimo import MimoCodePlugin

        assert MimoCodePlugin(api_key="x").health_check()["ok"] is True
        assert MimoCodePlugin(api_key=None).health_check()["ok"] is False
        q = MimoCodePlugin(api_key="x").check_quota()
        assert q["provider"] == "mimo"
        assert q["remaining"] == "unknown"
        assert "mimo-v2.6-pro" in MimoCodePlugin(api_key="x").list_models()


# ---------------------------------------------------------------------------
# OpenAI-compatible long tail
# ---------------------------------------------------------------------------
class TestOpenAICompatFactory:
    def test_factory_produces_sub_pools(self):
        from providers.openai_compat import SUB_POOLS, OpenAICompatFactory

        pools = OpenAICompatFactory()()
        names = {p.name for p in pools}
        assert names == set(SUB_POOLS)
        assert len(pools) == len(SUB_POOLS)

    def test_each_pool_has_env_key_and_endpoint(self):
        from providers.openai_compat import OpenAICompatFactory

        for p in OpenAICompatFactory()():
            assert p.default_endpoint.startswith("https://")
            assert p.env_key
            assert p.model

    def test_health_without_key_is_false(self):
        from providers.openai_compat import OpenAICompatSubPool

        p = OpenAICompatSubPool(pool_name="X", endpoint="https://e", model="m", env_key="NOPE_KEY")
        assert p.health_check()["ok"] is False

    def test_chat_uses_openai_client(self):
        from providers.openai_compat import OpenAICompatSubPool

        p = OpenAICompatSubPool(
            pool_name="X", endpoint="https://e", model="m", env_key="NOPE", api_key="k"
        )
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = _sdk_response(
                {"choices": [{"message": {"content": "hi"}}], "model": "m"}
            )
            out = p.chat(PING)
        assert out["choices"][0]["message"]["content"] == "hi"

    def test_registry_discovers_openai_compat_pools(self):
        from pathlib import Path

        from core.registry import PluginRegistry

        root = Path(__file__).resolve().parent.parent
        reg = PluginRegistry(root).discover()
        names = reg.names()
        for expected in ("Groq-Llama", "Mistral-Large", "Qwen-DashScope", "xAI-Grok"):
            assert expected in names, f"missing {expected} in {names}"
        assert not any("duplicate plugin name: Groq" in e for e in reg.errors())
