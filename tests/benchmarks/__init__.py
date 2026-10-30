"""Benchmarks for K.H.A.V.I.S. hot paths.

These tests time the router's critical paths so we have honest numbers
to publish alongside LiteLLM / Bifrost's marketing claims. They mock all
external I/O so they run in CI without API keys.

Why plain ``time.perf_counter`` instead of ``pytest-benchmark``:
the project does not depend on ``pytest-benchmark`` (see pyproject.toml
``[project.optional-dependencies.dev]``). Adding it would be welcome
but would require a dependency change. Plain timing keeps these tests
portable.

Run::

    pytest tests/benchmarks/ -v -s
    # or:
    python scripts/run_benchmarks.py
"""
