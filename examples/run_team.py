#!/usr/bin/env python3
"""Run the multi-agent team on a sample coding task.

Example::

    cd /path/to/khavis
    python3 examples/run_team.py

The script demonstrates:

* building the graph,
* running a sample task end-to-end,
* printing every intermediate state field,
* saving an attribution summary to logs/.

It does NOT require network access — if no API keys are present the
agent factory will raise and the fallback path will write a placeholder.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running this file directly without installing the package.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


SAMPLE_TASK = (
    "Write a Python function to calculate Fibonacci numbers with memoization. "
    "Include type hints, docstring, and a small pytest-style test."
)


def _print_state(label: str, value: object) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n{label}\n{bar}")
    if value is None:
        print("(empty)")
        return
    if isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the K.H.A.V.I.S. team.")
    parser.add_argument("--task", default=SAMPLE_TASK, help="User task prompt.")
    parser.add_argument(
        "--session-id",
        default="demo-session",
        help="Thread id used by the memory checkpointer.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip LLM calls and only print the state skeleton.",
    )
    args = parser.parse_args(argv)

    from agents.attribution import get_attribution_logger
    from agents.graph import build_graph, run_team

    if args.dry_run:
        print("[dry-run] building graph only ...")
        try:
            g = build_graph()
            print("graph nodes:", list(g.nodes.keys()) if hasattr(g, "nodes") else "(compiled)")
            print("graph OK")
        except RuntimeError as exc:
            print(f"[dry-run] graph not built: {exc}")
        return 0

    print(f"[run_team] task: {args.task!r}")
    print(f"[run_team] session_id: {args.session_id}")
    final = run_team(args.task, session_id=args.session_id)

    _print_state("PLAN", final.get("plan"))
    _print_state("CODE A", final.get("code_a"))
    _print_state("CODE B", final.get("code_b"))
    _print_state("DEBATE LOG", final.get("debate_log"))
    _print_state("CRITIC REVIEW", final.get("critic_review"))
    _print_state("VERIFICATION", final.get("verification"))
    _print_state("FINAL", final.get("final"))

    summary = get_attribution_logger().get_session_summary(args.session_id)
    _print_state("ATTRIBUTION SUMMARY", summary)

    print("\n[done] attribution log written to logs/attribution.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
