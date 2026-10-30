"""Regression tests for the Option-C task-stream Workbench (web/workbench_c.py).

The C page reuses the legacy client-side JS verbatim and only re-shells the
markup, so the risk is not "new logic breaks" but "the re-shell drops a DOM
node the legacy JS depends on". These tests pin that contract.

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from web.workbench import _WORKBENCH_CLIENT_JS, _WORKBENCH_PAGE_HEAD, render_workbench_html
from web.workbench_c import render_workbench_html_c

# Every id the legacy client JS reaches for via getElementById.
# Kept in sync with web/workbench.py; a missing id means a silently broken
# feature in the new layout.
REQUIRED_DOM_IDS = (
    "active-context-banner",
    "active-context-text",
    "agents-fleet-grid",
    "alerts-log-container",
    "artifact-drawer",
    "artifacts-badge",
    "artifacts-grid",
    "btn-dispatch",
    "btn-dispatch-label",
    "btn-mode-shared",
    "btn-mode-worktree",
    "ca-args",
    "ca-base-url",
    "ca-binary",
    "ca-id",
    "ca-key-env",
    "ca-model",
    "ca-name",
    "ca-type",
    "capability-hint",
    "attachment-input",
    "attachment-list",
    "card-title-antigravity",
    "card-title-claude",
    "card-title-codex",
    "collab-mode-desc",
    "custom-agent-panel",
    "custom-agents-topology-grid",
    "dag-detail-drawer",
    "drawer-content",
    "drawer-meta",
    "drawer-title",
    "exec-spinner",
    "exec-status-text",
    "exec-timer",
    "history-container",
    "latency-bars-container",
    "latency-chart-box",
    "live-results-container",
    "model-save-toast",
    "model-settings-panel",
    "prompt-input",
    "thinking-capability-source",
    "thinking-mode",
    "quota-capsule-strip",
    "quota-cards-grid",
    "theme-icon",
    "wizard-env-template",
    "wizard-providers-grid",
    "wizard-save-toast",
    "workspace-diff-panel",
    "ws-active-path",
    "ws-diff-tabs",
    "ws-diff-viewer",
    "ws-file-count",
    "ws-files-list",
    "ws-merge-action",
    "ws-mode-badge",
    "ws-project-select",
)

# Tabs that switchTab() toggles via .tab-content / nav-<tab>.
REQUIRED_TABS = ("command", "artifacts", "quotas", "agents", "wizard")

_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
    "path",
    "circle",
    "line",
    "rect",
    "polyline",
    "polygon",
    "ellipse",
    "use",
    "stop",
}


class _BalanceChecker(HTMLParser):
    """Catch unbalanced markup produced by slicing the legacy template."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, tuple[int, int]]] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001, D102
        if tag not in _VOID:
            self.stack.append((tag, self.getpos()))

    def handle_endtag(self, tag: str) -> None:  # noqa: D102
        if tag in _VOID:
            return
        if not self.stack:
            self.errors.append(f"stray </{tag}> at line {self.getpos()[0]}")
            return
        open_tag, pos = self.stack.pop()
        if open_tag != tag:
            self.errors.append(
                f"</{tag}> at line {self.getpos()[0]} closes <{open_tag}> opened at line {pos[0]}"
            )


def test_render_workbench_html_c_basics() -> None:
    html = render_workbench_html_c()
    assert html.startswith("<!DOCTYPE html>")
    assert "K.H.A.V.I.S." in html
    # The new task-stream landmarks
    assert 'id="c-task-stream"' in html
    assert 'id="c-agent-chips"' in html
    assert 'id="c-progress-bar"' in html
    assert 'id="c-stage-row"' in html
    assert 'id="c-console"' in html
    # Morandi palette tokens
    assert "--m-mist" in html
    assert "#7C9BB5" in html
    assert "JUAPI TokenRouter" in html
    assert "gemini-4-argon" in html
    assert "kimi-k3.1-pro" in html


@pytest.mark.parametrize("dom_id", REQUIRED_DOM_IDS)
def test_c_page_keeps_legacy_dom_contract(dom_id: str) -> None:
    """Every node the legacy JS looks up must still exist in the C layout."""
    assert f'id="{dom_id}"' in render_workbench_html_c()


@pytest.mark.parametrize("tab", REQUIRED_TABS)
def test_c_page_keeps_all_tabs(tab: str) -> None:
    html = render_workbench_html_c()
    assert f'id="view-{tab}"' in html
    assert f'id="nav-{tab}"' in html


def test_c_page_markup_is_balanced() -> None:
    checker = _BalanceChecker()
    checker.feed(render_workbench_html_c())
    assert checker.errors == []
    assert checker.stack == []


def test_c_page_contains_no_duplicate_ids() -> None:
    """Duplicate ids break getElementById and inline onclick handlers."""
    html = render_workbench_html_c()
    ids = re.findall(r'id="([^"]+)"', html)
    dupes = {i for i in ids if ids.count(i) > 1}
    assert dupes == set()


