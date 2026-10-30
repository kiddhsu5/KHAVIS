# Plugin Development

Adding a new LLM provider to K.H.A.V.I.S. is intentionally a small job. A plugin is a single Python file with one class. The registry picks it up automatically; you do not touch any core code.

This guide walks you through writing one from scratch and shipping it. It documents the **current** (v1.0.0) contract — the same one pinned in [`PLUGIN_API.md`](PLUGIN_API.md).

## The `ProviderPlugin` interface

Every plugin subclasses `ProviderPlugin` and implements four abstract methods. All of them are **synchronous** and return plain dicts — there is no async layer and no response dataclasses.

```python
from providers.base import ProviderPlugin


class MyProvider(ProviderPlugin):
    name: str = "my-provider"  # unique pool name (registry key)
    provider_id: str = "acme"  # vendor id used in audit logs
    default_endpoint: str = "https://api.acme.ai"
    capabilities: list[str] = ["general"]  # tags the router can match

    def chat(self, messages: list[dict[str, str]], **kwargs) -> dict: ...
    def check_quota(self) -> dict: ...
    def list_models(self) -> list[str]: ...
    def health_check(self) -> dict: ...
```

### Method contracts

| Method | Returns | Notes |
|---|---|---|
| `chat(messages, **kwargs)` | `dict` | **Required.** Must be shaped like the OpenAI chat-completions response (see below). |
| `health_check()` | `dict` | `{"ok": bool, "detail": str}`. Keep it cheap — the registry and `khavis doctor` call it. |
| `list_models()` | `list[str]` | Discoverable model ids for `GET /v1/models`. |
| `check_quota()` | `dict` | Opaque quota snapshot; minimum keys `remaining`, `total`, `tier` (`None` when the upstream does not expose them). |

`chat()` return shape (what the router and audit log consume):

```python
{
    "choices": [{"message": {"role": "assistant", "content": "..."}}],
    "model": "acme-1-mini",
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},  # optional but recommended
}
```

> **Streaming**: `ProviderPlugin` has **no** `stream()` method today. Streaming is on the roadmap (see README §3 and `CHANGELOG.md`); until then every upstream call is a complete, non-streaming `chat()`.

---

## Hello World plugin

Let us build a minimal but real plugin — a wrapper around a hypothetical `AcmeAI` HTTP API.

### 1. Create the file

```bash
touch providers/acme.py
```

### 2. Write the plugin

```python
# providers/acme.py
"""AcmeAI provider plugin for K.H.A.V.I.S."""

from __future__ import annotations

from typing import Any

import requests

from .base import ProviderPlugin


class AcmePlugin(ProviderPlugin):
    """Talks to the AcmeAI /v1/chat/completions endpoint."""

    provider_id = "acme"

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name="Acme-1",
            endpoint=endpoint or "https://api.acme.ai",
            api_key=api_key,  # usually filled from pools.yaml env_key
            model=model or "acme-1-mini",
            capabilities=capabilities or ["general", "code"],
            metadata=metadata or {"tier": "standard"},
        )
        self._client = None

    def _get_client(self):
        # Lazy: do not touch the network at import/instantiation time.
        if self._client is None:
            self._client = requests.Session()
            self._client.headers["Authorization"] = f"Bearer {self.api_key or ''}"
        return self._client

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        model = kwargs.pop("model", self.model)
        resp = self._get_client().post(
            f"{self.default_endpoint}/v1/chat/completions",
            json={"model": model, "messages": messages, **kwargs},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()  # Acme already speaks the OpenAI shape

    def health_check(self) -> dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "detail": f"{self.name}: api key not set"}
        return {"ok": True, "detail": f"{self.name}: ready"}

    def list_models(self) -> list[str]:
        return ["acme-1-mini", "acme-1"]

    def check_quota(self) -> dict[str, Any]:
        return {"remaining": None, "total": None, "tier": "standard"}
```

Two habits worth copying from the in-repo plugins:

- **Lazy clients** — see `OpenRouterPlugin._get_client` in [`../providers/openrouter.py`](../providers/openrouter.py). Instantiation happens at discovery time and must not require credentials or network.
- **Normalization** — if your vendor returns a non-OpenAI shape (see [`../providers/claude.py`](../providers/claude.py)), translate it into the `choices`/`usage` dict above in a `_normalize()` helper so the rest of the stack stays provider-agnostic.

### 3. Declare the pool in `config/pools.yaml`

```yaml
pools:
  - name: "Acme-1"                # MUST match the plugin instance's ``name``
    provider_id: "acme"
    endpoint: "https://api.acme.ai"
    model: "acme-1-mini"
    env_key: "ACME_API_KEY"       # api_key is read from the environment
```

Supported keys: `name`, `provider_id`, `endpoint`, `model` (or `models` list), `env_key`, `region`/`tier`/`notes` (merged into `metadata`). `${VAR}` placeholders in `endpoint` are expanded from the environment. Unknown names are reported in `registry.errors()` and skipped.

### 4. Tag it in `config/capabilities.yaml`

```yaml
capabilities:
  - capability: "code"
    pools:
      - "MiniMax-M3"
      - "Acme-1"
    weight: 1.0
```

### 5. Reload and test

Edit YAML and the watchdog hot-reloader picks it up (restart not needed for config changes; a **new plugin file** needs a process restart to import). Then smoke-test the pool:

