"""Operator web dashboard + personal-developer wall.

Pure renderers — no framework, no build step. `scripts/status_server.py`
and the bot FastAPI app both call :func:`render_dashboard_html` /
:func:`api_payload`. The wall is README-facing HTML and also serves at
``/wall`` for a live stargazer strip.
"""

from __future__ import annotations

import html
import time
from typing import Any

BRAND = "#2f6feb"
ACCENT_GREEN = "#16a34a"
ACCENT_YELLOW = "#ca8a04"
ACCENT_RED = "#dc2626"
ACCENT_PURPLE = "#9333ea"

# Curated personal-developer wall entries. Extended via PR — see README.
WALL_ENTRIES: list[dict[str, str]] = [
    {
        "label": "kiddhsu5",
        "detail": "Multi-Agent CLI Workbench · MiniMax / GLM / GPT GO / Antigravity / Ollama",
        "url": "https://github.com/kiddhsu5",
    },
]

# The dashboard is served on a *public* status page. History events carry
# the user's prompt text and Telegram chat_id, and the budget guard keys its
# per-session usage by ``chat-<chat_id>`` — none of that may leave the box.
_PUBLIC_HISTORY_KEYS = (
    "ts",
    "kind",
    "ok",
    "backend",
    "backends",
    "phase",
    "latency_ms",
    "consensus_source",
)


