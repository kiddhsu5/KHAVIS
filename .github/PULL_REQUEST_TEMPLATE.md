---
name: Pull request
about: Submit a change to K.H.A.V.I.S.
---

## What changed

<!-- A short summary of the change. Link to any related issue(s). -->

- Closes #<issue-number> (if applicable)
- Type of change: bug fix / new feature / refactor / docs / tests

## Why

<!-- Explain the motivation. What problem does this solve? Why is this
the right approach? -->

## How it was tested

<!-- Describe the testing you performed. -->

- [ ] Unit tests pass (`pytest tests/`)
- [ ] Plugin smoke test passes (`python scripts/test_plugins.py`)
- [ ] Ruff lint + format check (`ruff check`, `ruff format --check`)
- [ ] mypy on providers/ core/ (if applicable)
- [ ] Manual end-to-end test with real provider(s): __________

## Configuration / migration impact

<!-- Does this require changes to config/*.yaml, environment variables,
or a database migration? Are there backward-compat considerations? -->

## Screenshots / logs

<!-- Paste logs, terminal output, or screenshots that demonstrate the
fix or feature. -->

## Checklist

- [ ] I have read CONTRIBUTING.md
- [ ] My code follows the project's style (ruff format)
- [ ] I have added/updated tests where appropriate
- [ ] I have updated relevant documentation (README, docs/, CHANGELOG.md)
- [ ] New public APIs are documented (docstrings + README if relevant)
- [ ] No secrets, API keys, or PII are included in the diff