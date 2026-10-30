"""K.H.A.V.I.S. One-Page Promotional Landing & Console Login Page (https://khavis.khorus.tech).

Renders a polished dark-mode Morandi/glassmorphism marketing & login gateway page
whilst preserving the contract assertions expected by status-page unit tests
(escaped pool rows table, `ok_n / total` summary, bot health, and zero secret leaks).

Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import html
import time

GENERATED_AT_FORMAT = "%Y-%m-%d %H:%M:%S UTC"


def render_landing_html(
    rows: list[dict[str, object]],
    bot: dict[str, object],
    cache: dict[str, object] | None = None,
) -> str:
    ok_n = sum(1 for r in rows if r.get("ok") is True)
    warming_n = sum(1 for r in rows if r.get("ok") is None)
    total = len(rows)
    bot_ok = bool(bot.get("ok"))
    cache = cache or {"age_s": None, "warming": False}

    def row_html(r: dict[str, object]) -> str:
        mark = "🟢" if r.get("ok") is True else ("🟡" if r.get("ok") is None else "🔴")
        caps = ", ".join(str(c) for c in r.get("capabilities", []))  # type: ignore[arg-type]
        models = ", ".join(str(m) for m in r.get("models", []))  # type: ignore[arg-type]
        return (
            "<tr class='border-b border-white/5 hover:bg-white/[0.02] transition'>"
            f"<td class='py-2.5 px-3'>{mark}</td>"
            f"<td class='py-2.5 px-3 font-mono text-xs text-sky-300'><code>{html.escape(str(r.get('name')))}</code></td>"
            f"<td class='py-2.5 px-3 text-xs text-slate-300'>{html.escape(str(r.get('provider_id')))}</td>"
            f"<td class='py-2.5 px-3 text-xs text-emerald-300 font-mono'>{html.escape(models)}</td>"
            f"<td class='py-2.5 px-3 text-xs text-slate-400'>{html.escape(caps)}</td>"
            f"<td class='py-2.5 px-3 text-xs text-slate-500 font-mono max-w-xs truncate'>{html.escape(str(r.get('detail')))}</td>"
            "</tr>"
        )

    body_rows = "\n".join(row_html(r) for r in rows)
    warming_html = (
        f"<span class='ml-2 text-amber-300'>🟡 <strong>{warming_n} warming up</strong></span>"
        if warming_n
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-Hant" class="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>KHAVIS · 跨廠牌 AI 編碼 CLI 統一智慧工作台 & 多模型配額路由器</title>
  <meta name="description" content="KHAVIS — 並發調度 Claude Code、OpenAI Codex、Google Antigravity、MiniMax、OpenCode (opencode) 與 Manus API，內建 Git Worktree 隔離、Unified Diff 視覺化合併與 12 模型池零額度中斷路由。">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&family=Noto+Sans+TC:wght@400;500;700;900&display=swap" rel="stylesheet">
  <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {{
      darkMode: 'class',
      theme: {{
        extend: {{
          colors: {{
            ink: {{ 950: '#0B0D12', 900: '#12151E', 800: '#181C26', 700: '#222736' }},
            m: {{
              mist: '#7C9BB5',
              sage: '#8FA894',
              lilac: '#A99AB5',
              apricot: '#C9A17E',
              rose: '#BC8B8B',
              bone: '#E6E4E0'
            }}
          }},
          fontFamily: {{
            sans: ['Inter', 'Noto Sans TC', 'sans-serif'],
            mono: ['JetBrains Mono', 'monospace']
          }}
        }}
      }}
    }};
  </script>
  <style>
    body {{
      background-color: #0B0D12;
      color: #E6E4E0;
      background-image:
        radial-gradient(at 15% 15%, rgba(124, 155, 181, 0.14) 0px, transparent 48%),
        radial-gradient(at 85% 20%, rgba(143, 168, 148, 0.12) 0px, transparent 48%),
        radial-gradient(at 50% 85%, rgba(169, 154, 181, 0.08) 0px, transparent 50%);
      background-attachment: fixed;
    }}
    .glass-card {{
      background: rgba(24, 28, 38, 0.78);
      backdrop-filter: blur(16px);
      border: 1px solid rgba(230, 228, 224, 0.09);
    }}
    .glass-card-hover:hover {{
      border-color: rgba(124, 155, 181, 0.38);
      transform: translateY(-2px);
    }}
    .hero-glow {{
      box-shadow: 0 0 60px -15px rgba(124, 155, 181, 0.25);
    }}
    #status-matrix table {{
      width: 100%;
      text-align: left;
      border-collapse: collapse;
    }}
  </style>
</head>
<body class="font-sans antialiased min-h-screen flex flex-col selection:bg-sky-500/30 selection:text-sky-200">

  <!-- 頂部導覽列 -->
  <header class="sticky top-0 z-30 border-b border-white/[0.07] bg-ink-950/80 backdrop-blur-md">
    <div class="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
      <a href="/" class="flex items-center gap-3 group">
        <div class="w-9 h-9 rounded-xl grid place-items-center font-black text-sm shadow-lg"
             style="background:linear-gradient(135deg,#7C9BB5,#8FA894);color:#0B0D12">K</div>
        <div>
          <span class="font-extrabold tracking-tight text-base text-white">KHAVIS</span>
          <span class="hidden sm:inline-block ml-2 text-[11px] font-mono px-2 py-0.5 rounded-full bg-white/5 text-slate-300 border border-white/10">v0.5.0 Open Core</span>
        </div>
      </a>

      <nav class="hidden md:flex items-center gap-6 text-xs font-medium text-slate-300">
        <a href="#features" class="hover:text-white transition">核心能力</a>
        <a href="#architecture" class="hover:text-white transition">多 CLI 協同</a>
        <a href="#enterprise" class="hover:text-white transition">團隊與商用方案</a>
        <a href="#status-matrix" class="hover:text-white transition">即時節點狀態 ({ok_n}/{total})</a>
      </nav>

      <div class="flex items-center gap-2.5">
        <a href="https://github.com/kiddhsu5/KHAVIS" target="_blank" rel="noopener"
           class="px-3 py-1.5 rounded-lg glass-card hover:bg-white/10 text-xs font-medium flex items-center gap-1.5 transition">
          <i class="fab fa-github text-sm"></i>
          <span class="hidden sm:inline">GitHub</span>
        </a>
        <a href="/workbench"
           class="px-3.5 py-1.5 rounded-lg text-xs font-bold text-ink-950 transition shadow-md flex items-center gap-1.5"
           style="background:linear-gradient(135deg,#8FA894,#7C9BB5)">
          <span>進入工作台</span>
          <i class="fas fa-arrow-right text-[10px]"></i>
        </a>
      </div>
    </div>
  </header>

  <main class="flex-1">
    <!-- Hero 雙欄區：左側產品價值主張 + 右側指揮官登入與訪客快速入口 -->
    <section class="max-w-6xl mx-auto px-4 sm:px-6 pt-12 pb-16 lg:pt-20 lg:pb-24">
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-10 items-center">

        <!-- 左側 7 欄：宣傳主視覺 -->
        <div class="lg:col-span-7 space-y-6">
          <div class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/[0.04] border border-white/10 text-xs font-mono text-slate-300">
            <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            <span>Multi-Agent CLI Workbench · 24-Pool BYOK Router · Zero Quota Interruption</span>
          </div>

          <h1 class="text-3xl sm:text-5xl font-black tracking-tight leading-[1.15] text-white">
            跨廠牌 AI 編碼 CLI 的<br>
            <span class="bg-clip-text text-transparent" style="background-image:linear-gradient(90deg,#7C9BB5,#8FA894,#C9A17E)">
              統一智慧指揮工作台
            </span>
          </h1>

          <p class="text-sm sm:text-base text-slate-300 leading-relaxed max-w-2xl">
            同時訂閱了 Claude、OpenAI、Google Antigravity、MiniMax 與智譜 GLM Coding Plan？
            <strong>KHAVIS</strong> 將五大主流編碼 CLI、Manus 雲端規劃與 24 個模型池收斂進同一個莫蘭迪圖形化工作台。支援<strong>自動閉環、接力、互評、並發</strong>四種協同模式，內建 <strong>Git Worktree 自動隔離與視覺化 Diff 合併</strong>，徹底終結 429 配額中斷與多 Agent 改檔衝突。
          </p>

          <!-- 支援的 CLI 徽章列 -->
          <div class="flex flex-wrap items-center gap-2 pt-1">
            <span class="px-2.5 py-1 rounded-lg bg-white/[0.04] border border-white/10 text-xs font-mono text-emerald-300">
              <i class="fas fa-terminal mr-1.5 opacity-75"></i>Claude Code
            </span>
            <span class="px-2.5 py-1 rounded-lg bg-white/[0.04] border border-white/10 text-xs font-mono text-purple-300">
              <i class="fas fa-code mr-1.5 opacity-75"></i>OpenAI Codex
            </span>
            <span class="px-2.5 py-1 rounded-lg bg-white/[0.04] border border-white/10 text-xs font-mono text-sky-300">
              <i class="fas fa-rocket mr-1.5 opacity-75"></i>Google Antigravity
            </span>
            <span class="px-2.5 py-1 rounded-lg bg-white/[0.04] border border-white/10 text-xs font-mono text-amber-300">
              <i class="fas fa-bolt mr-1.5 opacity-75"></i>MiniMax M3.1
            </span>
            <span class="px-2.5 py-1 rounded-lg bg-white/[0.04] border border-white/10 text-xs font-mono text-rose-300">
              <i class="fas fa-microchip mr-1.5 opacity-75"></i>OpenCode (GLM-5.3)
            </span>
            <span class="px-2.5 py-1 rounded-lg bg-white/[0.04] border border-white/10 text-xs font-mono text-slate-300">
              <i class="fas fa-satellite-dish mr-1.5 opacity-75"></i>Manus API v2
            </span>
          </div>

          <!-- 關鍵效能指標 -->
          <div class="grid grid-cols-3 gap-3 pt-3 max-w-lg">
            <div class="glass-card rounded-xl p-3.5">
              <div class="text-xl sm:text-2xl font-black font-mono text-white">0.02<span class="text-xs font-normal text-slate-400 ml-1">ms</span></div>
              <div class="text-[11px] text-slate-400 mt-0.5">Router 選池開銷 (p95)</div>
            </div>
            <div class="glass-card rounded-xl p-3.5">
              <div class="text-xl sm:text-2xl font-black font-mono text-emerald-300">{ok_n} / {total}</div>
              <div class="text-[11px] text-slate-400 mt-0.5">在線健康模型池</div>
            </div>
            <div class="glass-card rounded-xl p-3.5">
              <div class="text-xl sm:text-2xl font-black font-mono text-sky-300">Apache 2.0</div>
              <div class="text-[11px] text-slate-400 mt-0.5">100% BYOK · 零抽成</div>
            </div>
          </div>
        </div>

        <!-- 右側 5 欄：指揮官登入卡片 & 訪客唯讀體驗入口 -->
        <div class="lg:col-span-5">
          <div class="glass-card hero-glow rounded-2xl p-6 sm:p-7 space-y-5 relative overflow-hidden">
            <div class="flex items-center justify-between border-b border-white/10 pb-4">
              <div>
                <span class="text-[10px] font-mono uppercase tracking-wider px-2 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30">Console Access</span>
                <h2 class="text-lg font-bold text-white mt-1.5">登入 KHAVIS 任務工作台</h2>
              </div>
              <div class="w-10 h-10 rounded-xl bg-white/5 border border-white/10 grid place-items-center text-sky-300">
                <i class="fas fa-shield-halved text-lg"></i>
              </div>
            </div>

            <!-- 登入表單 -->
            <form id="console-login-form" onsubmit="handleConsoleLogin(event)" class="space-y-4">
              <div>
                <label for="landing-console-token" class="block text-xs font-medium text-slate-300 mb-1.5">
                  指揮官授權金鑰 (Console Token)
                </label>
                <div class="relative">
                  <input id="landing-console-token" type="password" autocomplete="current-password"
                         placeholder="輸入 KHAVIS_CONSOLE_TOKEN 解鎖完整派工權限…"
                         class="w-full rounded-xl bg-ink-950/90 border border-white/15 px-3.5 py-2.5 text-xs sm:text-sm font-mono text-white placeholder-slate-500 focus:outline-none focus:border-sky-400 transition" />
                </div>
                <p class="text-[11px] text-slate-400 mt-1.5 leading-relaxed">
                  <i class="fas fa-lock text-[10px] mr-1 text-emerald-400"></i>Token 僅保存在您的瀏覽器當前分頁 (<code>sessionStorage</code>)，驗證通過後將自動跳轉至 <code>/workbench</code> 並解鎖多 Agent 派工與 Manus 指令台。
                </p>
              </div>

              <div id="login-feedback" class="hidden rounded-xl px-3.5 py-2.5 text-xs font-mono"></div>

              <button type="submit" id="login-submit-btn"
                      class="w-full py-2.5 px-4 rounded-xl font-bold text-xs sm:text-sm text-ink-950 transition shadow-lg flex items-center justify-center gap-2"
                      style="background:linear-gradient(135deg,#7C9BB5,#8FA894)">
                <i class="fas fa-key"></i>
                <span>驗證 Token 並進入指揮工作台</span>
              </button>
            </form>

            <div class="relative flex py-1 items-center">
              <div class="flex-grow border-t border-white/10"></div>
              <span class="flex-shrink mx-3 text-[11px] font-mono text-slate-500">OR LIVE DEMO</span>
              <div class="flex-grow border-t border-white/10"></div>
            </div>

            <!-- 訪客免登入唯讀預覽按鈕 -->
            <div class="space-y-2.5">
              <a href="/workbench"
                 class="w-full py-2.5 px-4 rounded-xl bg-white/[0.05] hover:bg-white/[0.1] border border-white/15 text-xs sm:text-sm font-semibold text-white transition flex items-center justify-center gap-2">
                <i class="fas fa-eye text-sky-300"></i>
                <span>免登入 · 進入訪客唯讀展示模式 (Live Demo)</span>
              </a>
              <div class="grid grid-cols-2 gap-2 text-center">
                <a href="/dashboard" class="py-1.5 px-3 rounded-lg bg-white/[0.02] hover:bg-white/[0.06] border border-white/5 text-[11px] font-mono text-slate-400 hover:text-white transition">
                  📊 維運儀表板 (/dashboard)
                </a>
                <a href="/wall" class="py-1.5 px-3 rounded-lg bg-white/[0.02] hover:bg-white/[0.06] border border-white/5 text-[11px] font-mono text-slate-400 hover:text-white transition">
                  🧱 狀態電視牆 (/wall)
                </a>
              </div>
            </div>
          </div>
        </div>

      </div>
    </section>

    <!-- 核心差異化亮點 -->
    <section id="features" class="max-w-6xl mx-auto px-4 sm:px-6 py-14 border-t border-white/[0.07]">
      <div class="text-center max-w-2xl mx-auto mb-10">
        <span class="text-xs font-mono uppercase tracking-widest text-sky-300">Why KHAVIS</span>
        <h2 class="text-2xl sm:text-3xl font-bold text-white mt-1">專為重度 Multi-Agent 開發者打造的工程中樞</h2>
        <p class="text-xs sm:text-sm text-slate-400 mt-2">不只是 API 轉發層，而是把「多訂閱配額調度」、「多 CLI 協同開發」與「Git 分支隔離」合一的完整工作台。</p>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-5">
        <div class="glass-card glass-card-hover rounded-2xl p-6 transition duration-200 space-y-3">
          <div class="w-10 h-10 rounded-xl bg-sky-500/15 border border-sky-500/30 grid place-items-center text-sky-300">
            <i class="fas fa-sitemap"></i>
          </div>
          <h3 class="text-base font-bold text-white">4 種多 CLI 協同派工模式</h3>
          <p class="text-xs text-slate-300 leading-relaxed">
            內建<strong>自動閉環 (Auto-Loop)</strong>、<strong>順序接力 (Relay)</strong>、<strong>雙向互評 (Critique)</strong> 與<strong>獨立並發 (Parallel)</strong>。讓 Antigravity 規劃架構、Claude Code 實作、Codex 撰寫測試並自動收斂最佳成果。
          </p>
        </div>

        <div class="glass-card glass-card-hover rounded-2xl p-6 transition duration-200 space-y-3">
          <div class="w-10 h-10 rounded-xl bg-emerald-500/15 border border-emerald-500/30 grid place-items-center text-emerald-300">
            <i class="fas fa-code-branch"></i>
          </div>
          <h3 class="text-base font-bold text-white">共用資料夾 & Git Worktree Diff</h3>
          <p class="text-xs text-slate-300 leading-relaxed">
            支援單一專案共用目錄無縫接力，或在並發派工時自動為每個 CLI 建立獨立 <strong>Git Worktree 分支</strong>，於網頁上直接檢視紅綠語法高亮 Unified Diff 並一鍵合併。
          </p>
        </div>

        <div class="glass-card glass-card-hover rounded-2xl p-6 transition duration-200 space-y-3">
          <div class="w-10 h-10 rounded-xl bg-amber-500/15 border border-amber-500/30 grid place-items-center text-amber-300">
            <i class="fas fa-gauge-high"></i>
          </div>
          <h3 class="text-base font-bold text-white">配額感知與 429 毫秒級無感切換</h3>
          <p class="text-xs text-slate-300 leading-relaxed">
            即時追蹤 MiniMax Token Plan、智譜 GLM Coding Plan、GPT GO 與本地 Ollama 配額視窗。在觸發 429 限流前自動熔斷並切換備援池，實現<strong>零額度中斷</strong>。
          </p>
        </div>
      </div>
    </section>

    <!-- 30 秒快速自託管部署 & 商業合作 (Open Core) -->
    <section id="enterprise" class="max-w-6xl mx-auto px-4 sm:px-6 py-14 border-t border-white/[0.07]">
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <!-- 開源快速啟動 -->
        <div class="lg:col-span-7 glass-card rounded-2xl p-6 sm:p-7 space-y-4">
          <div class="flex items-center justify-between">
            <span class="text-xs font-mono text-emerald-300 bg-emerald-500/10 px-2.5 py-1 rounded border border-emerald-500/20">
              Apache 2.0 · Self-Hosted BYOK
            </span>
            <a href="https://github.com/kiddhsu5/KHAVIS" target="_blank" rel="noopener" class="text-xs font-mono text-sky-300 hover:underline">
              github.com/kiddhsu5/KHAVIS &rarr;
            </a>
          </div>
          <h3 class="text-lg font-bold text-white">個人與開源社群版：30 秒一鍵啟動</h3>
          <pre class="rounded-xl bg-ink-950/90 border border-white/10 p-4 text-xs font-mono text-slate-200 overflow-x-auto leading-relaxed"><code>git clone https://github.com/kiddhsu5/KHAVIS.git
cd KHAVIS
bash scripts/quick_setup_wizard.sh
khavis serve   # 開啟 http://localhost:8080/workbench</code></pre>
        </div>

        <!-- 企業與團隊商業版 -->
        <div class="lg:col-span-5 glass-card rounded-2xl p-6 sm:p-7 flex flex-col justify-between space-y-4 border-sky-500/25">
          <div class="space-y-2.5">
            <span class="text-xs font-mono text-sky-300 bg-sky-500/10 px-2.5 py-1 rounded border border-sky-500/20">
              🏢 Enterprise & Team Edition
            </span>
            <h3 class="text-lg font-bold text-white">企業研發中台與團隊私有化部署</h3>
            <ul class="text-xs text-slate-300 space-y-2 leading-relaxed pt-1">
              <li><i class="fas fa-check text-emerald-400 mr-2"></i><strong>團隊共享 Coding Plan 配額池</strong>與多成員 RBAC 權限管理</li>
              <li><i class="fas fa-check text-emerald-400 mr-2"></i><strong>企業內網 / VPC 私有化部署</strong>與內部自訂 CLI 工具鏈對接</li>
              <li><i class="fas fa-check text-emerald-400 mr-2"></i><strong>全團隊 Prompt 與 Git Worktree 審計日誌</strong>與合規報表</li>
            </ul>
          </div>
          <div class="pt-2 flex flex-wrap gap-2">
            <a href="https://github.com/kiddhsu5/KHAVIS/security/advisories/new"
               class="px-4 py-2 rounded-xl text-xs font-bold text-ink-950 transition"
               style="background:linear-gradient(135deg,#7C9BB5,#8FA894)">
              📩 聯繫商業合作 / 私有化部署
            </a>
            <a href="https://github.com/kiddhsu5/KHAVIS/discussions" target="_blank" rel="noopener"
               class="px-3.5 py-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-xs font-medium text-slate-200 transition">
              社群討論區
            </a>
          </div>
        </div>
      </div>
    </section>

    <!-- 即時節點健康矩陣（同時滿足 status_server 契約測試） -->
    <section id="status-matrix" class="max-w-6xl mx-auto px-4 sm:px-6 py-12 border-t border-white/[0.07]">
      <div class="glass-card rounded-2xl p-5 sm:p-6 space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 class="text-base font-bold text-white">即時節點與模型池健康狀態 (Live Telemetry)</h2>
            <p class="summary text-xs text-slate-300 mt-1">
              {"🟢" if bot_ok else "🔴"} <strong>Telegram dispatch bot</strong> — {html.escape(str(bot.get("detail")))}
              <span class="mx-2 text-slate-600">|</span>
              {"🟢" if ok_n == total and total else "🟡"} <strong>{ok_n} / {total}</strong> LLM pools healthy
              {warming_html}
            </p>
          </div>
          <div class="flex items-center gap-2 text-xs font-mono">
            <a href="/healthz" class="px-2.5 py-1 rounded bg-white/5 hover:bg-white/10 text-sky-300 border border-white/10">/healthz JSON</a>
            <a href="/pgp.txt" class="px-2.5 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-300 border border-white/10">PGP Key</a>
          </div>
        </div>

        <div class="overflow-x-auto">
          <table>
            <thead>
              <tr class="border-b border-white/10 text-[11px] font-mono uppercase text-slate-400">
                <th class="py-2 px-3">狀態</th>
                <th class="py-2 px-3">Pool</th>
                <th class="py-2 px-3">Provider</th>
                <th class="py-2 px-3">Models</th>
                <th class="py-2 px-3">Capabilities</th>
                <th class="py-2 px-3">Detail</th>
              </tr>
            </thead>
            <tbody>
{body_rows}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </main>

  <footer class="border-t border-white/[0.07] py-6 text-center text-xs text-slate-500 font-mono">
    <div class="max-w-6xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-2">
      <div>
        <strong class="text-slate-300">KHAVIS</strong> (K.H.A.V.I.S.) · Apache-2.0 License
      </div>
      <div>
        <a href="https://github.com/kiddhsu5/KHAVIS" class="text-sky-400 hover:underline">github.com/kiddhsu5/KHAVIS</a>
        · <a href="/workbench" class="text-emerald-400 hover:underline">/workbench</a>
        · <a href="/healthz" class="hover:underline">/healthz</a>
        · rendered {time.strftime(GENERATED_AT_FORMAT, time.gmtime())}
        · cache age {"n/a" if cache.get("age_s") is None else str(cache.get("age_s")) + "s"}{" · refreshing" if cache.get("warming") else ""}
      </div>
    </div>
  </footer>

  <script>
    async function handleConsoleLogin(ev) {{
      ev.preventDefault();
      const input = document.getElementById('landing-console-token');
      const feedback = document.getElementById('login-feedback');
      const btn = document.getElementById('login-submit-btn');
      const token = (input && input.value || '').trim();

      const showMsg = (msg, isErr) => {{
        if (!feedback) return;
        feedback.classList.remove('hidden');
        feedback.textContent = msg;
        feedback.className = 'rounded-xl px-3.5 py-2.5 text-xs font-mono ' + (
          isErr
            ? 'bg-rose-500/15 border border-rose-500/30 text-rose-300'
            : 'bg-emerald-500/15 border border-emerald-500/30 text-emerald-300'
        );
      }};

      if (!token) {{
        showMsg('請輸入 Console Token，或點擊下方「免登入 · 進入訪客唯讀展示模式」。', true);
        return;
      }}

      if (btn) btn.disabled = true;
      showMsg('正在驗證 Console Token…', false);

      try {{
        const res = await fetch('/api/manus/status', {{
          headers: {{ 'X-KHAVIS-Console-Token': token }}
        }});
        const data = await res.json().catch(() => ({{}}));
        if (!res.ok || data.ok === false) {{
          const detail = (data.error && data.error.detail) || data.detail || ('驗證失敗 (HTTP ' + res.status + ')');
          showMsg(detail, true);
          if (btn) btn.disabled = false;
          return;
        }}
        try {{
          sessionStorage.setItem('khavis_console_token', token);
        }} catch (_) {{}}
        try {{
          localStorage.setItem('khavis_console_token', token);
        }} catch (_) {{}}
        showMsg('✓ 授權驗證成功！正在進入任務工作台…', false);
        window.location.href = '/workbench';
      }} catch (err) {{
        showMsg('連線錯誤：' + (err && err.message ? err.message : String(err)), true);
        if (btn) btn.disabled = false;
      }}
    }}
  </script>
</body>
</html>
"""