```bash
PYTHONPATH=. python3 scripts/test_plugins.py        # loads + validates every pool
khavis doctor                                        # per-pool health snapshot
```

That is the whole loop.

---

## Capability tag conventions

The router matches a single request capability string against `plugin.capabilities` (a `list[str]`, matched with plain equality). Tags in use today live in `config/capabilities.yaml` and `providers/*.py` — e.g. `中文`, `英文`, `程式碼`, `推理`, `速度優先`, `工具調用`, `local`, `cheap`.

You can invent your own tags. The router treats them as opaque strings — no central registry needed. Keep them consistent between `providers/*.py` `capabilities` lists and the `capability:` keys in `config/capabilities.yaml` (English or Chinese both work; match what your deployment already uses).

---

## Testing your plugin

Tests live under `tests/` as `test_<topic>.py` (there is no `tests/providers/` subtree). The established pattern is `unittest.mock` — patch your lazy client, never hit the live network:

```python
# tests/test_acme_plugin.py
from unittest.mock import MagicMock, patch

from providers.acme import AcmePlugin


def test_defaults(monkeypatch):
    monkeypatch.delenv("ACME_API_KEY", raising=False)
    p = AcmePlugin()
    assert p.name == "Acme-1"
    assert p.provider_id == "acme"


def test_chat_normalises_response():
    p = AcmePlugin(api_key="sk-test")
    fake = MagicMock()
    fake.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "pong"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    with patch.object(p, "_get_client") as gc:
        gc.return_value.post.return_value = fake
        out = p.chat([{"role": "user", "content": "ping"}])
    assert out["choices"][0]["message"]["content"] == "pong"
    assert out["usage"]["prompt_tokens"] == 1
```

Conventions (mirroring `tests/test_providers_unit.py`):

- One test class per plugin; cover at minimum: defaults, env-key loading, happy-path `chat()` normalization, an empty/missing `choices` case, `health_check()` without a key.
- Mock the SDK/client with `unittest.mock` — no live network calls in tests.
- Run with `pytest tests/test_providers_unit.py -q` (or the whole suite: `pytest -q`).

---

## Reference plugins in the repo

The shipped `providers/` directory is the best place to copy-paste from. Highlights:

- [`providers/openrouter.py`](../providers/openrouter.py) — minimal OpenAI-compatible plugin. The cleanest template for any provider that exposes `/v1/chat/completions`.
- [`providers/openai.py`](../providers/openai.py) — OpenAI Platform API plugin using the official `openai` SDK.
- [`providers/claude.py`](../providers/claude.py) — Anthropic-native example. Shows how to:
  1. Split `system` messages out of the chat history (Anthropic takes a single `system=` kwarg, not a message role).
  2. Translate Anthropic `content` blocks back into the OpenAI-style `choices[]` dict so the router and audit log stay provider-agnostic.
- [`providers/volcano.py`](../providers/volcano.py) — factory pattern: one file declares three sub-pools (DeepSeek-V4-Pro, DeepSeek-V4.1-Flash, GLM-5.3-Flash) sharing a single API key. See "Discovery modes" below.
- [`providers/ollama.py`](../providers/ollama.py) — `_plugin_instances` multi-instance pattern (Ollama-Mac + Ollama-Surface) and `${VAR}` endpoint expansion for LAN hosts.

---

## Discovery modes

`core.registry.PluginRegistry.discover()` scans `providers/` and accepts three shapes (see [`PLUGIN_API.md`](PLUGIN_API.md) §2):

1. **Plain class** — subclass of `ProviderPlugin`. Instantiated with `cls()` at discovery, so the constructor must work with zero arguments (default name/endpoint/model; `pools.yaml` then overrides).
2. **Multi-instance class** — set a `_plugin_instances: list[dict]` class attribute; each dict is passed to the constructor, producing one pool per entry.
3. **Factory callable** — a module-level callable with a `provider_id` attribute, returning `list[ProviderPlugin]`. Set `_skip_auto_instantiate = True` on any class only the factory should build.

---

## Submitting to the registry

If your plugin is generally useful, please open a PR against the upstream `providers/` directory.

The PR template asks for:

- [ ] Plugin file under `providers/` (no subdirectory required).
- [ ] Tests in `tests/` following the mocking conventions above.
- [ ] Entry in `config/pools.yaml` (or a commented example in your PR description).
- [ ] Entry in `docs/CONFIGURATION.md` provider table.
- [ ] `CHANGELOG.md` updated under "Unreleased".
- [ ] No hardcoded secrets — auth must be read from env or `pools.yaml` `env_key`.
- [ ] License header matching the project (Apache 2.0).

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the full PR checklist.

---

## Reference: full signatures

```python
class ProviderPlugin(ABC):
    name: str
    provider_id: str
    default_endpoint: str
    model: str | None
    api_key: str | None
    capabilities: list[str]
    metadata: dict[str, Any]

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]: ...
    def health_check(self) -> dict[str, Any]: ...
    def list_models(self) -> list[str]: ...
    def check_quota(self) -> dict[str, Any]: ...

    # provided for you:
    def describes(self) -> dict[str, Any]: ...
    def has_capability(self, tag: str) -> bool: ...
```

`messages` follows the OpenAI chat-completions shape (`{"role": ..., "content": ...}`). `**kwargs` carries per-request overrides (`temperature`, `max_tokens`, `model`, …) — your plugin decides which to honor.

Happy hacking.
