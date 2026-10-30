# K.H.A.V.I.S. v0.5.0

**Release date:** 2026-10-28
**Theme:** Provider-aware Workbench and reproducible zero-quota operations

K.H.A.V.I.S. v0.5.0 packages the current provider-aware Workbench changes into a reproducible release candidate. The release keeps the existing self-hosted, BYOK architecture and adds better model identity, capability discovery, attachments, thinking controls, and long-history handling.

## Highlights

- Provider-qualified model references preserve the connected provider when a model name is shared by more than one upstream.
- The Workbench can display provider-reported models and thinking modes instead of relying only on a stale hard-coded catalog.
- Conversations can include validated attachments stored in the shared workspace with bounded request sizes.
- Claude Code and Codex preserve provider routing through CLI and HTTP-compatible fallback paths, including the JUAPI TokenRouter presets.
- Long execution-history fields retain more context while keeping safety bounds and dashboard rendering predictable.
- Workbench C page fragments and legacy slice boundaries are aligned with the current page structure.
- `examples/zero_quota_demo.py` demonstrates 429 handling, quota exhaustion, webhook alerting, and a local Ollama-style fallback without API keys, external network calls, or a running Ollama server.

## Quick verification

From a clean checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev,server,agents]"
.venv/bin/python examples/zero_quota_demo.py --json
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check core bot scripts web providers tests
```

The demo is deterministic and does not consume provider quota. Real provider calls remain optional and require the relevant BYOK environment variables.

## Model defaults

The public catalog uses provider-qualified values where needed. The current configured defaults are:

| CLI | Default model | Routing note |
| --- | --- | --- |
| Claude Code | `gemini-4-argon` | Provider-qualified catalogue; JUAPI Anthropic-compatible route when configured |
| Codex | `kimi-k3.1-pro` | JUAPI TokenRouter when configured |
| MiniMax Code | `MiniMax-M3.1-Flash` | MiniMax-compatible route |
| OpenCode | `glm-5.3-flashx` | OpenAI-compatible route |

The Workbench treats a connected provider's `/models` response as authoritative when capability discovery is available. A displayed model name is not a promise that the corresponding account has quota or that a provider's CLI is installed.

## Upgrade notes

This release does not bundle API keys, cookies, Manus credentials, provider subscriptions, or a hosted control plane. Copy the relevant environment example and configure secrets outside Git. Existing `config/` files and shared workspaces are preserved; review provider-qualified model references if you have a locally customized catalog.

The zero-quota demo uses an in-memory `Ollama-Mock` pool. To test a real local fallback, configure an Ollama endpoint separately and use the existing failover and stress-test scripts with your own environment.

## Known boundaries

The release gate covers offline unit tests, provider contract behavior represented by mocks, Python packaging, and Docker configuration validation. It does not claim that every real provider credential, subscription quota, or third-party CLI is healthy at release time.
