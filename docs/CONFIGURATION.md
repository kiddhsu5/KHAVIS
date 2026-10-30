# Configuration Reference

K.H.A.V.I.S. reads three categories of configuration:

1. **YAML files** in `config/` — declarative routing and pool definitions.
2. **Environment variables** (and `.env`) — secrets and runtime overrides.
3. **Command-line flags** — boot-time toggles (`khavis serve --host/--port`, …).

This document describes the format the code actually parses. The on-disk files
are the source of truth; run `khavis doctor` after any change.

---

## `config/capabilities.yaml`

Maps a capability tag to the pools that serve it. The router does
**weighted-random** selection over the listed pools.

```yaml
capabilities:
  - capability: <tag>          # free-form string; match plugin capability tags
    pools:                     # pool names from config/pools.yaml
      - <pool-name>
      - <pool-name>
    weight: <float>            # relative share for weighted-random selection
```

### Example

```yaml
capabilities:
  - capability: "程式碼"
    pools:
      - "GLM-5.3"
      - "DeepSeek-V4-Pro"
      - "NVIDIA-Cloud"
      - "OpenAI-API"
    weight: 1.2

  - capability: "速度優先"
    pools:
      - "Google-Gemini"
      - "DeepSeek-V4.1-Flash"
      - "OpenRouter-Free"
      - "Ollama-Mac"
    weight: 1.0
```

Notes:

- The tags in the shipped config are `中文`, `英文`, `程式碼`, `推理`, `工具調用`,
  `Embedding`, `長文`, `速度優先`, `辯論`, `審查`, `驗證` — but the tag vocabulary
  is yours; inventing a new tag just works.
- `weight` is shared by every pool in the entry (default `1.0`).
- If a tag has no explicit entry, the router falls back to pools whose plugin
  `capabilities` list advertises the tag (`registry.by_capability`).
- Selection strategies: `select(cap, strategy="weighted"|"round_robin"|"random")`.

---

## `config/pools.yaml`

Declares each pool and its connection settings. Adding a pool normally means
dropping a plugin into `providers/` and adding one entry here — no core code
changes.

```yaml
pools:
  - name: <pool-name>          # unique; must match the plugin instance's ``name``
    provider_id: <id>          # the plugin's provider id (module in providers/)
    endpoint: <url>            # "${VAR}" placeholders expand from the environment
    model: <model-id>          # or: models: [<id>, ...] for multi-model pools
    env_key: <ENV_VAR>         # api key is read from this env var (null = none)
    region: <str>              # optional; merged into plugin metadata
    tier: <str>                # optional; merged into plugin metadata
    notes: <str>               # optional; merged into plugin metadata
```

### Example

```yaml
pools:
  - name: "MiniMax-M3"
    provider_id: "MiniMax"
    endpoint: "https://api.minimax.cn/v1"
    model: "MiniMax-M3"
    env_key: "MINIMAX_API_KEY"

  - name: "Google-Gemini"
    provider_id: "gemini"
    endpoint: "https://generativelanguage.googleapis.com"
    models: ["gemini-flash-latest", "gemini-pro-latest"]
    env_key: "GOOGLE_API_KEY"

  - name: "Ollama-Surface"
    provider_id: "ollama"
    endpoint: "http://${SURFACE_IP}:11434"
    model: "qwen2.5:1.5b"
    env_key: null
```

Unknown `name:` values (no matching discovered plugin) are collected in
`registry.errors()` and skipped.

---

## `config/models.yaml`

Model overrides and fine-tune registrations (v0.5.0). Applied by
`PluginRegistry.apply_model_overrides()` (idempotent — safe to re-apply).
Not watched by hot reload: `scripts/start.sh` only watches `pools.yaml`
and `capabilities.yaml`, so restart after editing this file.

```yaml
overrides:
  # Point a pool at a different / self-hosted model id:
  Groq-Llama: "my-org/my-finetune-v1"
```

---

## `config/budgets.yaml`

Soft token/cost caps enforced at the router edge (v0.2.0):

```yaml
policy:
  max_tokens_per_request: 32000        # pre-flight estimate cap for one chat()
  max_tokens_per_session: 400000       # rolling window per session_id
  max_cost_per_session_usd: 5.0        # 0 disables the cost check
  window_s: 3600                       # session window length
  filter_router: true                  # select() skips over-budget pools

pricing_usd_per_1k:
  MiniMax-M3: 0.0                      # missing pool ⇒ cost 0 (token caps only)
  GLM-5.3: 0.0
```

