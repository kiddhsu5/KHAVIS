# Architecture

This document is the deep dive. If you only need to *use* K.H.A.V.I.S., the [README](../README.md) is enough. If you want to *extend* it, debug it, or just understand why it is shaped the way it is, read on.

## Design principles

1. **Pluggable everything.** Every provider is a file. Every capability tag is a string. Every memory backend is an interface. Nothing is hard-coded twice.
2. **BYOK and self-hosted.** K.H.A.V.I.S. is your software, running on your hardware, with your keys. There is no K.H.A.V.I.S. cloud.
3. **Capability over identity.** Callers request *what they need* (`code`, `vision`, `cheap`), not *which model*. The router resolves.
4. **Fail forward.** Errors are signal. Quota pressure, latency spikes, and model downtime all feed back into the routing decision in real time.
5. **Files > UI.** Configuration is YAML. State is logs. Audit is append-only JSONL. Everything can be diffed in git.

## The six layers

```
                       Layer 5: Audit & Observability
                       ┌──────────────────────────────────┐
                       │  JSONL log · metrics · traces     │
                       └──────────────────────────────────┘
                                          ▲
                       Layer 4: Memory (working/episodic/semantic)
                       ┌──────────────────────────────────┐
                       │  context cache · sessions · RAG   │
                       └──────────────────────────────────┘
                                          ▲
   ┌────────────────────┐    Layer 3: Multi-agent Orchestration
   │   Your caller      │   ┌──────────────────────────────────┐
   │   (CLI / HTTP /    ├──►│  Pipeline · DAG · Planner/Critic │
   │    Python import)  │   └──────────────────────────────────┘
   └────────────────────┘                  ▲
                                            │
                       Layer 2: Capability Router
                       ┌──────────────────────────────────┐
                       │  prefer/fallback · cooldown · LRU │
                       └──────────────────────────────────┘
                                          ▲
                       Layer 1: Provider Registry & Plugins
                       ┌──────────────────────────────────┐
                       │  auto-discovery · health · quota  │
                       └──────────────────────────────────┘
                                          ▲
                       Layer 0: Provider Plugins (the 12 pools)
                       ┌──────────────────────────────────┐
                       │ MiniMax-M3 · GLM-5.3 ·           ·
                       │ Gemini Flash/Pro · NVIDIA Cloud  ·
                       │ Volcano Ark (×3) · OpenRouter Free ·
                       │ Ollama (local + surface) ·       ·
                       │ OpenAI Platform API · Claude API │
                       └──────────────────────────────────┘
```

---

## Layer 1 — Provider plugins

The lowest layer. Each provider is a single Python class implementing the `ProviderPlugin` interface:

```python
class ProviderPlugin(Protocol):
    name: str
    capabilities: set[str]

    def chat(self, messages: list[dict], **kwargs) -> ChatResponse: ...
    def stream(self, messages: list[dict], **kwargs) -> Iterator[Chunk]: ...
    def health(self) -> HealthStatus: ...
    def quota(self) -> QuotaStatus: ...
```

Plugins live in `providers/`. They are pure logic — no global state, no shared singletons. The only thing they expose is `name` and their capability tags. Auth is injected by the registry at construction time, never read from `os.environ` directly inside the plugin.

This isolation is deliberate: it makes plugins trivial to test (just instantiate and call) and trivial to swap (the registry does not care what is inside).

## Layer 2 — Plugin Registry & auto-discovery

The registry owns:

- **Discovery.** On boot it walks `providers/`, imports each `.py`, finds every `ProviderPlugin` subclass, and indexes it by `name`.
- **Construction.** It reads `pools.yaml`, matches each `pool` entry to a plugin class, injects auth from `.env`, and instantiates one object per pool.
- **Health & quota tracking.** Each pool emits `HealthStatus` and `QuotaStatus` on a background thread every N seconds. The registry maintains a sliding window: last 100 calls, last 60 seconds of quota spend, last error per error class.
- **Hot reload.** Under `scripts/start.sh`, a `watchdog` observer re-applies `config/pools.yaml` and `config/capabilities.yaml` when they change (debounced per file). `khavis serve` reads config only at startup; provider files and `models.yaml` always need a restart.

The registry never decides *which* pool to use. It only knows what is available. That decision belongs to Layer 3.

## Layer 3 — Capability Router

Given a `ChatRequest` (capability + messages + constraints) the router produces an ordered list of candidate pools and an execution plan.

Inputs it considers:

| Signal             | Source                                 | Example                                    |
|--------------------|----------------------------------------|--------------------------------------------|
| Capability tags    | request + `capabilities.yaml`          | `code`, `vision`, `cheap`                  |
| Pool preferences   | `capabilities.yaml`                    | `prefer: [MiniMax-M3, volcano-ark-deepseek]`|
| Fallback chain     | `capabilities.yaml`                    | `fallback: [nvidia-cloud, openrouter-free]`|
| Live health        | registry                               | last call returned 503                     |
| Live quota         | registry                               | `gemini-pro` at 95% of daily cap           |
| Cost ceiling       | request / session                      | `max_cost_usd: 0.01`                       |
| Cooldown           | registry                               | `gemini-pro` cooling down for 47s          |
| Sticky session     | caller-supplied `session_id`           | keep the same pool across turns            |
| Explicit pin       | request `pool=`                        | force a specific pool                      |

