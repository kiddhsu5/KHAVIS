# v0.1.0 (2026-09-20) — Initial Release

The first public release of `khavis`. 84 files, ~14,200 lines of code,
148 green unit tests, 12 working LLM pools (8 always-on + 4 BYOK), and one
OpenAI-compatible HTTP API that unifies them all.

> **One endpoint. Twelve pools. Zero quota interruption.**

---

## Highlights

- **Pluggable architecture** — drop a Python file into `providers/` and the
  registry picks it up. No central registration code, no DI container, no
  YAML schema to learn.
- **Capability-based smart routing** — `prefer` / `fallback` / `forbid` /
  `require` semantics with per-capability cost caps and silent failover on
  `429` / `503` / timeout.
- **Hot reload** — edit `capabilities.yaml`, save the file, and the running
  daemon picks up the change. No restart, no re-auth, no call-site touch.
- **Multi-agent orchestration** — six LangGraph node types compose into
  Pipeline, DAG, and Debate primitives with per-step retry and cost budgets.
- **Three-tier memory** — Working, Episodic (SQLite), Semantic (Chroma /
  Qdrant / pgvector). Per-request opt-in.
- **Append-only attribution log** — every call is logged with cost, latency,
  capability, and outcome for forensics and billing.
- **Multi-arch Docker** — `linux/amd64` and `linux/arm64` images, published
  to GHCR on every tag.
- **Apache 2.0** — use commercially, fork freely, ship anywhere.

---

## New Features

### Core

- `PluginRegistry` with file-based auto-discovery of `providers/*.py`
- `CapabilityRouter` with `prefer` / `fallback` / `forbid` / `require`
  filters, per-capability `max_cost_usd` caps, and `retry_on` classifiers
- `watchdog`-backed `HotReload` observer for `capabilities.yaml`,
  `pools.yaml`, and individual plugin files
- YAML-first configuration (no database required)
- Append-only JSONL audit log with daily rotation and `attribution` module
- `Router.from_yaml(...)` entry point and `Router.chat(...)` /
  `Router.stream(...)` public API

### Providers (12 pools)

| Pool ID | Provider | Capability tags | BYOK? |
| --- | --- | --- | --- |
| `MiniMax-M3` | MiniMax | `general`, `code` | No |
| `glm53` | Zhipu GLM-5.3 | `general`, `code`, `long-context` | No |
| `gemini-flash` | Google Gemini Flash | `cheap`, `vision`, `long-context` | No |
| `gemini-pro` | Google Gemini Pro | `reasoning`, `vision`, `long-context` | No |
| `nvidia-cloud` | NVIDIA Cloud (NIM) | `code`, `reasoning` | **Yes** (paid) |
| `volcano-ark-deepseek` | ByteDance Volcano Ark / DeepSeek | `code`, `reasoning` | No |
| `volcano-ark-qwen` | ByteDance Volcano Ark / Qwen | `general`, `code` | No |
| `volcano-ark-doubao` | ByteDance Volcano Ark / Doubao | `general`, `vision` | No |
| `openrouter-free` | OpenRouter Free tier | `cheap`, `general` | No |
| `openai-api` | OpenAI Platform API | `code`, `reasoning`, `tools`, `speed` | **Yes** |
| `claude-api` | Anthropic Claude API | `reasoning`, `tools`, `long-context` | **Yes** |
| `ollama-local` | Ollama (local) | `local`, `private`, `general` | No |

Every plugin ships with a unit test that exercises the `chat()` happy
path, the streaming path, and the error-classification path. BYOK plugins
are enabled in code by default — they are silently skipped by the
integration test runner when their env var is unset. See the README
**BYOK — Bring Your Own Keys** section for the full breakdown.

### Multi-Agent System

- **Node types**: `Planner`, two `Coder` variants, `Debate`, `Critic`,
  `Verifier`, `Learn`