def public_history(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Strip prompts, chat ids, errors and anything else not whitelisted."""
    out: list[dict[str, Any]] = []
    for e in events or []:
        if isinstance(e, dict):
            out.append({k: e[k] for k in _PUBLIC_HISTORY_KEYS if k in e})
    return out


def public_budget(status: dict[str, Any] | None) -> dict[str, Any] | None:
    """Aggregate-only view of ``BudgetGuard.status()``."""
    if not isinstance(status, dict):
        return None
    sessions = status.get("sessions") or {}
    rows = [v for v in sessions.values() if isinstance(v, dict)]
    return {
        "policy": status.get("policy") or {},
        "by_pool": status.get("by_pool") or {},
        "sessions": {
            "count": len(rows),
            "tokens": float(sum(float(r.get("tokens") or 0) for r in rows)),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 6),
            "calls": float(sum(float(r.get("calls") or 0) for r in rows)),
        },
    }


def api_payload(
    *,
    pools: list[dict[str, Any]],
    bot: dict[str, Any],
    cache: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
    wall: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """JSON shape for ``/api/dashboard``. Secrets stay redacted upstream."""
    budget: dict[str, Any] | None = None
    try:
        from core.budget import get_guard  # noqa: PLC0415

        budget = public_budget(get_guard().status())
    except Exception:  # noqa: BLE001
        budget = None

    quotas: dict[str, Any] = {}
    try:
        from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

        quotas = get_quota_tracker().all_snapshots()
    except Exception:  # noqa: BLE001
        quotas = {}

    alerts: list[dict[str, Any]] = []
    try:
        from core.alerting import get_alert_manager  # noqa: PLC0415

        alerts = get_alert_manager().recent_alerts(20)
    except Exception:  # noqa: BLE001
        alerts = []

    return {
        "generated_at": time.time(),
        "pools": pools,
        "bot": bot,
        "cache": cache or {"age_s": None, "warming": False},
        "history": public_history(history),
        "wall": wall if wall is not None else WALL_ENTRIES,
        "budget": budget,
        "quotas": quotas,
        "alerts": alerts,
    }


def _css() -> str:
    return f"""
  :root {{ color-scheme: light dark; }}
  body {{ font-family: ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
         margin: 0 auto; max-width: 68rem; padding: 1.75rem 1.25rem 4rem;
         line-height: 1.55; }}
  h1 {{ font-size: 1.6rem; margin-bottom: .15rem; }}
  h1 span {{ color: {BRAND}; }}
  h2 {{ font-size: 1.15rem; margin: 2rem 0 .75rem; border-bottom: 2px solid color-mix(in srgb, currentColor 10%, transparent); padding-bottom: .35rem; }}
  .sub {{ opacity: .75; margin-top: 0; }}
  .badges {{ display: flex; gap: .5rem; flex-wrap: wrap; margin: 1rem 0 1.5rem; }}
  .badge {{ border: 1px solid color-mix(in srgb, {BRAND} 45%, transparent);
            color: {BRAND}; border-radius: 999px; padding: .22rem .7rem;
            font-size: .78rem; font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .88rem; margin: .5rem 0 1.25rem; }}
  th, td {{ text-align: left; padding: .5rem .6rem;
            border-bottom: 1px solid color-mix(in srgb, currentColor 14%, transparent);
            vertical-align: middle; }}
  th {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .04em;
        opacity: .65; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: .85em; }}
  .small {{ font-size: .78rem; opacity: .8; }}
  .detail {{ max-width: 20rem; word-break: break-word; }}
  .summary {{ margin: 1rem 0 1.25rem; font-size: 1.02rem; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
            gap: .75rem; margin: 1rem 0 1.25rem; }}
  .card {{ border: 1px solid color-mix(in srgb, currentColor 14%, transparent);
           border-radius: 10px; padding: .85rem 1rem; }}
  .card .n {{ font-size: 1.45rem; font-weight: 700; color: {BRAND}; }}
  .card .l {{ font-size: .78rem; opacity: .7; }}
  .wall {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
           gap: .75rem; margin: .75rem 0 1rem; }}
  .wall a {{ text-decoration: none; color: inherit; }}
  .wall .who {{ font-weight: 650; color: {BRAND}; }}
  .bars {{ display: grid; gap: .45rem; margin: .75rem 0 1.25rem; }}
  .bar-row {{ display: grid; grid-template-columns: 11rem 1fr 6rem; gap: .55rem;
              align-items: center; font-size: .82rem; }}
  .bar-track {{ background: color-mix(in srgb, currentColor 10%, transparent);
                border-radius: 6px; height: 10px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 6px; background: {BRAND}; }}

  /* Status badges */
  .badge-status {{ font-size: .72rem; font-weight: 600; padding: .18rem .5rem; border-radius: 999px; display: inline-block; white-space: nowrap; }}
  .status-healthy {{ background: color-mix(in srgb, {ACCENT_GREEN} 18%, transparent); color: {ACCENT_GREEN}; border: 1px solid color-mix(in srgb, {ACCENT_GREEN} 40%, transparent); }}
  .status-warning {{ background: color-mix(in srgb, {ACCENT_YELLOW} 18%, transparent); color: {ACCENT_YELLOW}; border: 1px solid color-mix(in srgb, {ACCENT_YELLOW} 40%, transparent); }}
  .status-exhausted {{ background: color-mix(in srgb, {ACCENT_RED} 18%, transparent); color: {ACCENT_RED}; border: 1px solid color-mix(in srgb, {ACCENT_RED} 40%, transparent); }}
  .status-local {{ background: color-mix(in srgb, {ACCENT_PURPLE} 18%, transparent); color: {ACCENT_PURPLE}; border: 1px solid color-mix(in srgb, {ACCENT_PURPLE} 40%, transparent); }}

  /* Countdown timer */
  .countdown {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; font-weight: 600; padding: .15rem .45rem; border-radius: 5px; background: color-mix(in srgb, {BRAND} 12%, transparent); color: {BRAND}; }}
  .countdown.active {{ color: {ACCENT_YELLOW}; background: color-mix(in srgb, {ACCENT_YELLOW} 15%, transparent); }}
  .countdown.ready {{ color: {ACCENT_GREEN}; background: color-mix(in srgb, {ACCENT_GREEN} 15%, transparent); }}

  /* Agent Grid */
  .agent-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: .75rem; margin: .75rem 0 1.25rem; }}
  .agent-card {{ border: 1px solid color-mix(in srgb, currentColor 14%, transparent); border-radius: 8px; padding: .75rem .9rem; background: color-mix(in srgb, currentColor 2%, transparent); }}
  .agent-card .role {{ font-weight: 650; font-size: .95rem; color: {BRAND}; }}
  .agent-card .cap {{ font-size: .78rem; opacity: .75; margin-top: .15rem; }}

  /* Alert Feed */
  .alert-feed {{ display: flex; flex-direction: column; gap: .5rem; margin: .75rem 0 1.25rem; font-size: .84rem; }}
  .alert-item {{ padding: .55rem .85rem; border-radius: 6px; border-left: 4px solid; display: flex; justify-content: space-between; align-items: center; background: color-mix(in srgb, currentColor 3%, transparent); }}
  .alert-warning {{ border-color: {ACCENT_YELLOW}; }}
  .alert-error, .alert-critical {{ border-color: {ACCENT_RED}; }}
  .alert-info {{ border-color: {BRAND}; }}

  footer {{ margin-top: 2.5rem; font-size: .78rem; opacity: .6; }}
  a {{ color: {BRAND}; }}
