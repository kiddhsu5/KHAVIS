# KHAVIS

> One workspace where an AI team ships together.

KHAVIS integrates multiple CLI agents with a self-hostable LLM router. Share a project and let agents接力 in relay, or isolate parallel work with Git Worktrees — then inspect diffs and checks before adopting any result. Provider availability, quotas, and tooling always depend on your own deployment.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org)
[![Status](https://img.shields.io/badge/status-v0.5.0-emerald.svg)](#roadmap)
[![Project site](https://img.shields.io/badge/Project_Site-khavis.khorus.tech-0EA5E9)](https://khavis.khorus.tech)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[繁體中文 README](README.zh-TW.md) · Project site: [khavis.khorus.tech](https://khavis.khorus.tech) · Workbench: [khavis.khorus.tech/workbench](https://khavis.khorus.tech/workbench)

## The workbench: from a task to a verifiable deliverable

The conversation stream stays central. Agents, providers, and model settings live on the dedicated `/agents` page; the document library lives at `/database`. Model IDs and thinking modes come from connected providers' own catalogues — KHAVIS never invents capabilities it cannot verify.

The library archives outputs by conversation topic, keeps the producing task on record, and offers format filters, previews, and downloads. Classification uses local rules with restricted content samples — no paid model calls, no full-text or vector knowledge base.

Shared workspaces suit接力 work; Git Worktrees suit isolated parallel changes. An agent saying "done" is not a passed test and not a merge. KHAVIS is the workspace platform; **K.A.V.I.S.** is the optional built-in agent layer.

**Start here:** [Quick start](#quick-start). To check the control flow without any keys, use the clearly-labelled offline mock below — it verifies routing logic, not real providers or CLIs.

## Names in this project

- **KHAVIS** is the platform and this repository: provider registry, capability router, config files, CLI, optional OpenAI-compatible HTTP server, audit and budget components, and provider plugins.
- **K.A.V.I.S.** is the built-in agent name used by the optional multi-agent and deployment layers. Depending on installed extras and deployment, it can run the local LangGraph team or dispatch through the configured `deploy/kavis_adapter.py` bridge. It is not a provider and does not guarantee any remote service or fixed model.
- **External CLI tools** (Claude Code, Codex, Antigravity, MiniMax Code, OpenCode, Aider, or your own commands) are integration targets, not extra KHAVIS models. They must be installed, logged in, and configured separately.

Available pools are defined by config files. A plugin existing in the repo does not imply the provider, pool, model alias, quota, or external CLI is available. Check `config/pools.yaml`, environment variables, and `khavis doctor` before sending real requests.

## What KHAVIS provides

- Capability-based selection using tags and weights from `config/capabilities.yaml`;
- Provider plugin auto-discovery from `providers/`;
- YAML-configured pools, budgets, health, and agents;
- `khavis chat` for router usage without a daemon;
- `khavis serve` for an optional OpenAI-compatible HTTP API;
- Per-request budget and health tracking, plus HTTP audit logging;
- Optional multi-agent orchestration, Telegram integration, and deployment bridges.

Routing and failover are best-effort runtime behaviour. Providers can be unreachable, misconfigured, rate-limited, out of quota, or missing the requested model. KHAVIS reports those failures; it does not promise uninterrupted completion.

## One-click deployment (Windows / macOS / Linux)

The installer checks the environment (Python ≥ 3.11), clones the repo, builds a venv, installs dependencies, initialises `.env` (never overwritten), optionally registers a background service, and runs a health check.

```bash
# macOS / Linux (bash)
bash deploy/install.sh

# Include the K.A.V.I.S. remote dispatch bridge (Unix + hermes required)
bash deploy/install.sh --with-adapter

# Windows (PowerShell, from the repo root)
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\install.ps1
```

Use `--no-service` if you do not want a background service (macOS skips systemd automatically). Upgrades: `git -C <install-dir> pull` and restart. See [`deploy/README.md`](deploy/README.md).

## Quick start

```bash
git clone https://github.com/kiddhsu5/KHAVIS.git
cd KHAVIS
python3.11 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e .

# Writes config/pools.yaml, config/capabilities.yaml, config/agents.yaml.example.
# Existing files are preserved, never overwritten.
khavis init

# Inspect discovered pools and per-provider health.
khavis doctor
```

In a fresh BYOK setup, `khavis doctor` may list pools without keys or with unreachable endpoints — that is normal. Only fill `.env` and `config/pools.yaml` for providers you actually use.

### Optional extras

```bash
python -m pip install -e ".[server]"   # khavis serve / OpenAI-compatible HTTP API
python -m pip install -e ".[agents]"   # optional LangGraph multi-agent runtime
python -m pip install -e ".[bot]"      # optional Telegram bot components
python -m pip install -e ".[dev]"      # tests, lint, type checks, packaging tools
```

## Two very different kinds of test

### Offline mock smoke test — no keys, no network, no Ollama service

```bash
python3 examples/zero_quota_demo.py --json
```

A reproducible failure-flow drill using in-memory cloud, webhook, and Ollama-style stand-ins. It verifies local routing, quota exhaustion handling, alert capture, and local fallback control flow. It does **not** contact providers, test real Ollama, validate API keys, or prove any model is available. The JSON summary includes `external_network: false`, `api_keys_required: false`, and `ollama_service_required: false`.

### Reproducible relay proof (E2)

`examples/relay_quickstart/` proves the core workflow — relay → file output → git diff → human adopt — on a clean environment. The offline path needs no keys or network; the BYOK path runs the real LangGraph team and always stops at a human review gate.

### Real provider requests

```bash
# Edit .env and config/pools.yaml first.
khavis doctor                 # health / configuration checks per provider
khavis doctor --deep          # one real minimal request per ready pool
khavis chat "Write a quicksort in Python."
```

`doctor --deep` may consume provider quota; run it only when you intend to make real requests. A filled-in key, endpoint, or model name does not guarantee the provider will accept a request.

## Real Ollama setup

The Ollama provider is a real HTTP integration — completely different from the offline mock above:

1. Install and start Ollama on the machine hosting your models.
2. Pull the model you intend to use, e.g. `ollama pull <model-id>`.
3. Point the matching `config/pools.yaml` entry at that host's Ollama endpoint and an actually-installed model tag. For the parameterised Mac / Surface pools, set `MAC_IP` or `SURFACE_IP` in `.env` as needed.
4. Run `khavis doctor`, then `khavis doctor --deep` for a real minimal generation.
5. Call the pool by its configured name:

```bash
khavis chat --pool <configured-ollama-pool> "Summarise this into three points."
```

Model tags are deliberately placeholders: Ollama only offers what is installed on the selected host, and the repository does not guarantee any fixed model name.

## Using the CLI directly

```bash
khavis chat "Explain this error."
khavis chat --capability <tag-from-config> "Refactor this function."
khavis chat --pool <configured-pool-name> "Answer briefly."
khavis chat --json "Return a provider-shaped result."
khavis chat --quiet "No pool/model/token footer."
khavis chat --max-attempts 2 "Try at most two pools."
```

By default the CLI refreshes pool health first; after a provider failure it may try other configured candidates. That is bounded failover, not a guarantee. Use `--no-refresh` only if you understand a stale pool may be selected.

```bash
khavis health --refresh   # probe pools, show circuit / quota state
khavis health --reset     # reset this process's circuit state before probing
```

Circuit state belongs to the process that holds it; a separate `khavis serve` keeps its own tracker.

## OpenAI-compatible HTTP API

```bash
python -m pip install -e ".[server]"
khavis init
khavis serve --host 127.0.0.1 --port 8080
```

```bash
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Write a quicksort in Python."}]}'
```

Responses use OpenAI-style `choices` / `model` / `usage` and may include KHAVIS fields such as `pool`, `cost_usd`, `latency_ms`. With auditing enabled, HTTP requests append to `config/audit.jsonl`. Starting the server does not make upstream models available by itself.

## K.A.V.I.S. and optional multi-agent execution

```bash
python -m pip install -e ".[agents]"
```

```python
from agents.graph import run_team

state = run_team("Design a REST API for a todo app.", session_id="demo")
print(state.get("final"))
```

In deployment docs, **K.A.V.I.S.** means this built-in agent layer, not a model pool. The local graph and the remote bridge are two distinct execution paths. Read [`deploy/README.md`](deploy/README.md) before enabling the Hermes adapter: it binds localhost by default, may require an adapter token, and executes the configured Hermes binary — it is not an offline mock, so start it only in trusted environments.

For external coding CLIs, verify the executable and login state yourself (e.g. the tool's `--version` or login flow). KHAVIS does not ship or guarantee those tools, their subscriptions, or default model aliases.

## Provider plugins and pool configuration

The repo ships provider implementations for OpenAI-compatible, Anthropic, Google, and Ollama API styles. The registry discovers plugin classes from `providers/`; actual pool names, endpoints, model tags, credentials, and capability tags come from config files and environment variables.

Start with:

- [`config/pools.yaml`](config/pools.yaml): pool names, endpoints, models, `env_key`;
- [`config/capabilities.yaml`](config/capabilities.yaml): capability → pool preferences;
- [`config/budgets.yaml`](config/budgets.yaml): token and cost policy;
- [`config/health.yaml`](config/health.yaml): health-check and circuit policy;
- [`.env.example`](.env.example): environment variable template; never commit secrets.

Minimal capability-routing example:

```python
from pathlib import Path

from core.capability_router import CapabilityRouter
from core.registry import PluginRegistry

root = Path(".")
registry = PluginRegistry(root).discover()
registry.apply_pools_config(root / "config" / "pools.yaml")
router = CapabilityRouter(registry)
router.load_capabilities(root / "config" / "capabilities.yaml")

plugin = router.select("<capability-tag>")
if plugin is None:
    raise RuntimeError("No configured pool can serve this capability")
result = plugin.chat([{"role": "user", "content": "Refactor this function."}])
print(result["choices"][0]["message"]["content"])
```

Every plugin returns an OpenAI-style dictionary. The provider decides the response and the model identifier; treat `model` in a response as that request's actual value, not a long-term availability promise.

## Optional integrations

### Telegram bot

```bash
python -m pip install -e ".[bot]"
cp .env.example .env
# Configure BOT_TOKEN and ALLOWED_CHAT_IDS per docs/TELEGRAM_BOT.md.
python -m bot.main --mode polling
```

See [`docs/TELEGRAM_BOT.md`](docs/TELEGRAM_BOT.md) and the [`deploy/`](deploy/) directory for webhook deployment and operations.

### Docker and VM deployment

- [`INSTALLATION.md`](INSTALLATION.md): prerequisites, install paths, verification, troubleshooting;
- [`deploy/README.md`](deploy/README.md): Ubuntu VM / cloud deployment and services;
- [`deploy/docker-compose.yml`](deploy/docker-compose.yml): deployment compose file;
- [`deploy/kavis_adapter.py`](deploy/kavis_adapter.py): localhost-only OpenAI-compatible K.A.V.I.S. bridge.

Read the deployment docs and confirm your security settings before exposing anything to a network. API keys, adapter tokens, and Telegram tokens belong in local secrets, never in Git.

## Documentation map

- [`INSTALLATION.md`](INSTALLATION.md): install, verify, run, troubleshoot.
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md): YAML and environment settings.
- [`docs/EVIDENCE.md`](docs/EVIDENCE.md): feature claims and their evidence levels (E0–E3).
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): router, registry, orchestration, memory, audit.
- [`docs/HEALTH.md`](docs/HEALTH.md): health checks, circuit state, quota telemetry.
- [`docs/PLUGIN_API.md`](docs/PLUGIN_API.md): provider plugin interface.
- [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md): add a provider plugin.
- [`docs/TELEGRAM_BOT.md`](docs/TELEGRAM_BOT.md): optional bot configuration.
- [`deploy/README.md`](deploy/README.md): VM deployment and the K.A.V.I.S. adapter.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): contribution flow.
- [`CHANGELOG.md`](CHANGELOG.md): releases and changes.
- [`CONTRIBUTORS.md`](CONTRIBUTORS.md): the people behind the project.

This README deliberately describes config-driven capabilities and does not present any provider, model, site, quota, latency, or external CLI as universally available. Refer to the repository implementation and your own runtime.

## Plugin development

```python
from providers.base import ProviderPlugin


class MyProvider(ProviderPlugin):
    provider_id = "acme"

    def __init__(self, **kwargs):
        super().__init__(name="Acme-1", capabilities=["<capability-tag>"], **kwargs)

    def chat(self, messages, **kwargs) -> dict:
        return {"choices": [], "model": "<provider-response>", "usage": {}}
```

Register the pool in config, add provider-appropriate tests, and read [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md) before opening a PR.

## Roadmap

Project status: the badge above reflects this repository snapshot. For versions and feature state, trust [`CHANGELOG.md`](CHANGELOG.md), the implementation, and the tests. Optional dashboards, workbench paths, provider integrations, and deployment services may need extra configuration; this README does not claim they are enabled everywhere.

## Contributing

Bug reports, provider plugins, routing improvements, and docs are welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) first, keep secrets out of Git, and update [`CHANGELOG.md`](CHANGELOG.md) where appropriate. Contributors are credited in [`CONTRIBUTORS.md`](CONTRIBUTORS.md) — people only.

## Community and support

- GitHub Issues: bug reports and feature requests.
- GitHub Discussions: questions, experiments, routing recipes, integration feedback.
- Security reports: follow [`.github/SECURITY.md`](.github/SECURITY.md).

## License

Apache License 2.0. See [`LICENSE`](LICENSE).

## Acknowledgements

KHAVIS builds on the work of the [LiteLLM](https://github.com/BerriAI/litellm) and [OpenRouter](https://openrouter.ai) communities, on the providers it adapts, and on everyone improving the code and documentation.