- **Composition primitives**: `Pipeline` (sequential), `DAG` (arbitrary
  graphs), `Debate` (N agents in parallel, judge picks the best)
- **Per-step retry** independent of the parent call
- **Per-pipeline cost budget** enforcement
- **State machine** in `agents/graph.py` with typed `AgentState`

### Memory & Attribution

- **Working memory** — in-process, lives for one `Pipeline.run()` lifetime
- **Episodic memory** — SQLite-backed, indexed by `session_id`
- **Semantic memory** — pluggable; Chroma / Qdrant / pgvector adapters
- **Attribution log** — JSONL, daily-rotated, fields include `timestamp`,
  `pool`, `capability`, `cost_usd`, `latency_ms`, `status`,
  `session_id_hash`
- Per-request opt-in via the `memory={...}` kwarg

### HTTP API

- `POST /v1/chat` — OpenAI-compatible chat completions
- `POST /v1/chat/stream` — server-sent-events streaming
- `GET /v1/pools` — list pools with live health / quota status
- `GET /healthz` — liveness probe
- `GET /readyz` — readiness probe (503 until at least one pool is healthy)

### CLI

- `khavis init` — generate starter config files
- `khavis serve` — run the HTTP daemon
- `khavis chat` — one-shot chat from the terminal
- `khavis doctor` — validate config and connectivity
- `khavis reload` — manually trigger hot reload
- `khavis agent run <name>` — run a named agent pipeline

### DevOps

- **GitHub Actions** — five workflows (test, lint, release, docker,
  integration) plus Dependabot
- **Multi-arch Docker** — `linux/amd64` and `linux/arm64` via `buildx`
- **Multi-OS CI** — Python 3.11 and 3.12 on Ubuntu and macOS
- **PyPI publishing** — automatic on `v*` tag via `release.yml`
- **GHCR publishing** — automatic on `v*` tag via `docker.yml`
- **Nightly integration** — live-provider smoke against the cheapest
  pool of each capability

### Documentation

- `README.md` (English) and `README.zh-TW.md` (Traditional Chinese)
- `INSTALLATION.md` — pip / Docker / source install paths
- `CONTRIBUTING.md` — code style, PR process, provider PR template
- `CHANGELOG.md` — Keep-a-Changelog format
- `docs/ARCHITECTURE.md` — six-layer deep dive with sequence diagram
- `docs/PLUGIN_DEVELOPMENT.md` — Hello World plugin walkthrough
- `docs/CONFIGURATION.md` — every YAML knob and env var
- `docs/FAQ.md` — 15+ common questions
- `docs/COMMITS.md` — Conventional Commits guide for contributors
- `LICENSE` (Apache 2.0)

### Marketing

- `BUSINESS_PLAN.md` — full go-to-market plan
- `blog/launch-post.md` — long-form launch announcement
- `blog/show-hn-post.md` — Show HN submission (under 280 words)
- `blog/dev-to-post.md` — DEV.to cross-post
- `blog/twitter-thread.md` — 9-tweet launch thread
- `blog/zhihu-post.md` — Zhihu long-form post (Simplified Chinese)
- `blog/code-examples.md` — copy-pasteable examples for blog readers
- `blog/launch-checklist.md` — pre-launch QA checklist

---

## Testing

| Suite | Count | Status |
| --- | --- | --- |
| Core (registry, capability router, hot reload) | 41 | passing |
| Providers (unit) | 54 | passing |
| Agents (graph) | 31 | passing |
| Hot reload | 11 | passing |
| LangGraph optional dep test | 1 | skipped |
| Plugin discovery smoke (`scripts/test_plugins.py`) | 12 pools enumerated | passing |
| **Total** | **148 pass + 1 skip** | green |

```bash
pytest -q
python scripts/test_plugins.py
ruff check .
ruff format --check .
mypy core providers
```

---

## Installation

### pip

```bash
python -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install khavis
```