Env overrides (win over the file): `KHAVIS_BUDGET_MAX_TOKENS_REQUEST`,
`KHAVIS_BUDGET_MAX_TOKENS_SESSION`, `KHAVIS_BUDGET_MAX_COST_SESSION_USD`,
`KHAVIS_BUDGET_WINDOW_S`. Bot operators can type `/budget` for live usage.

---

## `config/agents.yaml`

Multi-agent role definitions for the team graph. Each entry overrides a
built-in role from `agents/roles.py`; missing roles fall back to the built-ins
(`planner`, `coder_a`, `coder_b`, `debater`, `critic`, `verifier`, …).

```yaml
roles:
  <role-name>:
    name: <role-name>
    display_name: <str>
    system_prompt: |
      <the role's system prompt>
    capability_required: <tag>   # capability tag resolved to a pool
    temperature: <float>
    max_tokens: <int>
    max_iterations: <int>
    style_hint: <str>
```

Invoke from Python with `agents.graph.run_team(task, session_id=...)` — the
graph runs planner → parallel coders → debate → critic → verify → learn.

---

## `config/rag.yaml`

RAG connector settings (v0.3.0). Set `backend: null` to disable retrieval
entirely (the default). Supported backends: `chroma`, `qdrant`, `pgvector`.

---

## Audit log (`config/audit.jsonl`)

Per-request audit records are appended as one JSON line per HTTP request
(`server/openai_api.py` → `core/audit.py`). Fields:

`ts`, `capability`, `pool`, `model`, `prompt_tokens`, `completion_tokens`,
`total_tokens`, `cost_usd`, `latency_ms`, `status`, `session_id`, `error`.

Controls:

| Mechanism | Effect |
|---|---|
| *(default)* | writes `config/audit.jsonl` |
| `KHAVIS_AUDIT_FILE=/path/to/file.jsonl` | redirect the log |
| `KHAVIS_AUDIT_FILE=/dev/null` (or empty) | disable audit logging |

There is no built-in rotation or retention — point `KHAVIS_AUDIT_FILE` at a
path managed by `logrotate`/journald if you need it.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `KHAVIS_CONFIG_DIR` | Directory holding `capabilities.yaml`, `pools.yaml`, etc. Resolution order: this var → `./config` → package `config/`. |
| `KHAVIS_AUDIT_FILE` | Override the audit JSONL path; `/dev/null` or empty disables. |
| `KHAVIS_HOST` / `KHAVIS_PORT` | Bind address for `khavis serve` (default `127.0.0.1:8080`). |
| `KHAVIS_SESSION` | Default session id for audit/budget attribution on the HTTP server. |
| `KHAVIS_BUDGET_*` | See `config/budgets.yaml` above. |
| `MAC_IP` / `SURFACE_IP` | LAN/Tailscale addresses expanded from `${MAC_IP}` / `${SURFACE_IP}` in `pools.yaml` endpoints (Ollama pools). |
| `<ENV_KEY>` per pool | The env var named by each pool's `env_key:` (e.g. `MINIMAX_API_KEY`, `OPENAI_API_KEY`). |

### `.env` loading

K.H.A.V.I.S. loads `.env` via **`python-dotenv`** (now a declared dependency —
`pip install khavis` brings it in). Lookup order:

1. `./.env` under the current working directory,
2. `.env` at the package root (editable installs, Docker at `/app`).

Existing process env wins — `.env` only fills gaps. Loading is explicit
(`core.env.load_env()`), never at import time. Secrets referenced as
`${VAR}` in YAML endpoints and each pool's `env_key` are resolved after that.

Sample `.env`:

```bash
MINIMAX_API_KEY=sk-...
ZHIPUAI_API_KEY=...
GOOGLE_API_KEY=...
NVIDIA_API_KEY=...
BYTEDANCE_API_KEY=...
OPENROUTER_API_KEY=...
# Optional (separate billing accounts — see README "BYOK")
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

---

## Hot reload

The config watcher lives in `core/hot_reload.py` (`watchdog`-based) and is
started by `scripts/start.sh`, which re-applies `pools.yaml` /
`capabilities.yaml` when either changes (debounced per file). `khavis serve` currently loads config at startup —
restart it (or run under `scripts/start.sh`) after editing YAML. Plugin
**files** always need a process restart to re-import.

---

## Validation

`khavis doctor` checks:

- Python ≥ 3.11.
- `.env` presence (informational).
- `config/pools.yaml` + `config/capabilities.yaml` existence.
- Plugin discovery (and reports any skipped names).
- A per-pool `health_check()` snapshot.

Run it after any config change:

```bash
khavis doctor
```
