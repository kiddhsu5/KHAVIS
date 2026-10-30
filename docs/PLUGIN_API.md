# Plugin API stability (v1.0.0)

> **This document is the compatibility contract.** From v1.0.0, changes
> to the interfaces below follow SemVer: breaking changes require a
> major version bump. Additive fields are minor.

## 1. `providers.base.ProviderPlugin`

Stable abstract members:

| Member | Contract |
| --- | --- |
| `name: str` | Unique pool name (registry key) |
| `provider_id: str` | Vendor id used in audit logs |
| `default_endpoint: str` | API base URL |
| `model: str` | Default model id |
| `api_key: str \| None` | Credential (never logged) |
| `capabilities: list[str]` | Tags used by `CapabilityRouter` |
| `chat(messages, **kwargs) -> dict` | Returns OpenAI-style `{"choices", "model", "usage"}` |
| `health_check() -> dict` | `{"ok": bool, "detail": str}` |
| `list_models() -> list[str]` | Discoverable model ids |
| `check_quota() -> dict` | Opaque quota snapshot |

Private helpers (`_normalize`, `_get_client`, …) are **not** public API.

## 2. Discovery

Drop a module in `providers/`. The registry auto-discovers:

1. Subclasses of `ProviderPlugin` (set `_skip_auto_instantiate = True` if
   the class needs constructor args).
2. Callables with a `provider_id` attribute (factories). Calling the
   factory must return `list[ProviderPlugin]`.
3. Optional `_plugin_instances: list[dict]` on a plugin class for
   multi-instance pools.

## 3. Config surface

| File | Stable keys |
| --- | --- |
| `config/pools.yaml` | `name`, `provider_id`, `endpoint`, `model`/`models`, `env_key` |
| `config/capabilities.yaml` | `capability`, `pools`, `weight` |
| `config/budgets.yaml` | `policy.*`, `pricing_usd_per_1k` |
| `config/models.yaml` | `overrides[].{pool,model,notes}`, `custom_models[]` |
| `config/rag.yaml` | `backend`, per-backend options |

## 4. HTTP surfaces

| Endpoint | Stability |
| --- | --- |
| `GET /healthz` | stable (status page) |
| `GET /dashboard`, `/wall`, `/api/dashboard` | stable, may add fields |
| `POST /v1/chat/completions`, `GET /v1/models` | **OpenAI-shaped**, stable |

## 5. What we will *not* break without a major bump

- `ProviderPlugin` abstract signatures
- YAML keys listed above (unknown keys are ignored)
- OpenAI Chat Completions response shape
- Env var names documented in `.env.example`

## 6. Versioning

- `pyproject.toml` `version` is the product version
- LTS: the `main` branch at each minor release; security fixes backported
  for 12 months on request
