# Contributing to K.H.A.V.I.S.

Thanks for being here. K.H.A.V.I.S. is small enough that any single PR is appreciated, and big enough that every contributor matters.

This document covers the *how*. For the *what*, see the [open issues](https://github.com/kiddhsu5/KHAVIS/issues) and the [roadmap in the README](README.md#roadmap).

---

## Code of conduct

Be kind. Disagree on the merits. Assume good faith. We follow the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). Bad behavior gets you unsubscribed from the project, not from the LLM community.

---

## Ground rules

1. **Open an issue first** for non-trivial changes (new provider, new routing heuristic, schema change). For typo fixes and small docs improvements, send a PR directly.
2. **One PR, one thing.** Don't bundle a typo fix with a new provider.
3. **Tests are not optional.** Every PR must pass `pytest` and `mypy --strict`.
4. **No drive-by dependencies.** Adding a runtime dep is a discussion, not a fait accompli.

---

## Development setup

```bash
git clone https://github.com/kiddhsu5/KHAVIS.git
cd KHAVIS
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

Run the test suite:

```bash
pytest -q
```

Type-check:

```bash
mypy --strict khavis
```

Lint:

```bash
ruff check .
ruff format --check .
```

A one-shot check (CI parity):

```bash
make ci        # runs format + lint + types + tests
```

---

## Code style

### Python

- **PEP 8** with a line length of 100.
- **Type hints everywhere.** Public functions and methods must be fully annotated. `mypy --strict` clean is required.
- **Dataclasses** for value objects, not dicts.
- **No bare `except:`**. Catch specific exception types.
- **No mutable default arguments**. Use `field(default_factory=...)`.
- **Async-first**. Provider methods are `async def`. The router awaits them.

### Example — a function we would merge

```python
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RoutingDecision:
    pool: str
    estimated_cost_usd: float
    reason: str


async def pick_pool(
    candidates: Iterable[str],
    cost_ceiling_usd: float,
) -> RoutingDecision:
    """Return the cheapest candidate under the cost ceiling."""
    for pool in candidates:
        cost = await estimate_cost(pool)
        if cost <= cost_ceiling_usd:
            return RoutingDecision(pool=pool, estimated_cost_usd=cost, reason="under_ceiling")
    raise NoViablePoolError(candidates=candidates, ceiling=cost_ceiling_usd)
```

### Example — a function we would not

```python
def pick(pools):
    best = None
    for p in pools:
        try:
            c = cost(p)
        except:
            c = 999
        if not best or c < best[1]:
            best = (p, c)
    return best
```

(Why not: bare except, no types, mutable tuple, no docstring.)

### YAML

- 2-space indent. Always.
- Quote strings only when needed (i.e., when they start with a special character).
- Keys are lowercase-hyphenated.

### Commits

- Imperative mood ("Add Volcano Ark plugin", not "Added").
- Subject line under 72 chars.
- Body explains *why*, not *what*. The diff already says *what*.

Good:

```
Add Volcano Ark Doubao sub-model plugin

Users with Volcano Ark credits asked for vision-capable sub-models.
Doubao is the only one that supports image input today, so it fills
the `vision` gap in the default pool preferences.
```

Less good:

```
update pools
```

---

## Testing requirements

Every PR must include or update tests. We use `pytest`. Coverage thresholds:

- `khavis/core/` — 90% line coverage minimum.
- `khavis/plugins/` — 80% line coverage per plugin.
- New providers — at minimum: one happy-path test, one streaming test, one error test.

Run coverage locally:

```bash
pytest --cov=khavis --cov-report=term-missing
```

### Conventions

- Test files mirror source files: `khavis/router.py` → `tests/test_router.py`.
- Use `respx` or `pytest-httpx` for HTTP mocking. **Never** make live network calls in tests.
- Use `pytest-asyncio` for async tests; mark with `@pytest.mark.asyncio`.
- Snapshot tests for serialised routing decisions live in `tests/snapshots/`.

---

## Adding a new provider

This is the most common contribution. The PR template:

```markdown
## Provider: <name>

### Checklist
- [ ] Plugin file at `providers/<name>.py`
- [ ] Class inherits from `ProviderPlugin`
- [ ] `name`, `capabilities`, `chat`, `stream`, `health`, `quota` all implemented
- [ ] Test file at `tests/providers/test_<name>.py`
- [ ] Happy-path, streaming, and error tests included
- [ ] No hardcoded secrets — auth via `${ENV_VAR}` in `pools.yaml`
- [ ] `config/pools.yaml.example` updated (commented out)
- [ ] `docs/CONFIGURATION.md` provider table updated
- [ ] `docs/PLUGIN_DEVELOPMENT.md` capability tags added (if new convention)
- [ ] `CHANGELOG.md` updated under "Unreleased"
- [ ] `pytest` passes locally
- [ ] `mypy --strict` passes
- [ ] `ruff` passes
```

Full walkthrough: [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md).

---

## Pull request process

1. **Fork** the repo, create a branch from `main`: `git checkout -b feat/my-thing`.
2. **Commit** in logical chunks with clear messages.
3. **Push** and open a PR against `main`.
4. **Fill out the PR template** (it will appear automatically).
5. **Wait for CI**. Two green checks are required: `tests` and `lint`.
6. **Address review feedback**. We aim for first review within 3 business days.
7. **Squash-merge**. The merge button will offer "Squash and merge" by default.

A PR is mergeable when:

- [ ] CI is green.
- [ ] At least one maintainer has approved.
- [ ] All review comments are resolved or explicitly deferred.
- [ ] `CHANGELOG.md` is updated.
- [ ] Documentation is updated (if user-facing).

---

## Release process (for maintainers)

1. Update `CHANGELOG.md` — move "Unreleased" items into a dated versioned section.
2. Bump version in `pyproject.toml` and `khavis/__init__.py`.
3. Tag: `git tag -s v0.X.Y -m "v0.X.Y"`.
4. Push tag: `git push origin v0.X.Y`.
5. CI builds and publishes to PyPI + ghcr.io.
6. Announce in Discussions.

We follow [Semantic Versioning](https://semver.org/):

- **MAJOR** — breaking plugin API or config schema change.
- **MINOR** — new feature, new provider, new capability tag.
- **PATCH** — bug fix, doc fix, dependency bump.

---

## Where to ask for help

- **General questions**: [GitHub Discussions](https://github.com/kiddhsu5/KHAVIS/discussions).
- **Bug reports**: [GitHub Issues](https://github.com/kiddhsu5/KHAVIS/issues) with the bug report template.
- **Security**: see `SECURITY.md` — do not file public issues for vulnerabilities.

---

## Recognition

Contributors are listed in:

- `CHANGELOG.md` (every release).
- The GitHub contributors graph (automatic).
- A future `CONTRIBUTORS.md` once we cross ~25 contributors.

Thanks for helping make K.H.A.V.I.S. less painful for everyone juggling five LLM subscriptions.
