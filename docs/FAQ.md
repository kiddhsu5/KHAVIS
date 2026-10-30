# Frequently Asked Questions

> **15+ common questions about khavis.** If yours is not here, open a [Discussion](https://github.com/kiddhsu5/KHAVIS/discussions).

---

## General

### 1. How is K.H.A.V.I.S. different from OpenRouter?

OpenRouter is a **hosted gateway**. You send requests to OpenRouter's servers, OpenRouter calls upstream providers on your behalf, and you pay OpenRouter.

K.H.A.V.I.S. is a **self-hosted router** that runs on your machine. You send requests to *your* process, which calls the providers directly using *your* API keys. There is no K.H.A.V.I.S. company in the loop.

|                       | OpenRouter              | K.H.A.V.I.S.                          |
|-----------------------|-------------------------|-------------------------------------|
| Hosting               | Their cloud             | Your box                            |
| Billing               | Through OpenRouter      | Through your existing subscriptions |
| Free tier access      | Yes (rate-limited)      | Yes (your own free-tier keys)       |
| Local models          | No                      | Yes (Ollama)                        |
| Custom routing rules  | Limited                 | Unlimited YAML                      |
| Data passes through   | OpenRouter servers      | Never leaves your machine*          |

\* *Aside from the actual provider calls you make.*

### 2. How is K.H.A.V.I.S. different from LiteLLM?

[LiteLLM](https://github.com/BerriAI/litellm) is a translation layer. It gives you a uniform Python API to call many providers, and it is excellent at that job.

K.H.A.V.I.S. is built on top of the same idea but adds:

- **Routing decisions.** LiteLLM lets you *call* a provider; K.H.A.V.I.S. decides *which* provider to call for you.
- **Capability matching.** You request a capability tag, not `gpt-4o`.
- **Budget-aware selection.** Over-budget pools drop out of selection (`filter_router`) before you hit their 429s; on an upstream error the caller simply re-selects (automatic in-call retry is on the roadmap).
- **Multi-agent orchestration.** A built-in team graph — planner → parallel coders → debate → critic → verify → learn (`agents/graph.py`).
- **Three-tier memory.** Working, episodic, semantic — out of the box.
- **Audit & cost tracking.** Append-only JSONL, per-call attribution.

If you only need a thin wrapper, LiteLLM is great. If you need a *router*, K.H.A.V.I.S. is built for you.

### 3. Is K.H.A.V.I.S. a SaaS?

No. There is no K.H.A.V.I.S. cloud. The whole project is self-hosted Apache 2.0 software. If someone is selling you "K.H.A.V.I.S. as a service", they are a third party.

### 4. What license is K.H.A.V.I.S. under?

[Apache License 2.0](../LICENSE). You can use it commercially, modify it, distribute it, and ship it inside proprietary products, as long as you keep the license notice and clearly mark any changes you make.

---

## Setup & usage

### 5. Can I use my own API keys?

Yes — that is the entire point. Every pool in `pools.yaml` references `${ENV_VAR}` placeholders. Put your keys in `.env` (or your secret manager) and the router picks them up at boot. K.H.A.V.I.S. never sees, stores, or proxies your keys to anyone other than the providers themselves.

### 5b. What's BYOK — and which plugins need a separate API account?

**BYOK = Bring Your Own Key.** Some pools are powered by accounts you almost certainly already have (MiniMax, ZhipuAI, Google AI Studio, ByteDance Volcano Ark, OpenRouter, local Ollama). A small handful require a *separate* API account billed directly by the upstream provider. These plugins are still enabled in the code by default — the integration test runner just skips them gracefully when the env var is unset.

| Plugin        | Requires separate API account? | Where to sign up                          |
|---------------|--------------------------------|-------------------------------------------|
| `MiniMax-M3`  | No                             | you already have it                       |
| `GLM-5.3`     | No                             | you already have it                       |
| `Google-Gemini` | No                           | you already have it                       |
| Volcano Ark (×3) | No                          | you already have it                       |
| `OpenRouter-Free` | No                         | you already have it                       |
| Ollama (×2)   | No                             | local                                     |
| **`OpenAI-API`**   | **Yes**                  | https://platform.openai.com               |
| **`Claude-API`**   | **Yes**                  | https://console.anthropic.com             |
| **`NVIDIA-Cloud`** | **Yes** (paid GPU)     | https://build.nvidia.com                   |

Note that an **OpenAI Platform API key** is different from a ChatGPT Plus or ChatGPT GO subscription; an **Anthropic API key** is different from Claude Code or Cursor Pro. If you only have the subscription tier and not the platform tier, simply leave the env var empty — the router will route around the missing pool.

### 6. How do I add a new LLM provider?

Write a single Python file under `providers/`, register it in `pools.yaml`, tag it in `capabilities.yaml`. The registry auto-discovers it on boot (or hot-reload). The full walkthrough is in [`PLUGIN_DEVELOPMENT.md`](PLUGIN_DEVELOPMENT.md). It usually takes 30–90 minutes for a straightforward provider.

### 7. Does K.H.A.V.I.S. support fine-tuned models?

Fine-tuned models are supported via per-pool `options.model` overrides — set the
model name in `pools.yaml` to your fine-tuned ID and the provider plugin will
request it. v0.5.0 also adds provider-qualified model references and live model
capability discovery; a separate first-class fine-tuned registry with hot-swap
is still a future roadmap item.

### 8. Can I run K.H.A.V.I.S. without any cloud provider?

Yes. Point every pool at `ollama-local`, and the router becomes a pure local-model dispatcher. You lose failover diversity but gain full offline operation. This is a popular setup for air-gapped environments.

### 9. Does K.H.A.V.I.S. work behind a corporate proxy / firewall?

Yes. Set `HTTPS_PROXY` and `HTTP_PROXY` in the environment (standard `requests`/`httpx` conventions), and all provider calls will go through it. Self-signed CA certificates can be passed via `SSL_CERT_FILE`.

### 10. How much overhead does K.H.A.V.I.S. add?

For a happy-path request: ~1–3 ms of in-process routing logic on top of the provider's own latency. We measure overhead at p99 < 10 ms in benchmarks with 10 pools active. The routing layer adds only selection + budget/audit bookkeeping; the upstream call is made by the selected plugin and the response passes straight back to you.

---

## Privacy & data

### 11. What data does K.H.A.V.I.S. collect?

**None, by default.** K.H.A.V.I.S. does not phone home. There is no telemetry, no usage beacon, no "phone home if it crashes" opt-out.

What it *does* do locally:

- Writes an audit log (`config/audit.jsonl`) with: timestamp, capability, pool, model, token counts, cost, latency, status.
- Caches episodic memory in a local SQLite file if you opt in.

You can disable the audit log entirely with `KHAVIS_AUDIT_FILE=/dev/null` (or an empty value). You can disable episodic memory by simply not requesting it.

### 12. Where do my prompts go?

Directly to the provider whose pool served the request. K.H.A.V.I.S. does not proxy, log, or store the prompt content unless you explicitly enable a memory backend that does so.

### 13. Is K.H.A.V.I.S. SOC 2 / HIPAA / GDPR compliant?

K.H.A.V.I.S. itself is just code — it inherits the compliance posture of wherever you run it. If you run it on a SOC 2-compliant cloud, in a HIPAA BAA, or under a GDPR-compliant data processing agreement, the *router* does not change that. What you should worry about is the same as without khavis: which providers you send data to, and under what contract.

The audit log is designed to help you demonstrate compliance (who accessed what, when), not to undermine it. Disable it if your auditor objects.

### 14. Can I disable hot reload for compliance reasons?

Hot reload only runs when you start through `scripts/start.sh` (the `watchdog` watcher in `core/hot_reload.py`). `khavis serve` reads config at startup and never re-reads it — so a plain `khavis serve` deployment has nothing to disable.

---

## Operations

### 15. How do I monitor K.H.A.V.I.S. in production?

Three options, pick one:

1. **Audit log.** Ship `config/audit.jsonl` to your log aggregator (Vector, Filebeat, Fluent Bit). Every request is one line.
2. **Status server.** Run `python scripts/status_server.py` — it serves `/healthz`, `/dashboard` and `/wall` with live pool health and dispatch history.
3. **`GET /v1/models`.** The OpenAI-compatible server lists every model the discovered pools advertise — a cheap liveness probe for your blackbox exporter.

### 16. Can I run multiple K.H.A.V.I.S. instances for high availability?

Yes, but you probably do not need to. A single K.H.A.V.I.S. process can serve thousands of requests per second on commodity hardware (the bottleneck is provider latency, not the router). If you do run multiple, they are stateless — point them at the same `config/` (read-only). Audit lines are appended per process, so give each instance its own `KHAVIS_AUDIT_FILE` (or merge the files in your log pipeline).

### 17. How do I upgrade without downtime?

K.H.A.V.I.S. supports zero-downtime config reloads (hot reload), but binary upgrades require a restart. Recommended:

1. Start the new version on a different port.
2. Smoke-test it (`khavis doctor`, a few `/v1/chat/completions` calls).
3. Flip your reverse proxy from the old version to the new.
4. Shut down the old version.

The audit log format is append-only; old lines remain readable after upgrade.

### 18. What's on the roadmap?

See the [README roadmap](../README.md#roadmap). Highlights: cost budgeting
(v0.2.0), built-in RAG (v0.3.0), OpenAI-compatible server mode (v0.4.0),
provider-aware Workbench and zero-quota demo (v0.5.0), web UI (v0.6.0), and a
stable v1.0 API.

---

## Troubleshooting quick links

- `khavis doctor` — first thing to run.
- [`INSTALLATION.md`](../INSTALLATION.md) — common install errors.
- [`docs/CONFIGURATION.md`](CONFIGURATION.md) — every YAML knob.
- [GitHub Issues](https://github.com/kiddhsu5/KHAVIS/issues) — bug reports.
- [GitHub Discussions](https://github.com/kiddhsu5/KHAVIS/discussions) — questions.