The router walks the preference list, drops pools that violate a hard constraint (cost ceiling, capability mismatch, cooldown active), and returns the survivors in priority order.

## Layer 4 — Multi-agent orchestration

A single `ChatRequest` is not always enough. Some workloads want a planner → coder → critic chain, a debate pattern, or a parallel fan-out with voting.

`khavis.agents` provides three primitives:

- `Pipeline(steps)` — sequential, each step sees the previous step's output.
- `DAG(nodes, edges)` — arbitrary directed graph of LLM calls.
- `Debate(agents, rounds)` — N agents respond in parallel, a judge picks the best.

Every primitive uses the same router underneath. Each step is just a `ChatRequest` with its own capability tag, so a `critic` step can demand `reasoning` while a `coder` step demands `code`, and the router will pick the best fit for each independently.

The orchestrator also owns:

- **Step-level retries.** A failed `coder` step retries independently — it does not re-run the `planner`.
- **Budget enforcement.** Total cost across steps is summed and capped per `Pipeline.run()`.
- **Cancellation.** Killing the parent propagates to all in-flight child requests.

## Layer 5 — Memory (working / episodic / semantic)

Long-running agents need more than a single `messages` list. K.H.A.V.I.S. ships with a three-tier memory model:

| Tier      | Lifetime                | Purpose                                          | Default backend        |
|-----------|-------------------------|--------------------------------------------------|------------------------|
| Working   | one `Pipeline.run()`    | scratchpad, intermediate tool outputs            | in-process dict        |
| Episodic  | one session (`session_id`)| past turns, summarized when too long           | SQLite (`memory.db`)   |
| Semantic  | cross-session           | long-term facts, user preferences, RAG chunks    | pluggable (Chroma, Qdrant, pgvector) |

Memory is opt-in per request:

```python
from agents.graph import run_team

state = run_team("...", session_id="abc", enable_memory=True)
```

The memory layer is intentionally thin — it is a write/read interface, not a framework. You can replace the SQLite backend with Redis by writing 30 lines.

## Layer 6 — Audit & observability

Every `ChatRequest` produces one append-only JSONL record:

```jsonl
{"ts":"2026-09-20T10:11:12.345Z","req_id":"...","capability":"code","pool":"MiniMax-M3","latency_ms":842,"prompt_tokens":128,"completion_tokens":256,"cost_usd":0.00042,"status":"ok","retries":0}
```

These records are emitted to:

- `audit.jsonl` (rotated daily)
- stdout (when `--verbose`)
- any OTLP-compatible collector (Prometheus, Jaeger, Datadog) via the optional `khavis.otel` plugin

The audit layer is the single source of truth for:

- billing reconciliation
- debugging "why did the router pick *that* pool?"
- capacity planning (which pool saturates first?)
- compliance (who called what, when, with what prompt?)

---

## Sequence diagram — a typical request

```
caller          orchestrator        router          registry         plugin
  │                  │                 │                │                │
  │  chat(cap=code)  │                 │                │                │
  ├─────────────────►│                 │                │                │
  │                  │  resolve(cap)   │                │                │
  │                  ├────────────────►│                │                │
  │                  │                 │  health()      │                │
  │                  │                 ├───────────────►│                │
  │                  │                 │◄──── ok ───────┤                │
  │                  │                 │  quota()       │                │
  │                  │                 ├───────────────►│                │
  │                  │                 │◄── 42% used ───┤                │
  │                  │                 │  candidates=[MiniMax-M3, …]      │
  │                  │◄────────────────┤                │                │
  │                  │  chat(msgs)     │                │                │
  │                  ├─────────────────┼────────────────►│                │
  │                  │                 │                │  invoke        │
  │                  │                 │                ├───────────────►│
  │                  │                 │                │                │  HTTPS call
  │                  │                 │                │                │  (provider API)
  │                  │                 │                │◄─── resp ──────┤
  │                  │                 │                │  ok            │
  │                  │◄────────────────┼────────────────┤                │
  │  ChatResponse    │                 │                │                │
  │◄─────────────────┤                 │                │                │
  │                  │  audit(record)  │                │                │
  │                  ├────────────────►audit.jsonl       │                │
```

If the first pool returns a retryable error, the router drops it from the candidate list (for `cooldown_seconds`) and asks the registry again. The caller never sees a retry; only the final `ChatResponse`.

---

## Why six layers and not three?

Three layers (request → router → plugin) would be simpler. But it would also conflate concerns that change for different reasons:

- Plugins change when a vendor ships a new API.
- The registry changes when you onboard a new provider.
- The router changes when you discover a better routing heuristic.
- The orchestrator changes when you design a new agent pattern.
- Memory changes when you adopt a new vector store.
- Audit changes when compliance asks for a new field.

Keeping them separate means each layer can evolve on its own schedule, be tested in isolation, and be replaced without rewriting the others.

---

## Further reading

- [`PLUGIN_DEVELOPMENT.md`](PLUGIN_DEVELOPMENT.md) — write your own provider plugin.
- [`CONFIGURATION.md`](CONFIGURATION.md) — every YAML knob explained.
- [`FAQ.md`](FAQ.md) — architecture questions we get a lot.
