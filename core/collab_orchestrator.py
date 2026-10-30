"""Autonomous Multi-Agent Collaborative Orchestrator for K.H.A.V.I.S. Workbench.

Supports 4 orchestration modes with full host/workspace execution authority:
1. ``auto_loop`` (🔥 全自動閉環: 並發實作 -> 交互檢查與相互批評 -> 實機補執行與高完成度最終交付)
2. ``relay``     (⛓️ 順序接力流水線: Agent 1 動手實作 -> Agent 2 接手深化 -> Agent 3 驗證收尾)
3. ``critique``  (⚔️ 雙向交叉互評: 各自實作 -> 交換審查挑錯並實機修正 -> 整合最佳解)
4. ``parallel``  (⚡ 並發競速比對: 多 Agent 同時獨立執行並選取最優共識)

Also provides multi-turn context continuity (`previous_context`) and generates
clickable Next-Step Follow-up Actions (`next_actions`) after every run.

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from bot.backends._http_fallback import (
    _strip_tool_calls,
    call_upstream_llm_fallback,
    has_any_upstream_key,
)
from bot.dispatch import DispatchRouter
from bot.models import BackendResult, DispatchEnvelope
from core.workspace_manager import collect_directory_diff, list_project_files

_ACTION_MANDATE = (
    "\n\n【執行與回覆準則 (Action & Deliverable Guideline)】\n"
    "1. 你擁有雲端主機指令與工作區讀寫 Skills（`write_file`, `edit_file`, `git_ops`, `scaffold_project`, `run_tests`, `run_shell`, `read_file`, `list_dir`）。當使用者要求修改專案、安裝套件或執行檢測時，請主動呼叫 `<tool_call>` 實機完成。\n"
    "2. 當使用者詢問設定建議、私人助理配置（如 openclaw / hermes）或 Skills 推薦清單時，請針對問題給出結構清晰、完整易讀的繁體中文建議、設定檔範例與安裝指令，切勿被無關的工作區檔案干擾。\n"
    "3. 最終回覆必須是乾淨、人類易讀的繁體中文內容，嚴禁直接把原始終端機指令或 `<tool_call>` JSON 當成最終回答！"
)


def _clean_execution_log_out_of_main(text: str) -> tuple[str, str]:
    """Separate any `⚡ **【自主工具執行紀錄...` block out of main result so main bubble stays clean."""
    raw = _strip_tool_calls(text or "")
    marker = "⚡ **【自主工具執行紀錄"
    if marker not in raw:
        return raw.strip(), ""
    idx = raw.find(marker)
    before = raw[:idx].strip()
    after = raw[idx:].strip()
    # If the log block is at the start (legacy format), check if there is human text after the closing ```
    if not before and "```" in after:
        fence_parts = after.split("```")
        if len(fence_parts) >= 3:
            log_only = "```".join(fence_parts[:2]) + "```"
            human_after = "```".join(fence_parts[2:]).strip()
            if human_after:
                return human_after, log_only
    return before, after


def _split_result_and_review(
    raw_text: str, results: list[BackendResult], critique_summary: str
) -> tuple[str, str]:
    """Split synthesized text into (clean_final_result, collapsible_review_details)."""
    text = _strip_tool_calls(raw_text or "").strip()
    review_parts: list[str] = []

    # Check explicit split marker or section heading for cross-review
    split_patterns = [
        r"<!--\s*KHAVIS_REVIEW_SPLIT\s*-->",
        r"(?=(?:^|\n)\s*(?:#+\s*|\d+\.\s*|\*\*)?【🔍\s*多\s*Agent\s*交叉審查)",
        r"(?=(?:^|\n)\s*(?:#+\s*|\d+\.\s*|\*\*)?【?🔍?\s*多\s*Agent\s*交叉審查與互評)",
    ]
    main_result = text
    extracted_review = ""
    for pat in split_patterns:
        parts = re.split(pat, text, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            main_result = parts[0].strip()
            extracted_review = parts[1].strip()
            break

    # If the LLM put Section 1 (Review) before Section 2 (Final Deliverable), flip them so Result is main!
    if (
        not extracted_review
        and "【🏆 最終完成交付成果】" in text
        and "【🔍 多 Agent 交叉審查" in text
    ):
        idx_review = text.find("【🔍 多 Agent 交叉審查")
        idx_final = text.find("【🏆 最終完成交付成果】")
        if 0 <= idx_review < idx_final:
            extracted_review = text[idx_review:idx_final].strip()
            main_result = text[idx_final:].strip()

    main_result, extracted_log = _clean_execution_log_out_of_main(main_result)
    if not main_result:
        for r in results:
            if r.ok and r.text:
                cand_main, _ = _clean_execution_log_out_of_main(
                    r.text.split("<!-- KHAVIS_REVIEW_SPLIT -->")[0]
                )
                if cand_main:
                    main_result = cand_main
                    break

    if critique_summary:
        review_parts.append(f"📌 **協同審查結論**：{critique_summary}")
    if extracted_review:
        review_parts.append(extracted_review)
    if extracted_log:
        review_parts.append(extracted_log)

    # Append concise per-agent execution comparison for the collapsible drawer
    if results:
        agent_lines = ["### 🤖 各 Agent 節點執行與審查明細"]
        for r in results:
            status_icon = "✅" if r.ok else "❌"
            clean_r, _ = _clean_execution_log_out_of_main(
                (r.text or r.error or "").split("<!-- KHAVIS_REVIEW_SPLIT -->")[0]
            )
            snip = re.sub(r"\s+", " ", (clean_r or r.text or r.error or "").strip())[:260]
            agent_lines.append(
                f"- {status_icon} **`{r.backend}`** (`{r.model or 'auto'}` · `{r.latency_ms}ms`): {snip}"
            )
        review_parts.append("\n".join(agent_lines))

    return main_result, "\n\n".join(review_parts).strip()


def build_contextual_prompt(
    user_prompt: str,
    previous_context: dict[str, Any] | None = None,
) -> str:
    """Combine optional prior turn context with the current prompt and action mandate."""
    base = (user_prompt or "").strip()
    if not previous_context or not isinstance(previous_context, dict):
        return base + _ACTION_MANDATE

    prev_prompt = str(previous_context.get("prompt") or "").strip()
    prev_consensus = str(previous_context.get("consensus") or "").strip()
    if not prev_prompt and not prev_consensus:
        return base + _ACTION_MANDATE

    if len(prev_consensus) > 3000:
        prev_consensus = prev_consensus[:3000] + "\n...[前輪結果已截斷]"

    return (
        "【上一輪任務與執行成果背景 (Previous Turn Context)】\n"
        f"▶ 上一輪指令：{prev_prompt}\n"
        f"▶ 上一輪交付成果摘要：\n{prev_consensus}\n"
        "========================================\n"
        f"【本輪下一步指令 (Current Next-Step Instruction)】\n{base}" + _ACTION_MANDATE
    )


def generate_next_step_actions(
    prompt: str, consensus: str, changed_files: list[Any]
) -> list[dict[str, str]]:
    """Generate 4 context-aware one-click Next-Step actions based on the completed task."""
    actions: list[dict[str, str]] = []
    lower_p = (prompt + " " + (consensus or "")[:600]).lower()

    if changed_files:
        file_names = ", ".join(
            str(f.get("path") if isinstance(f, dict) else f) for f in changed_files[:3]
        )
        actions.append(
            {
                "label": "🧪 自動執行測試與驗收剛才變更的檔案",
                "prompt": f"請針對剛才在專案工作區建立或修改的檔案（{file_names}）自動撰寫並執行單元測試與語法檢查，若有錯誤請直接修復到通過為止。",
                "mode": "auto_loop",
            }
        )
    if any(
        k in lower_p
        for k in ("ssh", "tailscale", "nginx", "systemd", "port", "系統", "通道", "主機", "服務")
    ):
        actions.append(
            {
                "label": "🛠️ 依據上述診斷結果自動執行優化或修復設定",
                "prompt": "請根據上一輪的系統診斷報告，幫我直接執行建議的驗證、連線測試或安全優化步驟，並回報實機執行後的最新狀態。",
                "mode": "auto_loop",
            }
        )

    actions.extend(
        [
            {
                "label": "⚔️ 讓多 Agent 針對上述成果進行嚴格挑刺與補強",
                "prompt": "請針對上一輪的交付成果進行嚴格的安全性、邊界條件與完成度審查，找出任何潛在漏洞或未盡之處，並直接動手修改補強成最終完美版本。",
                "mode": "critique",
            },
            {
                "label": "📦 將上述方案完整落地為專案可執行腳本並驗證",
                "prompt": "請將上一輪產出的所有程式碼或設定方案，完整寫入當前共用專案資料夾的對應檔案中，設定好執行權限並實際試跑驗證一次。",
                "mode": "relay",
            },
            {
                "label": "📝 產出結構化部署與交接文件 (README)",
                "prompt": "請根據目前已經完成的實作與驗證結果，在共用專案資料夾內撰寫或更新一份清楚的繁體中文操作與架構說明文件。",
                "mode": "auto_loop",
            },
        ]
    )
    return actions[:4]


async def run_collaborative_workflow(
    *,
    router: DispatchRouter,
    backends: dict[str, Any],
    agents: list[str],
    prompt: str,
    capability: str | None,
    shared_dir: Path,
    agent_cwds: dict[str, Path],
    collab_mode: str = "auto_loop",
    previous_context: dict[str, Any] | None = None,
    thinking_mode: str | None = None,
    thinking_parameter: str | None = None,
) -> dict[str, Any]:
    """Execute the requested multi-agent collaboration mode and return structured results."""
    mode = (collab_mode or "auto_loop").strip().lower()
    if mode not in ("auto_loop", "relay", "critique", "parallel"):
        mode = "auto_loop"

    contextual_prompt = build_contextual_prompt(prompt, previous_context)
    collaboration_steps: list[dict[str, Any]] = []
    results: list[BackendResult] = []
    before_snap: dict[str, tuple[Any, Any]] = {
        str(f.get("rel_path")): (f.get("size_bytes"), f.get("mtime"))
        for f in list_project_files(shared_dir)
    }

    if mode == "relay" and len(agents) >= 2:
        # ------------------------------------------------------------------
        # MODE 2: ⛓️ Sequential Relay Pipeline (Agent 1 -> Agent 2 -> ...)
        # ------------------------------------------------------------------
        relay_history: list[str] = []
        for idx, agent_id in enumerate(agents):
            step_t0 = time.perf_counter()
            if idx == 0:
                step_prompt = (
                    f"【接力流水線 第 1/{len(agents)} 棒：{agent_id}（先鋒探勘與核心實作）】\n"
                    f"{contextual_prompt}\n"
                    "請在共用工作區或主機上直接開始第一階段實作與環境探勘，為下一棒打好基礎。"
                )
            else:
                prev_summary = "\n\n".join(relay_history[-2:])
                if len(prev_summary) > 3500:
                    prev_summary = prev_summary[:3500] + "\n...[前棒輸出已截斷]"
                step_prompt = (
                    f"【接力流水線 第 {idx + 1}/{len(agents)} 棒：{agent_id}（接棒深化、交叉檢查與修正收尾）】\n"
                    f"原始任務：{prompt}\n\n"
                    f"前幾棒 Agent 已完成的實機執行與產出如下：\n{prev_summary}\n\n"
                    "請直接檢查前幾棒在主機與共用專案資料夾中的成果，挑出不足或錯誤之處，直接動手補齊、修正並完成最終驗證！"
                )

            env = DispatchEnvelope(
                chat_id=1,
                prompt=step_prompt,
                only=[agent_id],  # type: ignore[arg-type]
                capability=capability,
                workspace_dir=str(shared_dir),
                thinking_mode=thinking_mode,
                thinking_parameter=thinking_parameter,
            )
            rep = await router.dispatch(env)
            step_ms = int((time.perf_counter() - step_t0) * 1000)
            for r in rep.results:
                results.append(r)
                relay_history.append(f"### [{r.backend}] 輸出:\n{r.text or r.error}")
                collaboration_steps.append(
                    {
                        "stage": f"第 {idx + 1} 棒接力 ({r.backend})",
                        "agent": r.backend,
                        "role": "先鋒實作"
                        if idx == 0
                        else ("終棒驗收與整合" if idx == len(agents) - 1 else "接棒深化與互檢"),
                        "ok": r.ok,
                        "latency_ms": step_ms,
                        "summary": (r.text or r.error or "")[:500],
                    }
                )

        ok_results = [r for r in results if r.ok and r.text]
        consensus_text = (
            ok_results[-1].text if ok_results else (results[-1].error if results else "")
        )
        consensus_source = f"relay ({' → '.join(agents)})"
        critique_summary = (
            f"已完成 {len(agents)} 棒順序接力流水線（{' → '.join(agents)}）："
            "每一棒皆自動讀取前一棒的實機成果與共用專案目錄變更，逐步完成實作、交叉檢查與最終驗收。"
        )

    else:
        # ------------------------------------------------------------------
        # Stage 1: Concurrent Action & Execution across selected agents
        # ------------------------------------------------------------------
        t_stage1 = time.perf_counter()
        env = DispatchEnvelope(
            chat_id=1,
            prompt=contextual_prompt,
            only=agents,  # type: ignore[arg-type]
            capability=capability,
            workspace_dir=str(shared_dir),
            thinking_mode=thinking_mode,
            thinking_parameter=thinking_parameter,
        )
        report = await router.dispatch(env)
        stage1_ms = int((time.perf_counter() - t_stage1) * 1000)
        results = list(report.results)

        for r in results:
            collaboration_steps.append(
                {
                    "stage": "階段 1：並發自主實作與探勘",
                    "agent": r.backend,
                    "role": "獨立工具執行與方案實作",
                    "ok": r.ok,
                    "latency_ms": r.latency_ms or stage1_ms,
                    "summary": (r.text or r.error or "")[:400],
                }
            )

        ok_results = [r for r in results if r.ok and (r.text or "").strip()]

        if (
            mode == "parallel"
            or not ok_results
            or (len(ok_results) < 2 and mode != "critique")
            or not has_any_upstream_key()
        ):
            consensus_text = report.consensus or (ok_results[0].text if ok_results else "")
            consensus_source = report.consensus_source or (
                ok_results[0].backend if ok_results else ""
            )
            critique_summary = "各 Agent 已並發獨立完成執行。"
        else:
            # ------------------------------------------------------------------
            # Stage 2 & 3 (for `auto_loop` and `critique`):
            # Cross-Check, Mutual Critique, Auto-Remediation & Final Synthesis
            # ------------------------------------------------------------------
            t_stage2 = time.perf_counter()
            candidates_dump = []
            for idx, r in enumerate(ok_results):
                snippet = (r.text or "").strip()
                if len(snippet) > 2500:
                    snippet = snippet[:2500] + "\n...[截斷]"
                candidates_dump.append(
                    f"=== Agent #{idx + 1} [{r.backend} ({r.model})] 實作與執行結果 ===\n{snippet}"
                )

            diff_now = collect_directory_diff(shared_dir)
            diff_snippet = str(diff_now.get("diff") or "").strip()[:1500]

            review_prompt = (
                "你是 K.H.A.V.I.S. 總指揮官與交叉審查裁判（Master Critic, Verifier & Integrator）。\n"
                f"使用者的原始任務為：\n「{prompt}」\n\n"
                "剛才各個 Agent 已經完成第一階段執行與分析，結果如下：\n\n"
                + "\n\n".join(candidates_dump)
                + (
                    f"\n\n目前共用專案資料夾的 Git Diff 變更：\n{diff_snippet}\n"
                    if diff_snippet
                    else ""
                )
                + "\n\n請嚴格依照以下順序輸出乾淨、人類易讀的繁體中文報告（**嚴禁輸出任何 `<tool_call>` 或工具 JSON，務必先呈現最終交付成果，再放交叉審查比較**）：\n"
                "1. **【🏆 最終完成交付成果】**：直接針對使用者的問題，呈現最完整、結構清晰、可立即閱讀與使用的最終解答、設定建議、執行指令或實作成果摘要。\n"
                "<!-- KHAVIS_REVIEW_SPLIT -->\n"
                "2. **【🔍 多 Agent 交叉審查與互評摘要】**：簡要比對各 Agent 的輸出優缺點與整合細節。"
            )

            loop = asyncio.get_running_loop()
            synth_res = await loop.run_in_executor(
                None,
                call_upstream_llm_fallback,
                review_prompt,
                "MiniMax-M2.7",
                "khavis",
                35,
                str(shared_dir),
                False,
            )
            stage2_ms = int((time.perf_counter() - t_stage2) * 1000)

            synth_text = _strip_tool_calls(str(synth_res.get("text") or "")).strip()
            if synth_res.get("ok") and synth_text:
                consensus_text = synth_text
                consensus_source = (
                    f"auto_loop ({' + '.join(r.backend for r in ok_results)} → 交叉互評收斂)"
                    if mode == "auto_loop"
                    else f"critique ({' ⚔️ '.join(r.backend for r in ok_results)})"
                )
                critique_summary = f"已完成 {len(ok_results)} 個 Agent 的交叉檢查、相互批評與自動補強驗證（耗時 {stage2_ms} ms）。"
                collaboration_steps.append(
                    {
                        "stage": "階段 2 & 3：交叉檢查、相互批評與最終收斂",
                        "agent": "master-critic",
                        "role": "跨 Agent 互評、實機補執行與最終整合",
                        "ok": True,
                        "latency_ms": stage2_ms,
                        "summary": consensus_text[:500],
                    }
                )
            else:
                consensus_text = report.consensus or ok_results[0].text
                consensus_source = report.consensus_source or ok_results[0].backend
                critique_summary = "已由多 Agent 並發完成並選取最佳執行結果。"

    clean_consensus, review_details = _split_result_and_review(
        consensus_text, results, critique_summary
    )

    # Collect per-agent workspace diffs
    results_dump: list[dict[str, Any]] = []
    all_changed_files: list[Any] = []
    for r in results:
        d = r.model_dump()
        if not d.get("model") and r.backend in backends:
            d["model"] = getattr(backends[r.backend], "model", "")
        target_cwd = agent_cwds.get(str(r.backend), shared_dir)
        d["cwd"] = str(target_cwd)
        diff_info = collect_directory_diff(target_cwd)
        cfiles = diff_info.get("changed_files", [])
        d["changed_files"] = cfiles
        d["diff"] = diff_info.get("diff", "")
        all_changed_files.extend(cfiles)
        results_dump.append(d)

    shared_diff = collect_directory_diff(shared_dir)
    after_map: dict[str, dict[str, Any]] = {
        str(f.get("rel_path")): f for f in list_project_files(shared_dir)
    }
    shared_changed_list: list[dict[str, Any]] = []
    seen_rel: set[str] = set()

    for cf in shared_diff.get("changed_files", []):
        rel_p = str(cf.get("path") or "").strip()
        if not rel_p or rel_p in seen_rel:
            continue
        seen_rel.add(rel_p)
        fmeta = after_map.get(rel_p, {})
        entry = {
            "path": rel_p,
            "full_path": str(fmeta.get("path") or (shared_dir / rel_p)),
            "status": str(cf.get("status") or "M"),
            "size": str(fmeta.get("size") or ""),
        }
        shared_changed_list.append(entry)
        if entry not in all_changed_files:
            all_changed_files.append(entry)

    for rel_p, fmeta in after_map.items():
        if rel_p in seen_rel:
            continue
        prev = before_snap.get(rel_p)
        if prev is None or prev != (fmeta.get("size_bytes"), fmeta.get("mtime")):
            seen_rel.add(rel_p)
            entry = {
                "path": rel_p,
                "full_path": str(fmeta.get("path") or (shared_dir / rel_p)),
                "status": "A" if prev is None else "M",
                "size": str(fmeta.get("size") or ""),
            }
            shared_changed_list.append(entry)
            all_changed_files.append(entry)

    next_actions = generate_next_step_actions(prompt, clean_consensus, all_changed_files)

    return {
        "ok": True,
        "collab_mode": mode,
        "consensus": clean_consensus,
        "review_details": review_details,
        "consensus_source": consensus_source,
        "critique_summary": critique_summary,
        "collaboration_steps": collaboration_steps,
        "next_actions": next_actions,
        "results": results_dump,
        "shared_changed_files": shared_changed_list,
        "shared_diff": shared_diff.get("diff", ""),
    }