def test_c_page_does_not_redefine_legacy_dom_ids() -> None:
    """The C chrome must not reuse ids that the legacy panels also declare.

    The legacy markup is sliced into the new layout, so any id invented for
    the new header/footer must be namespaced (c-*) to avoid shadowing.
    """
    html = render_workbench_html_c()
    legacy_ids = set(re.findall(r'id="([^"]+)"', _WORKBENCH_PAGE_HEAD))
    c_ids = {i for i in re.findall(r'id="(c-[^"]+)"', html)}
    assert c_ids, "expected the C page to define its own c-* ids"
    # every c-* id must be unique to the C page
    assert not (c_ids & legacy_ids)


def test_c_page_reuses_legacy_client_js_verbatim() -> None:
    """The C page must not fork the client logic — that is how drift starts."""
    html = render_workbench_html_c()
    assert _WORKBENCH_CLIENT_JS in html


def test_legacy_refactor_is_output_identical() -> None:
    """Splitting the legacy template into constants must not change output."""
    from web.workbench import _WORKBENCH_PAGE_TAIL

    rebuilt = _WORKBENCH_PAGE_HEAD + _WORKBENCH_CLIENT_JS + _WORKBENCH_PAGE_TAIL
    assert rebuilt == render_workbench_html()


def test_legacy_capture_block_precedes_overrides() -> None:
    """Function hoisting means overrides must capture legacy refs in an
    earlier script block, or they recurse into themselves."""
    html = render_workbench_html_c()
    capture = html.index("__legacySwitchTab")
    override = html.index("_switchTabLegacy = __legacySwitchTab")
    assert capture < override
    for name in ("__legacySetCollabMode", "__legacySetPipelineStep", "__legacySubmitDispatch"):
        assert name in html


def test_no_element_indexof_mistake() -> None:
    """getElementById() returns an Element, which has no .indexOf().

    Regression: the C dispatch wrapper read ``statusEl.indexOf('✅')`` straight
    off the Element. That threw a TypeError *after* every run, which left the
    re-entrancy flag set — so every later "接下來怎麼辦" click and every
    follow-up silently did nothing.
    """
    html = render_workbench_html_c()
    bad = re.findall(r"getElementById\([^)]*\)\s*\.indexOf\(", html)
    assert bad == []
    # and the correct form must be present where the status is read
    assert re.search(r"statusEl[^;]*\.textContent", html)


def test_dispatch_wrapper_always_releases_reentrancy_flag() -> None:
    """cRunningTask must be cleared even when the legacy dispatch throws."""
    html = render_workbench_html_c()
    body = html[html.index("async function submitDispatch()") :]
    body = body[: body.index("\nfunction cStageUpdate")]
    assert "dispatchError" in body, "wrapper must capture dispatch failures"
    assert body.count("cFinishTask") >= 1
    # the flag is cleared inside cFinishTask, which must be reached on both paths
    assert re.search(r"catch\s*\(e\)\s*\{\s*dispatchError\s*=\s*e;", body)
    assert "if (dispatchError) throw dispatchError;" in body


def test_c_page_next_step_and_followup_are_wired() -> None:
    """The next-step flow depends on these legacy globals staying reachable."""
    html = render_workbench_html_c()
    for needle in (
        "runNextStepAction",  # rendered by legacy submitDispatch
        "submitFollowup",
        "followup-inline-input",
        "latestNextActions",
        "latestFinalDeliverableText",
        "__legacySubmitDispatch",
    ):
        assert needle in html, f"missing {needle}"


def _run_node_harness(script_name: str, page_html: str) -> subprocess.CompletedProcess:
    import shutil
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    harness = Path(__file__).with_name(script_name)
    assert harness.exists(), f"missing harness {harness}"
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(page_html, encoding="utf-8")
        return subprocess.run([node, str(harness), str(page)], capture_output=True, text=True)


def test_c_page_scripts_are_syntactically_valid() -> None:
    """Cheap guard against a malformed inline script (node --check)."""
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    html = render_workbench_html_c()
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert blocks
    with tempfile.TemporaryDirectory() as tmp:
        for i, block in enumerate(blocks):
            path = f"{tmp}/b{i}.js"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(block)
            proc = subprocess.run([node, "--check", path], capture_output=True, text=True)
            assert proc.returncode == 0, f"block {i} failed:\n{proc.stderr}"


def test_c_page_loads_without_runtime_errors() -> None:
    """Headless DOM shim: all inline scripts execute and DOMContentLoaded fires."""
    proc = _run_node_harness("smoke_workbench.js", render_workbench_html_c())
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_next_step_buttons_and_followup_actually_work() -> None:
    """Drive the real interaction chain against a mocked dispatch API.

    Regression: a TypeError after the run left the re-entrancy flag set, so the
    "接下來怎麼辦" buttons and the follow-up field rendered but did nothing.
    This exercises submitDispatch → runNextStepAction → submitFollowup.
    """
    proc = _run_node_harness("functional_workbench.js", render_workbench_html_c())
    assert proc.returncode == 0, proc.stdout + proc.stderr