"""


def _js() -> str:
    return """
  <script>
    function updateCountdowns() {
      const now = Date.now();
      document.querySelectorAll('.countdown').forEach(el => {
        const resetAt = parseFloat(el.getAttribute('data-reset-at') || '0');
        if (!resetAt) {
          el.innerText = '—';
          el.className = 'countdown ready';
          return;
        }
        const diff = Math.max(0, Math.ceil((resetAt * 1000 - now) / 1000));
        if (diff <= 0) {
          el.innerText = 'Reset Ready';
          el.className = 'countdown ready';
        } else {
          const m = Math.floor(diff / 60);
          const s = diff % 60;
          el.innerText = m > 0 ? `${m}m ${s}s` : `${s}s`;
          el.className = 'countdown active';
        }
      });
    }
    setInterval(updateCountdowns, 1000);
    document.addEventListener('DOMContentLoaded', updateCountdowns);
  </script>
"""


def render_dashboard_html(
    *,
    pools: list[dict[str, Any]],
    bot: dict[str, Any],
    cache: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
    wall: list[dict[str, str]] | None = None,
) -> str:
    ok_n = sum(1 for r in pools if r.get("ok") is True)
    warming_n = sum(1 for r in pools if r.get("ok") is None)
    total = len(pools)
    bot_ok = bool(bot.get("ok"))
    history = public_history(history)
    wall = wall if wall is not None else WALL_ENTRIES

    # Fetch live quota snapshots and alerts
    quotas: dict[str, Any] = {}
    try:
        from core.quota_tracker import get_quota_tracker  # noqa: PLC0415

        quotas = get_quota_tracker().all_snapshots()
    except Exception:  # noqa: BLE001
        quotas = {}

    alerts: list[dict[str, Any]] = []
    try:
        from core.alerting import get_alert_manager  # noqa: PLC0415

        alerts = get_alert_manager().recent_alerts(15)
    except Exception:  # noqa: BLE001
        alerts = []

    try:
        from core.budget import get_guard  # noqa: PLC0415

        _bpol = get_guard().policy
        budget_req = int(_bpol.max_tokens_per_request)
        budget_cost = float(_bpol.max_cost_per_session_usd)
    except Exception:  # noqa: BLE001
        budget_req, budget_cost = 0, 0.0

    now = time.time()

    def quota_row(p: dict[str, Any]) -> str:
        name = str(p.get("name"))
        provider_id = str(p.get("provider_id"))
        is_local = provider_id == "ollama" or "local" in (p.get("capabilities") or [])
        snap = quotas.get(name, {})

        rem_req = snap.get("remaining_requests")
        rem_tok = snap.get("remaining_tokens")
        balance_usd = snap.get("balance_usd")
        is_exhausted = snap.get("is_exhausted", False)
        reset_s = snap.get("seconds_until_reset", 0.0)
        reset_at = (now + reset_s) if reset_s > 0 else 0

        # Status badge determination
        if is_exhausted or p.get("ok") is False:
            badge = '<span class="badge-status status-exhausted">🔴 額度耗盡 / 429</span>'
        elif (rem_req is not None and rem_req <= 2) or (rem_tok is not None and rem_tok <= 500):
            badge = '<span class="badge-status status-warning">🟡 額度告急</span>'
        elif is_local:
            badge = '<span class="badge-status status-local">💻 本地 Ollama</span>'
        else:
            badge = '<span class="badge-status status-healthy">🟢 正常可用</span>'

        req_str = str(rem_req) if rem_req is not None else "—"
        tok_str = f"{rem_tok:,}" if rem_tok is not None else "—"
        bal_str = f"${balance_usd:.2f}" if balance_usd is not None else "—"

        timer_html = (
            f'<span class="countdown" data-reset-at="{reset_at}">{int(reset_s)}s</span>'
            if reset_s > 0
            else '<span class="countdown ready">—</span>'
        )

        return (
            "<tr>"
            f"<td><code>{html.escape(name)}</code></td>"
            f"<td>{badge}</td>"
            f"<td class='small'>{req_str}</td>"
            f"<td class='small'>{tok_str}</td>"
            f"<td class='small'>{bal_str}</td>"
            f"<td>{timer_html}</td>"
            f"<td class='small detail'>{html.escape(str(p.get('model') or '—'))}</td>"
            "</tr>"
        )

    def hist_row(h: dict[str, Any]) -> str:
        ok = h.get("ok")
        mark = "✅" if ok else ("⏳" if ok is None else "❌")
        backends = h.get("backends")
        if isinstance(backends, list) and backends:
            backend = ", ".join(str(b) for b in backends)
        else:
            backend = h.get("backend") or h.get("phase") or "-"
        kind = str(h.get("kind") or h.get("phase") or "")[:80]
        ms = h.get("latency_ms")
        ts = h.get("ts")
        when = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "—"
        return (
            "<tr>"
            f"<td>{mark}</td>"
            f"<td class='small'>{html.escape(when)}</td>"
            f"<td><code>{html.escape(str(backend))}</code></td>"
            f"<td class='detail'>{html.escape(kind)}</td>"
            f"<td class='small'>{ms if ms is not None else ''}</td>"
            "</tr>"
        )

    def alert_row(a: dict[str, Any]) -> str:
        lvl = str(a.get("level", "info")).lower()
        pool = html.escape(str(a.get("pool_name", "")))
        msg = html.escape(str(a.get("message", "")))
        ts = a.get("timestamp")
        when = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "—"
        return (
            f"<div class='alert-item alert-{lvl}'>"
            f"<div><strong>[{lvl.upper()}]</strong> <code>{pool}</code>: {msg}</div>"
            f"<div class='small'>{when}</div>"
            f"</div>"
        )

    # Agent roles preview
    agent_roles = [
        ("Planner", "理論 / 架構設計", "拆解需求為具體實作步驟與依賴圖"),
        ("Coder A", "程式碼 (Clean)", "以高可讀性、正交簡潔風格實作"),
        ("Coder B", "程式碼 (Defensive)", "以防禦性編程、完善邊界檢查實作"),
        ("Debater", "辯論 (Debate)", "比較多方案優缺點，尋求最佳平衡點"),
        ("Critic", "審查 (Review)", "銳利找出安全缺陷、架構漏洞與壞味道"),
        ("Verifier", "驗證 (Verify)", "三值判定 (Pass/Retry/Terminate)，確認可執行性"),
        ("Learner", "歸納 (Learn)", "提煉成功 Pattern 沉澱至語義記憶"),
    ]
    agent_cards_html = "\n".join(
        f"<div class='agent-card'>"
        f"<div class='role'>{html.escape(role)}</div>"
        f"<div class='cap'>{html.escape(cap)}</div>"
        f"<div class='small' style='margin-top:.35rem'>{html.escape(desc)}</div>"
        f"</div>"
        for role, cap, desc in agent_roles
    )

    alert_list_html = "\n".join(alert_row(a) for a in alerts) or (
        "<div class='small' style='opacity:.7'>目前無觸發中之告警事件</div>"
    )

    hist_rows = "\n".join(hist_row(h) for h in history[:20]) or (
        "<tr><td colspan='5' class='small'>尚無派工紀錄</td></tr>"
    )

    # Budget bars
    try:
        from core.budget import get_guard  # noqa: PLC0415

        _bst = get_guard().status()
        _by_pool = _bst.get("by_pool") or {}
        _pol = _bst.get("policy") or {}
        _sess_cap = float(_pol.get("max_tokens_per_session") or 0) or 1.0
        rows = sorted(_by_pool.items(), key=lambda kv: kv[1].get("tokens", 0), reverse=True)[:8]
        bars = []
        for name, u in rows:
            pct = max(0.0, min(100.0, 100.0 * float(u.get("tokens") or 0) / _sess_cap))
            bars.append(
                f"<div class='bar-row'><code>{html.escape(str(name))}</code>"
                f"<div class='bar-track'><div class='bar-fill' style='width:{pct:.1f}%'></div></div>"
                f"<div class='small'>{int(float(u.get('tokens') or 0))} tok</div></div>"
            )
        if not bars:
            bars = ["<div class='small'>尚無用量記錄</div>"]
        budget_bars = "\n".join(bars)
    except Exception:  # noqa: BLE001
        budget_bars = "<div class='small'>budget unavailable</div>"

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>K.H.A.V.I.S. · 即時額度與 Agent 派工儀表板</title>
<style>{_css()}</style>
</head>
<body>
  <h1><span>K.H.A.V.I.S.</span> · 運營與狀態儀表板</h1>
  <p class="sub">即時額度監控 · 速率限制重置倒數 · 多 Agent 自動派工 · 本地 Ollama 零中斷備援</p>

  <div class="badges">
    <span class="badge">Apache 2.0</span>
    <span class="badge">Zero Quota Interruption</span>
    <span class="badge">Active Rate-Limit Sensing</span>
    <span class="badge">LangGraph 7-Role Team</span>
    <span class="badge">Ollama Local Safeguard</span>
  </div>

  <div style="margin: 1.25rem 0;">
    <a href="/workbench" style="display:inline-flex; align-items:center; gap:0.5rem; padding:0.6rem 1.2rem; background:linear-gradient(135deg, #0EA5E9, #0D9488); color:#ffffff; border-radius:10px; text-decoration:none; font-weight:700; font-size:0.95rem; box-shadow:0 4px 14px rgba(14,165,233,0.35);">
      <span>🚀 開啟統一智慧工作台 (Unified Workbench) &rarr;</span>
    </a>
  </div>

  <div class="cards">
    <div class="card"><div class="n">{ok_n}/{total}</div><div class="l">健康模型池 (Healthy)</div></div>
    <div class="card"><div class="n">{warming_n}</div><div class="l">預熱探測中 (Warming)</div></div>
    <div class="card"><div class="n">{"OK" if bot_ok else "DOWN"}</div><div class="l">Telegram 派工 Bot</div></div>
    <div class="card"><div class="n">{len(alerts)}</div><div class="l">活躍告警事件 (Alerts)</div></div>
    <div class="card"><div class="n">{budget_req // 1000}k</div><div class="l">單請求 Token 上限</div></div>
    <div class="card"><div class="n">${budget_cost:.2f}</div><div class="l">Session 成本預算</div></div>
  </div>

  <h2>即時模型配額與重置倒數 (Real-time Quota & Reset Timers)</h2>
  <table>
    <thead><tr><th>模型池 (Pool)</th><th>即時狀態</th><th>剩餘請求</th><th>剩餘 Token</th><th>餘額 (USD)</th><th>重置倒數 (Reset)</th><th>目前型號</th></tr></thead>
    <tbody>
      {"".join(quota_row(p) for p in pools)}
    </tbody>
  </table>

  <h2>LangGraph 多 Agent 協作流水線 (Multi-Agent Team Status)</h2>
  <p class="small">當任務過度複雜或單一雲端模型額度用盡時，自動無縫派工至以下 7 角色團隊協同作業：</p>
  <div class="agent-grid">
    {agent_cards_html}
  </div>

  <h2>即時監控與 Webhook 告警 (Alerts Feed)</h2>
  <div class="alert-feed">
    {alert_list_html}
  </div>

  <h2>Token 用量監控 (Session Window)</h2>
  <div class="bars">
    {budget_bars}
  </div>

  <h2>近期派工記錄 (Recent Dispatches / Phases)</h2>
  <table>
    <thead><tr><th></th><th>時間</th><th>後端 / 角色</th><th>任務類別</th><th>耗時 (ms)</th></tr></thead>
    <tbody>
      {hist_rows}
    </tbody>
  </table>

  <h2>個人開發者 wall</h2>
  <div class="wall">
    {"".join(f"<a class='card' href='{html.escape(w.get('url') or '#')}' target='_blank' rel='noopener'><div class='who'>{html.escape(w.get('label') or '')}</div><div class='small'>{html.escape(w.get('detail') or '')}</div></a>" for w in wall)}
  </div>

  <footer>
    K.H.A.V.I.S. · Zero Markup · <a href="/healthz">healthz</a> · <a href="/">status</a> · <a href="/api/dashboard">api</a> · <a href="/api/quota">quota-api</a> · <a href="/api/alerts">alerts-api</a>
  </footer>
  {_js()}
</body>
</html>
"""


def render_wall_html(wall: list[dict[str, str]] | None = None) -> str:
    wall = wall if wall is not None else WALL_ENTRIES

    def card(w: dict[str, str]) -> str:
        url = html.escape(w.get("url") or "#")
        label = html.escape(w.get("label") or "anonymous")
        detail = html.escape(w.get("detail") or "")
        return f"<a class='card' href='{url}' target='_blank' rel='noopener'><div class='who'>{label}</div><div class='small'>{detail}</div></a>"

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>K.H.A.V.I.S. · 個人開發者 wall</title>
<style>{_css()}</style>
</head>
<body>
  <h1><span>K.H.A.V.I.S.</span> · 個人開發者 wall</h1>
  <p class="sub">In production at personal / homelab setups. Open a PR to join.</p>
  <div class="wall">
    {"".join(card(w) for w in wall)}
  </div>
  <footer>K.H.A.V.I.S. · <a href="/dashboard">dashboard</a></footer>
</body>
</html>
"""
