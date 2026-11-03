"""額度交接台。

獨立於現行 /workbench。沿用既有 /api/quota、/api/workbench/agents、
/api/workbench/dispatch，不改派工契約。未部署前只在本機路由提供。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["render_relay_workbench_html"]

_HTML = Path(__file__).with_name("relay_workbench.html")


def render_relay_workbench_html() -> str:
    return _HTML.read_text(encoding="utf-8")
