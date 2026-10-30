"""Regression tests for the 2026-10-02 provider review fixes.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Everything is mocked; no test touches the network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from providers import nvidia as nvidia_mod
from providers._anthropic_format import to_anthropic_messages
from providers._common import normalize_choices, raise_if_empty, resolve_timeout
from providers._openai_base import OpenAIChatPlugin, is_reasoning_model
from providers.claude import AnthropicPlugin
from providers.gemini import CONTINUE_PROMPT, GeminiPlugin, to_gemini_contents
from providers.glm import GLMPlugin
from providers.mimo import MimoCodePlugin
from providers.MiniMax import MiniMaxPlugin
from providers.nvidia import NvidiaPlugin
from providers.ollama import OllamaPlugin
from providers.openai import OpenAIPlugin
from providers.openai_compat import SUB_POOLS as COMPAT_POOLS
from providers.openai_compat import OpenAICompatSubPool
from providers.openrouter import OpenRouterPlugin
from providers.volcano import VolcanoSubPool
from tests.test_providers_unit import PROVIDER_ENV_VARS

PING = [{"role": "user", "content": "ping"}]
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch):
    for name in PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _sdk(payload: dict[str, Any]) -> MagicMock:
    m = MagicMock()
    m.model_dump.return_value = payload
    return m


def _plain_openai_client(client_getter: MagicMock) -> MagicMock:
    """Use the ordinary SDK response path unless a test opts into headers."""
    client_getter.return_value.chat.completions.with_raw_response = None
    return client_getter.return_value


def _plain_anthropic_client(client_getter: MagicMock) -> MagicMock:
    """Use the ordinary Anthropic response path unless a test opts into headers."""
    client_getter.return_value.messages.with_raw_response = None
    return client_getter.return_value


def _ok(content: Any = "ok", **msg: Any) -> MagicMock:
    return _sdk({"choices": [{"message": {"role": "assistant", "content": content, **msg}}]})


# ---------------------------------------------------------------------------
# Shared OpenAI base
# ---------------------------------------------------------------------------
class TestOpenAIBase:
    @pytest.mark.parametrize(
        "cls", [GLMPlugin, MiniMaxPlugin, OpenAIPlugin, OpenRouterPlugin, OpenAICompatSubPool]
    )
    def test_all_share_base(self, cls):
        assert issubclass(cls, OpenAIChatPlugin)

    def test_skip_flag_not_inherited(self):
        assert OpenAIChatPlugin._skip_auto_instantiate is True
        for cls in (GLMPlugin, MiniMaxPlugin, OpenAIPlugin, OpenRouterPlugin):
            assert cls._skip_auto_instantiate is False
        assert VolcanoSubPool._skip_auto_instantiate is True
        assert OpenAICompatSubPool._skip_auto_instantiate is True

    def test_client_has_timeout_and_no_sdk_retries(self):
        p = GLMPlugin(api_key="k")
        with patch("providers._openai_base.OpenAI") as ctor:
            p._get_client()
        kw = ctor.call_args.kwargs
        assert kw["timeout"] == 60.0
        assert kw["max_retries"] == 0
        assert kw["base_url"] == p.default_endpoint

    def test_timeout_env_and_metadata_override(self, monkeypatch):
        monkeypatch.setenv("KHAVIS_PROVIDER_TIMEOUT_S", "15")
        with patch("providers._openai_base.OpenAI") as ctor:
            OpenRouterPlugin(api_key="k")._get_client()
        assert ctor.call_args.kwargs["timeout"] == 15.0
        with patch("providers._openai_base.OpenAI") as ctor:
            OpenRouterPlugin(api_key="k", metadata={"timeout_s": 7})._get_client()
        assert ctor.call_args.kwargs["timeout"] == 7.0

    def test_resolve_timeout_precedence_and_garbage(self, monkeypatch):
        monkeypatch.setenv("KHAVIS_PROVIDER_TIMEOUT_S", "nonsense")
        assert resolve_timeout() == 60.0
        assert resolve_timeout({"timeout": 5}, override=3) == 3.0
        assert resolve_timeout({"timeout": -1}, default=120) == 120.0

    def test_explicit_empty_key_and_endpoint_are_kept(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "from-env")
        p = OpenAIPlugin(api_key="", endpoint="")
        assert p.api_key == ""
        assert p.default_endpoint == ""
        assert p.health_check()["ok"] is False
        assert OpenAIPlugin().api_key == "from-env"

    def test_health_requires_model(self):
        assert GLMPlugin(api_key="k", model="").health_check()["ok"] is False

    def test_none_content_normalised(self):
        p = GLMPlugin(api_key="k")
        call = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = _ok(
                None, tool_calls=[call]
            )
            out = p.chat(PING)
        assert out["choices"][0]["message"]["content"] == ""
        assert out["choices"][0]["message"]["tool_calls"] == [call]

    def test_empty_content_raises_for_failover(self):
        p = MiniMaxPlugin(api_key="k")
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = _ok(None)
            with pytest.raises(RuntimeError, match="empty response"):
                p.chat(PING)

    def test_list_content_flattened(self):
        data = normalize_choices(
            {"choices": [{"message": {"content": [{"type": "text", "text": "a"}, "b"]}}]}
        )
        assert data["choices"][0]["message"]["content"] == "a\nb"
        raise_if_empty(data, "x")  # does not raise

    def test_kwargs_model_none_uses_default(self):
        p = GLMPlugin(api_key="k")
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.return_value = _ok()
            p.chat(PING, model=None)
        assert gc.return_value.chat.completions.create.call_args.kwargs["model"] == "glm-5.3"


# ---------------------------------------------------------------------------
# Reasoning models (gpt-5 / o-series)
# ---------------------------------------------------------------------------
class TestReasoningParams:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("gpt-5-mini", True),
            ("gpt-5", True),
            ("o3-mini", True),
            ("o4-mini", True),
            ("openai/gpt-5", True),
            ("gpt-5-chat-latest", False),
            ("gpt-4o", False),
            ("glm-5.3", False),
            (None, False),
        ],
    )
    def test_prefix_detection(self, model, expected):
        assert is_reasoning_model(model) is expected

    def test_reasoning_model_params_adapted_up_front(self):
        p = OpenAIPlugin(api_key="k")  # gpt-5-mini
        with patch.object(p, "_get_client") as gc:
            create = _plain_openai_client(gc).chat.completions.create
            create.return_value = _ok()
            p.chat(PING, max_tokens=5, temperature=0.2)
        assert create.call_count == 1
        kw = create.call_args.kwargs
        assert "temperature" not in kw
        assert "max_tokens" not in kw
        assert kw["max_completion_tokens"] == 5

    def test_non_reasoning_model_keeps_params(self):
        p = OpenAIPlugin(api_key="k", model="gpt-4o")
        with patch.object(p, "_get_client") as gc:
            create = _plain_openai_client(gc).chat.completions.create
            create.return_value = _ok()
            p.chat(PING, max_tokens=5, temperature=0.2)
        kw = create.call_args.kwargs
        assert kw["temperature"] == 0.2
        assert kw["max_tokens"] == 5

    def test_single_retry_fixes_both_params(self):
        p = OpenAICompatSubPool(
            pool_name="X", endpoint="https://e", model="mystery", env_key="NOPE", api_key="k"
        )
        err = Exception(
            "Error code: 400 - Unsupported value: 'temperature' does not support 0.2 "
            "with this model. Only the default (1) value is supported."
        )
        with patch.object(p, "_get_client") as gc:
            create = _plain_openai_client(gc).chat.completions.create
            create.side_effect = [err, _ok()]
            p.chat(PING, max_tokens=5, temperature=0.2)
        assert create.call_count == 2
        kw = create.call_args.kwargs
        assert "temperature" not in kw
        assert kw["max_completion_tokens"] == 5

    def test_unrelated_error_is_raised(self):
        p = OpenAIPlugin(api_key="k", model="gpt-4o")
        with patch.object(p, "_get_client") as gc:
            _plain_openai_client(gc).chat.completions.create.side_effect = ValueError("boom")
            with pytest.raises(ValueError):
                p.chat(PING, temperature=0.2)


# ---------------------------------------------------------------------------
# Env keys / defaults
# ---------------------------------------------------------------------------
class TestEnvKeys:
    def test_minimax_upper_case_shim(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "upper")
        assert MiniMaxPlugin().api_key == "upper"
        monkeypatch.setenv("MiniMax_API_KEY", "historical")  # noqa: SIM112
        assert MiniMaxPlugin().api_key == "historical"

    def test_mimo_ignores_anthropic_auth_token(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "claude-code-token")
        assert MimoCodePlugin().api_key is None

    @pytest.mark.parametrize(
        ("factory", "env"),
        [
            (lambda: GeminiPlugin(api_key=""), "GOOGLE_API_KEY"),
            (lambda: NvidiaPlugin(api_key=""), "NVIDIA_API_KEY"),
            (lambda: AnthropicPlugin(api_key=""), "ANTHROPIC_API_KEY"),
            (lambda: MimoCodePlugin(api_key=""), "MIMO_AUTH_TOKEN"),
            (lambda: GLMPlugin(api_key=""), "ZHIPUAI_API_KEY"),
            (lambda: OpenRouterPlugin(api_key=""), "OPENROUTER_API_KEY"),
            (lambda: MiniMaxPlugin(api_key=""), "MiniMax_API_KEY"),
            (
                lambda: VolcanoSubPool(pool_name="x", model="m", capabilities=[], api_key=""),
                "BYTEDANCE_API_KEY",
            ),
        ],
    )
    def test_empty_string_key_means_no_key(self, monkeypatch, factory, env):
        monkeypatch.setenv(env, "from-env")
        p = factory()
        assert p.api_key == ""
        assert p.health_check()["ok"] is False


# ---------------------------------------------------------------------------
# NVIDIA
# ---------------------------------------------------------------------------
class TestNvidia:
    def test_default_model(self):
        assert NvidiaPlugin(api_key="k").model == nvidia_mod.DEFAULT_MODEL

    def test_health_requires_model(self):
        assert NvidiaPlugin(api_key="k").health_check()["ok"] is True
        assert NvidiaPlugin(api_key="k", model="").health_check()["ok"] is False

    def test_timeout_not_in_payload(self):
        fake = MagicMock()
        fake.json.return_value = {"choices": [{"message": {"content": "hi"}}]}
        with patch("providers.nvidia.requests.post", return_value=fake) as post:
            NvidiaPlugin(api_key="k").chat(PING, timeout=9)
        assert "timeout" not in post.call_args.kwargs["json"]
        assert post.call_args.kwargs["timeout"] == 9.0
        assert post.call_args.kwargs["json"]["model"] == nvidia_mod.DEFAULT_MODEL

    def test_null_content_empty_raises(self):
        fake = MagicMock()
        fake.json.return_value = {"choices": [{"message": {"content": None}}]}
        with (
            patch("providers.nvidia.requests.post", return_value=fake),
            pytest.raises(RuntimeError, match="empty response"),
        ):
            NvidiaPlugin(api_key="k").chat(PING)


# ---------------------------------------------------------------------------
# pools.yaml / openai_compat endpoints
# ---------------------------------------------------------------------------
class TestConfigEndpoints:
    @staticmethod
    def _pools() -> dict[str, dict[str, Any]]:
        data = yaml.safe_load((ROOT / "config" / "pools.yaml").read_text(encoding="utf-8"))
        return {p["name"]: p for p in data["pools"]}

    def test_nvidia_pool_has_model(self):
        assert self._pools()["NVIDIA-Cloud"]["model"] == nvidia_mod.DEFAULT_MODEL

    def test_cohere_and_hf_endpoints(self):
        assert (
            COMPAT_POOLS["Cohere-Command"]["endpoint"] == "https://api.cohere.ai/compatibility/v1"
        )
        assert (
            COMPAT_POOLS["HuggingFace-Inference"]["endpoint"] == "https://router.huggingface.co/v1"
        )
        pools = self._pools()
        for name in ("Cohere-Command", "HuggingFace-Inference"):
            if name in pools:
                assert pools[name]["endpoint"] == COMPAT_POOLS[name]["endpoint"]


# ---------------------------------------------------------------------------
# Anthropic-format conversion (claude + mimo)
# ---------------------------------------------------------------------------
class TestAnthropicFormat:
    def test_none_list_system_tool_and_empty_assistant(self):
        system, msgs = to_anthropic_messages(
            [
                {"role": "system", "content": [{"type": "text", "text": "rule A"}]},
                {"role": "system", "content": None},
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": None},
                {"role": "tool", "tool_call_id": "c1", "content": "42"},
                {"role": "assistant", "content": "a1"},
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "q2"}, {"type": "image_url"}],
                },
            ]
        )
        assert system == "rule A"
        assert msgs == [
            {"role": "user", "content": "q1\n\n[tool result c1]\n42"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]

    def test_assistant_tool_calls_kept_as_text(self):
        _, msgs = to_anthropic_messages(
            [
                {"role": "user", "content": "q"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": '{"x":1}'}}],
                },
            ]
        )
        assert msgs[1] == {"role": "assistant", "content": '[tool_call c1 f({"x":1})]'}

    def test_claude_client_timeout(self):
        with patch("providers.claude.Anthropic") as ctor:
            AnthropicPlugin(api_key="k")._get_client()
        assert ctor.call_args.kwargs["timeout"] == 60.0
        assert ctor.call_args.kwargs["max_retries"] == 0

    def test_claude_empty_response_raises(self):
        p = AnthropicPlugin(api_key="k")
        resp = MagicMock(content=[], stop_reason="end_turn", model="m", usage=None)
        with patch.object(p, "_get_client") as gc:
            _plain_anthropic_client(gc).messages.create.return_value = resp
            with pytest.raises(RuntimeError, match="empty response"):
                p.chat(PING)

    def test_claude_tool_use_only_does_not_raise(self):
        p = AnthropicPlugin(api_key="k")
        block = MagicMock(spec=["type", "id", "name", "input"], type="tool_use")
        resp = MagicMock(content=[block], stop_reason="tool_use", model="m", usage=None)
        with patch.object(p, "_get_client") as gc:
            _plain_anthropic_client(gc).messages.create.return_value = resp
            out = p.chat(PING)
        assert out["choices"][0]["message"]["content"] == ""

    def test_mimo_timeout_popped_before_payload(self):
        fake = MagicMock()
        fake.json.return_value = {"content": [{"type": "text", "text": "hi"}], "usage": {}}
        with patch("providers.mimo.requests.post", return_value=fake) as post:
            MimoCodePlugin(api_key="k").chat(PING, timeout=5)
        assert "timeout" not in post.call_args.kwargs["json"]
        assert post.call_args.kwargs["timeout"] == 5.0

    def test_mimo_thinking_only_raises(self):
        fake = MagicMock()
        fake.json.return_value = {
            "content": [{"type": "thinking", "thinking": "…"}],
            "stop_reason": "max_tokens",
        }
        with (
            patch("providers.mimo.requests.post", return_value=fake),
            pytest.raises(RuntimeError, match="empty response"),
        ):
            MimoCodePlugin(api_key="k").chat(PING)


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
class TestGemini:
    def test_full_history_converted(self):
        system, contents = to_gemini_contents(
            [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "again"},
            ]
        )
        assert system == "be brief"
        assert contents == [
            {"role": "user", "parts": [{"text": "hi"}]},
            {"role": "model", "parts": [{"text": "hello"}]},
            {"role": "user", "parts": [{"text": "again"}]},
        ]

    def test_trailing_assistant_gets_continue_turn(self):
        _, contents = to_gemini_contents(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "partial"}]
        )
        assert contents[-2]["role"] == "model"
        assert contents[-1] == {"role": "user", "parts": [{"text": CONTINUE_PROMPT}]}

    def _run(self, plugin: GeminiPlugin, response: Any, messages=PING):
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = response
        with (
            patch("providers.gemini.genai") as genai_mod,
            patch("providers.gemini.genai_types") as types_mod,
        ):
            genai_mod.Client.return_value = fake_client
            out = plugin.chat(messages)
        return out, fake_client, genai_mod, types_mod

    def test_chat_sends_all_contents_and_counts_thoughts(self):
        meta = MagicMock(
            prompt_token_count=4,
            candidates_token_count=2,
            thoughts_token_count=10,
            total_token_count=16,
        )
        out, client, _, _ = self._run(
            GeminiPlugin(api_key="k"),
            MagicMock(text="pong", usage_metadata=meta),
            [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
        )
        sent = client.models.generate_content.call_args.kwargs["contents"]
        assert [c["role"] for c in sent] == ["user", "model", "user"]
        assert out["usage"] == {"prompt_tokens": 4, "completion_tokens": 12, "total_tokens": 16}

    def test_custom_endpoint_used_as_base_url(self):
        _, _, genai_mod, types_mod = self._run(
            GeminiPlugin(api_key="k", endpoint="https://proxy.example"), MagicMock(text="x")
        )
        opts = types_mod.HttpOptions.call_args.kwargs
        assert opts["base_url"] == "https://proxy.example"
        assert opts["timeout"] == 60_000

    def test_default_endpoint_not_overridden(self):
        _, _, _, types_mod = self._run(GeminiPlugin(api_key="k"), MagicMock(text="x"))
        assert "base_url" not in types_mod.HttpOptions.call_args.kwargs

    def test_empty_text_raises(self):
        with pytest.raises(RuntimeError, match="empty response"):
            self._run(GeminiPlugin(api_key="k"), MagicMock(text=None))


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
class TestOllama:
    def _post(self, plugin: OllamaPlugin, **kwargs: Any):
        fake = MagicMock()
        fake.json.return_value = {
            "model": "foo:bar",
            "message": {"role": "assistant", "content": "ok"},
            "prompt_eval_count": 7,
            "eval_count": 3,
        }
        with patch("providers.ollama.requests.post", return_value=fake) as post:
            out = plugin.chat(PING, **kwargs)
        return out, post

    def test_kwargs_model_cannot_reintroduce_prefix(self):
        _, post = self._post(OllamaPlugin(model="foo:bar"), model="ollama/baz:1")
        assert post.call_args.kwargs["json"]["model"] == "baz:1"

    def test_sampling_kwargs_go_to_options(self):
        _, post = self._post(OllamaPlugin(), max_tokens=12, temperature=0.3, timeout=4)
        payload = post.call_args.kwargs["json"]
        assert payload["options"] == {"num_predict": 12, "temperature": 0.3}
        assert "max_tokens" not in payload and "temperature" not in payload
        assert "timeout" not in payload
        assert payload["stream"] is False
        assert post.call_args.kwargs["timeout"] == 4.0

    def test_usage_from_eval_counts(self):
        out, _ = self._post(OllamaPlugin())
        assert out["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