### Docker

```bash
docker run -d \
  --name khavis \
  -p 8080:8080 \
  -v $(pwd)/config:/app/config \
  ghcr.io/kiddhsu5/khavis:0.1.0
```

### From source

```bash
git clone https://github.com/kiddhsu5/KHAVIS.git
cd KHAVIS
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Full instructions: see [`INSTALLATION.md`](../INSTALLATION.md).

---

## Known Issues / Limitations

We document these honestly because a release notes page that hides
limitations does not serve users. None of these block production use of
the v0.1.0 surface area.

- **Web UI not shipped.** Pool health and quota dashboards live behind
  `GET /v1/pools` for now. The web UI is planned for `v0.6.0`.
- **No fine-tuned model registry yet.** First-class hot-swappable fine-tune
  tracking ships in `v0.5.0`. Until then, fine-tunes can be referenced
  by name in `pools.yaml` and the router will route to them like any
  other pool.
- **OpenTelemetry exporter is optional.** It ships as the
  `khavis[otel]` extra and is not bundled into the default install
  to keep cold-start small.
- **Plugin API marked "stable for v0.x".** We commit to not breaking
  `ProviderPlugin` within the v0.x series, but routing semantics may
  evolve. Anything marked "experimental" in the docs may change without
  a SemVer major bump until `v1.0.0`.
- **LangGraph is an optional dep.** The multi-agent system requires
  `pip install khavis[agents]`. Without it, `Router.chat()` and the
  HTTP API still work fully; only the `agents/` subpackage and its
  tests are skipped (1 skipped test).
- **CI integration tests require secrets.** Nightly live-provider smoke
  runs only on the `integration.yml` workflow and uses repository
  secrets. Public PRs do not exercise it.
- **Single-region routing.** Cross-region failover (e.g., routing
  around an entire provider outage that spans regions) is not yet
  implemented; planned for `v0.4.0`.

---

## Credits

Built by solo maintainer **Your Name** during Q3 2026.

Contributions welcome — see [`CONTRIBUTING.md`](../CONTRIBUTING.md) for
the PR process, code style, and provider plugin template.

`khavis` is released under the **Apache License 2.0**. It stands on
the shoulders of:

- [LiteLLM](https://github.com/BerriAI/litellm) — normalising provider APIs
- [OpenRouter](https://openrouter.ai) — proving the routing concept
- [LangGraph](https://github.com/langchain-ai/langgraph) — multi-agent primitives
- [Pydantic](https://docs.pydantic.dev/) — typed configuration that feels good
- [watchdog](https://github.com/gorakhargosh/watchdog) — filesystem events
- Every provider whose API we adapt to

---

## What's Next — v0.2.0 Preview

Targeted for late Q4 2026. Scope is intentionally narrow to keep the
release honest.

- **Token-aware cost budgeting** — per-request AND per-session caps,
  enforced before the LLM call dispatches, not after.
- **Streaming cost preview** — clients see `cost_usd_so_far` on every
  SSE chunk so they can `cancel` mid-stream.
- **Routing weights** — `prefer` becomes an ordered list with optional
  per-entry weights instead of strict ordering.
- **Pluggable health checks** — replace the fixed `healthz` ping with a
  provider-supplied `health_check()` hook so providers can declare
  richer readiness semantics.
- **More pools** — Mistral, Anthropic Claude (subscription tier), Cohere,
  and at least one more Chinese provider (TBD with the community).
- **First-party LangChain integration** — `khavis.ChatModel` as a
  drop-in `BaseChatModel` subclass.

Have an idea? Open an issue or vote on the
[discussion board](https://github.com/kiddhsu5/KHAVIS/discussions).

---

[v0.1.0]: https://github.com/kiddhsu5/KHAVIS/releases/tag/v0.1.0
[Unreleased]: https://github.com/kiddhsu5/KHAVIS/compare/v0.1.0...HEAD
