---
name: Bug report
about: Report a bug in K.H.A.V.I.S. so we can investigate and fix it
title: "[bug] "
labels: ["bug", "triage"]
assignees: []
---

## Bug description

A clear and concise description of what the bug is.

## Reproduction steps

1. Configure K.H.A.V.I.S. with... (e.g. provider, model, capabilities)
2. Run command... (e.g. `python scripts/start.sh`, `curl ...`)
3. Observe failure

Minimal config snippet that reproduces the issue (if applicable):

```yaml
# config/pools.yaml
pools:
  - name: my-pool
    provider: openai
    model: gpt-4o-mini
```

## Expected behavior

What you expected to happen.

## Actual behavior

What actually happened — include the full error message, stack trace, or
unexpected output.

```text
Paste logs here
```

## Environment

- OS / distro (e.g. Ubuntu 22.04, macOS 14):
- Python version (`python3 --version`):
- K.H.A.V.I.S. version / commit (`git rev-parse HEAD`):
- Installation method (pip / source / Docker):
- Relevant provider / model:

## Logs / additional context

Attach or paste any extra logs, screenshots, or context that might help
diagnose the issue. For secrets, please redact!

## Checklist

- [ ] I have searched existing issues and discussions for this bug
- [ ] I have included reproduction steps
- [ ] I have removed any sensitive information (API keys, etc.)