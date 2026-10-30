"""Unit tests for the dispatch aggregator and synthesis logic."""

from __future__ import annotations

from bot.aggregator import format_report
from bot.dispatch import synthesize
from bot.models import BackendResult, DispatchEnvelope, DispatchReport


def _env(prompt: str = "p") -> DispatchEnvelope:
    return DispatchEnvelope(chat_id=1, prompt=prompt)


class TestSynthesize:
    def test_picks_longest_above_threshold(self):
        r1 = BackendResult(backend="claude", ok=True, text="a" * 30, model="m")
        r2 = BackendResult(backend="codex", ok=True, text="b" * 25, model="m")
        r3 = BackendResult(backend="khavis", ok=True, text="c" * 20, model="m")
        text, source = synthesize([r1, r2, r3])
        assert text == "a" * 30
        assert source == "claude"

    def test_falls_back_to_first_when_all_short(self):
        r1 = BackendResult(backend="claude", ok=True, text="hi", model="m")
        r2 = BackendResult(backend="codex", ok=True, text="hey", model="m")
        text, source = synthesize([r1, r2])
        assert text in ("hi", "hey")
        assert source in ("claude", "codex")

    def test_skips_failures(self):
        ok = BackendResult(backend="claude", ok=True, text="a" * 50, model="m")
        bad = BackendResult(backend="codex", ok=False, error="boom", model="m")
        text, source = synthesize([bad, ok])
        assert text == "a" * 50
        assert source == "claude"

    def test_all_failures_returns_empty(self):
        bad1 = BackendResult(backend="claude", ok=False, error="x", model="m")
        bad2 = BackendResult(backend="codex", ok=False, error="y", model="m")
        text, source = synthesize([bad1, bad2])
        assert text == ""
        assert source == ""


class TestFormatReport:
    def test_renders_header_and_each_block(self):
        report = DispatchReport(
            envelope=_env("hello"),
            results=[
                BackendResult(backend="claude", ok=True, text="a" * 30, model="m1"),
                BackendResult(backend="codex", ok=False, error="boom", model="m2"),
            ],
            consensus="a" * 30,
            consensus_source="claude",
        )
        text = format_report(report)
        assert "/run: hello" in text
        assert "claude" in text
        assert "boom" in text
        assert "consensus" in text.lower()

    def test_empty_results_still_renders(self):
        report = DispatchReport(envelope=_env(), results=[], consensus="", consensus_source="")
        text = format_report(report)
        assert "/run" in text
        assert "no backend" in text.lower()

    def test_truncates_long_prompt(self):
        long_prompt = "x" * 500
        report = DispatchReport(
            envelope=_env(long_prompt),
            results=[BackendResult(backend="claude", ok=True, text="ok", model="m")],
            consensus="ok",
            consensus_source="claude",
        )
        text = format_report(report)
        # Long prompt should be truncated with …
        assert "…" in text
        # And no single line is absurdly long
        assert max(len(line) for line in text.splitlines()) <= 300


class TestPlainTextRendering:
    """Reports go out with parse_mode=None, so they must not be MarkdownV2-escaped."""

    def test_report_has_no_markdown_escapes(self):
        report = DispatchReport(
            envelope=_env("fix foo.bar() - now!"),
            results=[
                BackendResult(backend="claude", ok=True, text="use x.y(z) - done.", model="m.1")
            ],
            consensus="use x.y(z) - done.",
            consensus_source="claude",
        )
        text = format_report(report)
        assert "\\" not in text
        assert "fix foo.bar() - now!" in text
        assert "use x.y(z) - done." in text
        assert "(m.1," in text


class _Boom:
    name = "codex"

    async def run(self, envelope):
        raise RuntimeError("kaboom")

    async def health(self):  # pragma: no cover - unused
        raise NotImplementedError


class _Ok:
    name = "claude"

    async def run(self, envelope):
        return BackendResult(backend="claude", ok=True, text="fine " * 10, model="m")

    async def health(self):  # pragma: no cover - unused
        raise NotImplementedError


class TestBackendExceptions:
    """A raising backend used to build BackendResult(backend="unknown") -> ValidationError."""

    def _router(self):
        from bot.dispatch import DispatchRouter

        return DispatchRouter({"codex": _Boom(), "claude": _Ok()})  # type: ignore[dict-item]

    def test_dispatch_streaming_keeps_real_backend_name(self):
        import asyncio

        seen: list[str] = []

        async def on_complete(r, _all):
            seen.append(r.backend)

        env = DispatchEnvelope(chat_id=1, prompt="p", only=["codex", "claude"])
        report = asyncio.run(self._router().dispatch_streaming(env, on_complete))
        by_name = {r.backend: r for r in report.results}
        assert by_name["codex"].ok is False
        assert "kaboom" in by_name["codex"].error
        assert by_name["claude"].ok is True
        assert sorted(seen) == ["claude", "codex"]

    def test_dispatch_and_async_iterator(self):
        import asyncio

        env = DispatchEnvelope(chat_id=1, prompt="p", only=["codex", "claude"])
        router = self._router()
        report = asyncio.run(router.dispatch(env))
        assert {r.backend for r in report.results} == {"codex", "claude"}

        async def collect():
            return [r async for r in router.dispatch_async(env)]

        got = asyncio.run(collect())
        assert {r.backend for r in got} == {"codex", "claude"}
