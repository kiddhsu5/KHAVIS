"""Unit tests for local Ollama fallback when all cloud API pools are exhausted.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.failover import (
    call_with_failover,
    find_local_fallback_pool,
)


def _make_plugin(name: str, provider_id: str = "cloud", fail: bool = False, reply: str = "ok"):
    p = MagicMock()
    p.name = name
    p.provider_id = provider_id
    p.capabilities = ["local"] if provider_id == "ollama" else ["code"]
    p.has_capability = lambda c: c in p.capabilities
    if fail:
        p.chat.side_effect = RuntimeError(f"429 rate limit exceeded for {name}")
    else:
        p.chat.return_value = {
            "choices": [{"message": {"role": "assistant", "content": reply}}],
            "model": f"model-{name}",
        }
    return p


class TestLocalFallbackDiscovery:
    def test_finds_ollama_mac_first(self):
        cloud = _make_plugin("Cloud-GPT", provider_id="openai")
        ollama_surface = _make_plugin("Ollama-Surface", provider_id="ollama")
        ollama_mac = _make_plugin("Ollama-Mac", provider_id="ollama")

        router = MagicMock()
        router.registry = [cloud, ollama_surface, ollama_mac]

        found = find_local_fallback_pool(router)
        assert found is not None
        assert found.name == "Ollama-Mac"

    def test_respects_exclude_list(self):
        ollama_mac = _make_plugin("Ollama-Mac", provider_id="ollama")
        ollama_surface = _make_plugin("Ollama-Surface", provider_id="ollama")

        router = MagicMock()
        router.registry = [ollama_mac, ollama_surface]

        found = find_local_fallback_pool(router, exclude=["Ollama-Mac"])
        assert found is not None
        assert found.name == "Ollama-Surface"


class TestLocalFallbackExecution:
    def test_cloud_pools_exhausted_degrades_to_local_ollama(self):
        cloud_a = _make_plugin("Cloud-A", provider_id="openai", fail=True)
        cloud_b = _make_plugin("Cloud-B", provider_id="anthropic", fail=True)
        local_mac = _make_plugin(
            "Ollama-Mac",
            provider_id="ollama",
            fail=False,
            reply="Local Ollama Mac response",
        )

        router = MagicMock()
        # Router selects cloud_a first, then cloud_b on retry
        router.select.side_effect = [cloud_a, cloud_b, None]
        router.registry = [cloud_a, cloud_b, local_mac]

        msgs = [{"role": "user", "content": "Help me write code"}]

        used, result, attempts = call_with_failover(
            router,
            capability="code",
            messages=msgs,
            max_attempts=3,
            auto_escalate_agents=False,  # Test purely local degradation path
            local_fallback=True,
        )

        assert used.name == "Ollama-Mac"
        assert result["choices"][0]["message"]["content"] == "Local Ollama Mac response"
        assert result["khavis"]["degraded_to_local"] is True
        assert "all_cloud_pools_exhausted" in result["khavis"]["fallback_reason"]

    def test_local_fallback_disabled_raises_error(self):
        cloud = _make_plugin("Cloud-A", provider_id="openai", fail=True)
        local_mac = _make_plugin("Ollama-Mac", provider_id="ollama", fail=False)

        router = MagicMock()
        router.select.return_value = cloud
        router.registry = [cloud, local_mac]

        msgs = [{"role": "user", "content": "Help me write code"}]

        with pytest.raises(RuntimeError) as exc:
            call_with_failover(
                router,
                capability="code",
                messages=msgs,
                max_attempts=1,
                auto_escalate_agents=False,
                local_fallback=False,  # Explicitly opt out
            )
        assert "429" in str(exc.value)
