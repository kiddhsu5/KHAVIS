"""The relay workbench is a separate route and must keep its three controls."""

from __future__ import annotations

from web.relay_workbench import render_relay_workbench_html


def test_relay_workbench_exposes_user_controls() -> None:
    html = render_relay_workbench_html()
    assert "額度交接台" in html
    assert 'id="workflows"' in html
    assert 'id="agent-picks"' in html
    assert "data-model-agent" in html
    assert "/api/workbench/dispatch" in html
    assert "不顯示假百分比" in html
    assert "X-KHAVIS-Console-Token" in html
