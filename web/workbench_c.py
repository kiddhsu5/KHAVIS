"""K.H.A.V.I.S. Workbench — 選項 C：任務卡片流 (Task-Stream) 介面。

設計目標
--------
1. 訊息減量：把原本「永遠展開」的工作區列、拓撲畫布、模型設定、自訂 Agent
   表單全部收進可摺疊面板，預設關閉。
2. 任務優先：主畫面是一條由新到舊的任務卡片流，每張卡片自帶狀態、
   參與 Agent、耗時、檔案變更與成果摘要。
3. 執行可見：執行中的卡片即時顯示階段進度與主控台串流，不必捲到頁底。
4. 莫蘭迪配色：低飽和霧感底色 + 單一強調色，避免原本高對比霓虹造成的視覺噪音。

實作方式
--------
本模組**重組** `web.workbench` 既有的 markup（以 marker 切片）並沿用其
client-side JS，因此所有 API 呼叫、狀態管理與後端契約完全不變；外層版面
（側欄 / 任務流 / composer）則是全新的。

已驗證 `_WORKBENCH_PAGE_HEAD` 重構後輸出與線上頁面 byte-identical。
"""

from __future__ import annotations

import re
from typing import Final

from .workbench import _WORKBENCH_CLIENT_JS, _WORKBENCH_PAGE_HEAD

__all__ = ["render_workbench_html_c"]

# ── markup 切片工具 ────────────────────────────────────────────────────────
_LINES: Final[list[str]] = _WORKBENCH_PAGE_HEAD.split("\n")

# 1-indexed inclusive slices into _WORKBENCH_PAGE_HEAD (verified by the
# contract check in tests/test_workbench.py).
# 注意：行號與 legacy markup 綁定。在 workbench.py 的拓撲／views 區塊增刪行
# 之後，必須同步更新這張表（insertion after line 648 → 全部 +N）。
# 注意：行號與 legacy markup 綁定；在 workbench.py 的拓撲／views 區塊
# 增刪行之後必須同步更新這張表。
_SLICES: Final[dict[str, tuple[int, int]]] = {
    # 工作區 + Git worktree/diff 主控台
    "workspace": (250, 327),
    # 拓撲畫布 + Agent 節點 + 協同模式 + 任務範本 (不含輸入框, 見 composer)
    "topology": (329, 702),
    # 快速範本 + textarea + 能力路由 + 派工按鈕
    "composer": (703, 756),
    # 執行面板：狀態列、pipeline stepper、延遲圖、成果容器
    "result": (764, 850),
    # VIEW 2-5：交付文件 / 額度熔斷 / Agent 集群 / 部署精靈
    "views": (854, 984),
    # 產物預覽抽屜
    "drawers": (989, 1012),
}


def _slice(name: str) -> str:
    """Return a 1-indexed inclusive slice of the legacy markup."""
    start, end = _SLICES[name]
    fragment = "\n".join(_LINES[start - 1 : end])
    if name == "composer":
        # The legacy composer closes three wrappers that are not part of the
        # extracted C-page fragment; the C chrome owns those wrappers.
        for _ in range(3):
            fragment = re.sub(r"\n\s*</div>\s*$", "", fragment)
    return fragment


def _rebalance(html: str, extra_open: int = 0) -> str:
    """Close any <div>s left dangling by a partial slice.

    Slicing markup out of a deeply nested tree is inherently fragile; rather
    than hand-tuning boundaries we count the imbalance and append the closers
    needed so the resulting fragment is well-formed.
    """
    opened = len(re.findall(r"<div\b", html)) + extra_open
    closed = len(re.findall(r"</div>", html))
    return html + ("\n" + "</div>" * (opened - closed) if opened > closed else "")


def _legacy_style() -> str:
    """Reuse the original stylesheet (.agent-node-card, .check-indicator, ...).

    The topology panel markup still depends on those class hooks, so we keep
    them and layer the Morandi palette on top.
    """
    m = re.search(r"<style>(.*?)</style>", _WORKBENCH_PAGE_HEAD, re.S)
    return m.group(1) if m else ""


# ── 莫蘭迪設計語言 ─────────────────────────────────────────────────────────
_MORANDI_CSS: Final[str] = """
/* ══════════════════════════════════════════════════════════════════
   MORANDI DESIGN LANGUAGE — 低飽和霧感基底 + 單一強調色
   霧霾藍 #7C9BB5 為主色；灰綠 / 藕荷 / 豆沙 / 杏桃 為狀態跳色
   ══════════════════════════════════════════════════════════════════ */
:root{
  --m-mist:#7C9BB5;  --m-mist-soft:rgba(124,155,181,.16);
  --m-sage:#8FA894;  --m-sage-soft:rgba(143,168,148,.16);
  --m-lilac:#A99AB5; --m-lilac-soft:rgba(169,154,181,.16);
  --m-rose:#BC8B8B; --m-rose-soft:rgba(188,139,139,.16);
  --m-apricot:#C9A17E; --m-apricot-soft:rgba(201,161,126,.16);
  --m-bone:#E4E2DE;  --m-dim:#9A9A9E;  --m-faint:#6E6E74;
  --m-line:rgba(228,226,222,.07);
  --m-ink-900:#16181D; --m-ink-800:#1C1E25;
  --m-ink-700:#23262F; --m-ink-600:#2B2F3A;
  --m-on-accent:#16181D;
  --m-rose-text:#D2A5A5;
  /* 半透明面板底色（composer 外框 / 卡片內文 / 主控台 / 淡色底） */
  --m-panel:rgba(22,24,29,.82);
  --m-panel-2:rgba(22,24,29,.5);
  --m-panel-3:rgba(22,24,29,.65);
  --m-faint-bg:rgba(228,226,222,.05);
  --m-track:rgba(228,226,222,.06);
}

html{font-size:17px}
body{
  background:
    radial-gradient(1200px 620px at 12% -12%, rgba(124,155,181,.055), transparent 60%),
    radial-gradient(900px 520px at 100% 0%, rgba(169,154,181,.045), transparent 55%),
    var(--m-ink-900);
  color:var(--m-bone);
}

/* 卡片採霧面而非純黑，帶一點暖色，避免死黑造成的廉價感 */
.frost{background:linear-gradient(180deg,rgba(43,47,58,.55),rgba(35,38,47,.78))}
.frost-2{background:linear-gradient(180deg,rgba(35,38,47,.7),rgba(28,30,37,.85))}

.m-line{border:1px solid var(--m-line)}
.m-scroll::-webkit-scrollbar{width:7px;height:7px}
.m-scroll::-webkit-scrollbar-track{background:transparent}
.m-scroll::-webkit-scrollbar-thumb{background:rgba(228,226,222,.10);border-radius:8px}
.m-scroll::-webkit-scrollbar-thumb:hover{background:rgba(228,226,222,.18)}

.m-raise{box-shadow:0 1px 2px rgba(0,0,0,.45),0 12px 32px -16px rgba(0,0,0,.75)}

@keyframes mPulse{0%,100%{opacity:1}50%{opacity:.28}}
.m-live{animation:mPulse 1.6s ease-in-out infinite}
@keyframes mBeam{to{stroke-dashoffset:-32}}
.m-beam{stroke-dasharray:5 5;animation:mBeam 1s linear infinite}
@keyframes mIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.m-in{animation:mIn .34s cubic-bezier(.16,1,.3,1) both}

.m-chip{border-radius:.5rem;font-size:11px;padding:.125rem .5rem;border:1px solid}

/* 主按鈕：霧霾藍實心，畫面中唯一的高對比元素，用來導引視線 */
.m-btn-primary{background:var(--m-mist);color:var(--m-on-accent)}
.m-btn-primary:hover:not(:disabled){background:#8FACBF}
.m-btn-primary:disabled{opacity:.45;cursor:not-allowed}

.m-btn-ghost{background:rgba(228,226,222,.05);color:var(--m-dim);border:1px solid var(--m-line)}
.m-btn-ghost:hover{background:rgba(228,226,222,.09);color:var(--m-bone)}

/* 側欄導覽（取代原本的 sky-500 選中態） */
.m-nav{color:var(--m-dim);transition:background .15s,color .15s}
.m-nav:hover{background:rgba(228,226,222,.04);color:var(--m-bone)}
.m-nav-active{background:var(--m-mist-soft);color:#8FACBF}

.m-section-label{font-size:10px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--m-faint)}

/* 摺疊面板：預設收起，這是「訊息太多」的主要解方 */
.m-collapse > summary{list-style:none;cursor:pointer}
.m-collapse > summary::-webkit-details-marker{display:none}
.m-collapse[open] > summary .m-caret{transform:rotate(90deg)}
.m-caret{transition:transform .18s ease;color:var(--m-faint)}

/* composer 內的輸入框聚焦光暈 */
#prompt-input:focus{outline:none;box-shadow:none}
.composer:focus-within{border-color:rgba(124,155,181,.45)}

/* 讓舊拓撲面板在莫蘭迪底色下不突兀 */
.agent-node-card{background:linear-gradient(180deg,rgba(43,47,58,.5),rgba(28,30,37,.7))}
.agent-node-card.selected{border-color:rgba(124,155,181,.6)!important;box-shadow:0 0 22px -6px rgba(124,155,181,.35)}
.agent-node-card.running{border-color:rgba(143,168,148,.7)!important;box-shadow:0 0 24px -4px rgba(143,168,148,.4)}
.check-indicator{color:var(--m-faint)}

textarea,input,select{color-scheme:dark}
textarea::placeholder,input::placeholder{color:var(--m-faint)}

/* ══════════════════════════════════════════════════════════════════
   淺色主題 — 莫蘭迪的淺底變體
   原本只切 <html class>，但 legacy 的 !important 規則把 shell 塗成白色，
   文字色卻仍是深色版的 var(--m-dim)/var(--m-bone) → 側欄文字近乎不可見。
   這裡補上完整的淺色變數與元件覆寫。
   ══════════════════════════════════════════════════════════════════ */
html:not(.dark){
  --m-mist:#5C7F9C;   --m-mist-soft:rgba(92,127,156,.14);
  --m-sage:#61836A;   --m-sage-soft:rgba(97,131,106,.14);
  --m-lilac:#806F8E;  --m-lilac-soft:rgba(128,111,142,.14);
  --m-rose:#9E6D6D;   --m-rose-soft:rgba(158,109,109,.14);
  --m-apricot:#8A6A44; --m-apricot-soft:rgba(138,106,68,.14);
  --m-bone:#1B2330;   --m-dim:#57616F;  --m-faint:#8B95A4;
  --m-line:rgba(27,35,48,.12);
  --m-ink-900:#EDF0F4; --m-ink-800:#FFFFFF; --m-ink-700:#F2F5F9; --m-ink-600:#E6EBF2;
  --m-on-accent:#FFFFFF;
  --m-rose-text:#8C4A4A;
  --m-panel:rgba(255,255,255,.9);
  --m-panel-2:rgba(236,240,245,.9);
  --m-panel-3:rgba(240,244,248,.95);
  --m-faint-bg:rgba(27,35,48,.05);
  --m-track:rgba(27,35,48,.08);
}
html:not(.dark) body{
  background:
    radial-gradient(1200px 620px at 12% -12%, rgba(92,127,156,.10), transparent 60%),
    radial-gradient(900px 520px at 100% 0%, rgba(128,111,142,.08), transparent 55%),
    var(--m-ink-900) !important;
  color:var(--m-bone) !important;
}
/* main 保持透明，讓 body 的漸層透出來；header / aside 則是實心面板 */
html:not(.dark) header{background-color:var(--m-ink-800) !important;color:var(--m-bone) !important;border-color:var(--m-line) !important}
html:not(.dark) aside{background-color:var(--m-ink-800) !important;color:var(--m-bone) !important;border-color:var(--m-line) !important}
html:not(.dark) main{background-color:transparent !important;color:var(--m-bone) !important}
html:not(.dark) #artifact-drawer,
html:not(.dark) #drawer-content{background-color:var(--m-ink-800) !important;color:var(--m-bone) !important;border-color:var(--m-line) !important}
html:not(.dark) .frost{background:linear-gradient(180deg,rgba(255,255,255,.97),rgba(240,243,248,.94)) !important}
html:not(.dark) .frost-2{background:linear-gradient(180deg,#fff,#f3f6fa) !important}
html:not(.dark) .m-btn-ghost{background:rgba(27,35,48,.05);color:var(--m-dim);border-color:var(--m-line)}
html:not(.dark) .m-btn-ghost:hover{background:rgba(27,35,48,.09);color:var(--m-bone)}
html:not(.dark) .m-nav-active{background:var(--m-mist-soft);color:#3F6280}
html:not(.dark) .m-section-label{color:#94A0B0}
html:not(.dark) textarea,
html:not(.dark) select,
html:not(.dark) input{background-color:#fff !important;color:var(--m-bone) !important;border-color:var(--m-line) !important;color-scheme:light}
html:not(.dark) textarea::placeholder,
html:not(.dark) input::placeholder{color:#9AA5B4}
html:not(.dark) .m-scroll::-webkit-scrollbar-thumb{background:rgba(27,35,48,.16)}
html:not(.dark) .m-scroll::-webkit-scrollbar-thumb:hover{background:rgba(27,35,48,.28)}
html:not(.dark) .c-toast{background:linear-gradient(180deg,rgba(255,255,255,.98),rgba(243,246,250,.98));color:var(--m-bone);box-shadow:0 12px 30px -14px rgba(27,35,48,.35)}

/* legacy 的執行面板用 bg-white/5 與深色漸層，淺色底下會變成白底白框 */
html:not(.dark) #execution-panel div[class*="bg-white/5"],
html:not(.dark) #live-results-container div[class*="bg-white/5"],
html:not(.dark) #execution-panel div[class*="bg-[#050811]"],
html:not(.dark) #live-results-container div[class*="bg-[#050811]"]{
  background:rgba(27,35,48,.045) !important;
}
html:not(.dark) #live-results-container > div{
  background:linear-gradient(180deg,rgba(236,253,245,.9),rgba(255,255,255,.96)) !important;
  border-color:rgba(16,120,70,.28) !important;
}

/* ══════════════════════════════════════════════════════════════════
   響應式 — 窄視窗改抽屜式側欄
   原本 aside 固定 w-60(240px)，在 471px 的視窗下主欄只剩 225px，
   任務卡的標題被壓到 0 寬度而完全看不見。
   ══════════════════════════════════════════════════════════════════ */
#c-sidebar{transition:transform .24s cubic-bezier(.16,1,.3,1)}
#c-menu-btn{display:none}
#c-sidebar-close{display:none}
#c-scrim{
  position:fixed;inset:0;z-index:45;background:rgba(8,10,14,.55);
  opacity:0;pointer-events:none;transition:opacity .2s ease;
}
#c-scrim.c-on{opacity:1;pointer-events:auto}
@media (min-width:901px){
  /* 寬螢幕沒有抽屜模式，遮罩必須完全隱形，否則會擋住整個頁面 */
  #c-scrim{display:none !important}
}
@media (max-width:900px){
  #c-menu-btn{display:inline-flex}
  #c-sidebar-close{display:grid}
  #c-sidebar{
    position:fixed;top:0;bottom:0;left:0;z-index:50;width:258px;
    transform:translateX(-100%);
    box-shadow:0 18px 50px -12px rgba(0,0,0,.6);
  }
  #c-sidebar.c-open{transform:none}
}
/* 主欄與卡片都不允許被內容撐破 */
#c-main{min-width:0}
.c-card{min-width:0}

/* ══════════════════════════════════════════════════════════════════
   任務卡版面
   ══════════════════════════════════════════════════════════════════ */
.c-card-title{
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden;word-break:break-word;
}
.c-meta-line{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.c-caret{transition:transform .18s ease}
.c-caret-open{transform:rotate(180deg)}
.c-action{font-size:12.5px;padding:.275rem .675rem;border-radius:.5rem;transition:.15s}

/* 真實進度：後端 dispatch 是單次阻塞請求，沒有階段回報，
   所以用「不確定進度條 + 真實經過時間」取代假的百分比。 */
@keyframes mShimmer{0%{transform:translateX(-110%)}100%{transform:translateX(340%)}}
.m-indeterminate{position:relative;overflow:hidden}
.m-indeterminate::after{
  content:'';position:absolute;top:0;bottom:0;left:0;width:42%;
  background:linear-gradient(90deg,transparent,rgba(143,168,148,.6),transparent);
  animation:mShimmer 1.6s ease-in-out infinite;
}
@keyframes mDot{0%,80%,100%{opacity:.25;transform:scale(.8)}40%{opacity:1;transform:scale(1)}}
.m-dot{display:inline-block;width:4px;height:4px;border-radius:50%;background:var(--m-mist);
  animation:mDot 1.1s ease-in-out infinite}
.m-dot:nth-child(2){animation-delay:.15s}
.m-dot:nth-child(3){animation-delay:.3s}

/* ══════════════════════════════════════════════════════════════════
   Toast（取代會阻塞流程的 browser alert）
   ══════════════════════════════════════════════════════════════════ */
#c-toast-host{
  /* 放右上角（header 下方）：放右下角會蓋住派工鈕這個最重要控制項 */
  position:fixed;z-index:60;right:16px;top:56px;
  display:flex;flex-direction:column;gap:8px;pointer-events:none;max-width:min(380px,calc(100vw - 32px));
}
.c-toast{
  pointer-events:auto;padding:10px 14px;border-radius:12px;font-size:12px;line-height:1.55;
  background:linear-gradient(180deg,rgba(43,47,58,.97),rgba(28,30,37,.97));
  border:1px solid var(--m-line);color:var(--m-bone);
  box-shadow:0 12px 30px -12px rgba(0,0,0,.7);
  animation:mIn .22s cubic-bezier(.16,1,.3,1) both;
}
.c-toast.c-err{border-color:rgba(188,139,139,.5);color:var(--m-rose-text)}
.c-toast.c-ok{border-color:rgba(143,168,148,.5);color:#C2D6C5}

/* 派工鈕沿用 legacy 的 markup（id 固定）：改為品牌漸層、佔滿整行置中，
   消除按鈕右側的空白區域。 */
#btn-dispatch{
  width:100% !important;
  justify-content:center !important;
  background:var(--v-brand-grad) !important;
  background-image:var(--v-brand-grad) !important;
  color:#fff !important;
  border:0 !important;
  box-shadow:0 10px 26px -12px rgba(124,155,181,.8) !important;
  font-weight:700;
}
#btn-dispatch:hover:not(:disabled){
  background:#8FACBF !important;
  background-image:linear-gradient(135deg,#8FACBF 0%,#A3BCA6 100%) !important;
  box-shadow:0 10px 26px -10px rgba(124,155,181,.75) !important;
}
#btn-dispatch:disabled{opacity:.45 !important;cursor:not-allowed}
#btn-dispatch i{color:currentColor !important}

/* ══════════════════════════════════════════════════════════════════
   中央對話串檢視（左右雙邊對話：右邊用戶提問 / 左邊 Agent 回覆 + 可折疊交叉審查）
   ══════════════════════════════════════════════════════════════════ */
.c-conv-header{
  display:flex;align-items:center;gap:.625rem;flex-wrap:wrap;
  padding:.5rem .75rem;border-radius:12px;
  background:var(--m-panel-2);border:1px solid var(--m-line);
}
.c-conv-title{font-size:15px;font-weight:600;color:var(--m-bone)}
.c-turn{
  margin-top:1.1rem;padding:1rem;border-radius:16px;
  background:linear-gradient(180deg,rgba(35,38,47,.42),rgba(22,24,29,.55));
  border:1px solid var(--m-line);
  box-shadow:0 6px 20px -12px rgba(0,0,0,.45);
  display:flex;flex-direction:column;gap:.9rem;
}
.c-turn-divider{
  display:flex;align-items:center;gap:.5rem;
  font-size:11px;font-family:ui-monospace,monospace;color:var(--m-faint);
  padding-bottom:.4rem;border-bottom:1px dashed var(--m-line);
}
.c-turn-label{
  font-size:12px;font-weight:600;letter-spacing:.04em;
  color:var(--m-faint);margin-bottom:.375rem;display:flex;align-items:center;gap:.45rem;
  flex-wrap:wrap;
}
.c-turn-label .c-conn-badge{text-transform:none;white-space:nowrap}

/* 右側：用戶提問 (User Prompt on RIGHT) */
.c-turn-user{
  align-self:flex-end;width:fit-content;max-width:84%;
  display:flex;flex-direction:column;align-items:flex-end;
}
.c-turn-user .c-turn-label{justify-content:flex-end}
.c-turn-user .c-turn-bubble{
  border:1px solid rgba(124,155,181,.38);
  border-right:3px solid var(--m-mist);
  padding:.7rem 1rem;border-radius:16px 4px 16px 16px;
  background:var(--m-mist-soft);color:var(--m-bone);font-size:14.5px;line-height:1.7;
  white-space:pre-wrap;word-break:break-word;text-align:left;
}

/* 左側：Agent 回覆 (Agent Result on LEFT) */
.c-turn-agent{
  align-self:flex-start;width:100%;max-width:92%;
  display:flex;flex-direction:column;align-items:flex-start;
}
.c-turn-agent .c-turn-bubble{
  width:100%;
  border:1px solid var(--m-line);
  border-left:3px solid var(--m-sage);
  border-radius:4px 16px 16px 16px;padding:.9rem 1.05rem;
  background:var(--m-panel-2);color:var(--m-bone);font-size:14.5px;line-height:1.75;
  white-space:pre-wrap;word-break:break-word;max-height:30rem;overflow-y:auto;
}

/* Agent 交叉審查與比較（預設收起，點擊展開） */
.c-review-collapse{
  width:100%;margin-top:.55rem;border-radius:12px;
  border:1px solid rgba(201,161,126,.28);
  background:rgba(201,161,126,.06);overflow:hidden;
}
.c-review-collapse > summary{
  list-style:none;cursor:pointer;padding:.5rem .8rem;
  display:flex;align-items:center;justify-content:space-between;gap:.5rem;
  font-size:12px;font-weight:600;color:var(--m-apricot);
  user-select:none;transition:background .15s;
}
.c-review-collapse > summary::-webkit-details-marker{display:none}
.c-review-collapse > summary:hover{background:rgba(201,161,126,.12)}
.c-review-body{
  padding:.75rem .9rem;border-top:1px solid rgba(201,161,126,.2);
  font-size:12.5px;font-family:ui-monospace,monospace;line-height:1.65;
  color:var(--m-dim);white-space:pre-wrap;word-break:break-word;
  max-height:20rem;overflow-y:auto;background:var(--m-panel-3);
}
html:not(.dark) .c-turn{
  background:linear-gradient(180deg,rgba(255,255,255,.95),rgba(242,245,249,.92));
}

/* 成果文件：位於「以此任務接續」與「專案檔案 Diff」之間、預設展示的 Output 產出文檔下載與預覽欄 */
.c-turn-footer-flow{width:100%;margin-top:.65rem;display:flex;flex-direction:column;gap:.55rem}
.c-output-download-panel{
  width:100%;border-radius:12px;padding:.65rem .8rem;
  border:1px solid rgba(143,168,148,.42);
  background:linear-gradient(180deg,rgba(143,168,148,.09),rgba(35,38,47,.65));
  display:flex;flex-direction:column;gap:.45rem;
}
html:not(.dark) .c-output-download-panel{
  background:linear-gradient(180deg,rgba(97,131,106,.08),rgba(255,255,255,.95));
}
.c-output-panel-head{
  display:flex;align-items:center;justify-content:space-between;gap:.5rem;flex-wrap:wrap;
  padding-bottom:.35rem;border-bottom:1px dashed rgba(143,168,148,.28);
}
.c-turn-files{width:100%;display:flex;flex-direction:column;gap:.4rem}
.c-turn-files-title{font-size:12.5px;font-weight:700;color:var(--m-sage);letter-spacing:.02em;display:flex;align-items:center;gap:.4rem}
.c-dl-btn{
  display:inline-flex;align-items:center;gap:.3rem;
  font-size:12px;font-weight:600;padding:.24rem .65rem;border-radius:.45rem;
  background:rgba(143,168,148,.22);color:#CBE2CF;
  border:1px solid rgba(143,168,148,.5);text-decoration:none;transition:.15s;flex-shrink:0;
}
.c-dl-btn:hover{background:var(--m-sage);color:var(--m-on-accent)}
html:not(.dark) .c-dl-btn{background:rgba(97,131,106,.16);color:#35533D;border-color:rgba(97,131,106,.4)}
html:not(.dark) .c-dl-btn:hover{background:var(--m-sage);color:#fff}
.c-file{
  width:100%;border-radius:10px;border:1px solid var(--m-line);
  background:var(--m-panel-2);overflow:hidden;
}
.c-file > summary{
  list-style:none;cursor:pointer;padding:.45rem .7rem;
  display:flex;align-items:center;gap:.45rem;user-select:none;
  transition:background .15s;flex-wrap:wrap;
}
.c-file > summary::-webkit-details-marker{display:none}
.c-file > summary:hover{background:rgba(143,168,148,.10)}
.c-file[open] > summary{border-bottom:1px solid var(--m-line)}
.c-file-badge{
  font-size:10.5px;font-family:ui-monospace,monospace;font-weight:600;padding:.05rem .4rem;
  border-radius:5px;background:var(--m-sage-soft);color:var(--m-sage);flex-shrink:0;
}
.c-file-name{
  flex:1;min-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  font-size:12.5px;font-family:ui-monospace,monospace;color:var(--m-bone);
}
.c-file-size{font-size:11px;font-family:ui-monospace,monospace;color:var(--m-faint);flex-shrink:0}
.c-file-body{
  margin:0;padding:.75rem .9rem;font-size:12.5px;line-height:1.65;
  font-family:ui-monospace,monospace;color:var(--m-dim);background:var(--m-panel-3);
  white-space:pre-wrap;word-break:break-word;max-height:22rem;overflow-y:auto;
}

/* 進階明細（Agent 名稱、耗時、交叉審查、各節點輸出）預設不顯示 */
.c-adv-only{display:none !important}
body.c-adv .c-adv-only{display:block !important}
.c-turn-actions{display:flex;flex-wrap:wrap;align-items:center;gap:.45rem}
.c-live-slot:empty{display:none}
.c-live-slot{margin-top:.875rem}
.c-conn-badge{
  display:inline-flex;align-items:center;gap:.25rem;
  padding:.1rem .45rem;border-radius:6px;font-size:11.5px;font-family:ui-monospace,monospace;
}

/* 鍵盤可及性：主要可互動元件給出明確焦點環 */
:focus-visible{outline:2px solid var(--m-mist);outline-offset:2px;border-radius:6px}

/* ══════════════════════════════════════════════════════════════════
   MiniMax Code 風格的項目清單
   取自 MiniMax Code renderer 的真實 token：
     --mavis-radius-control: 8px
     --bg_interaction_tertiary_selected = white 8%（深）/ black 4%（淺）
     --border_default             = white 8%（深）/ black 8%（淺）
   ══════════════════════════════════════════════════════════════════ */
.c-item{
  display:block;width:100%;text-align:left;
  padding:.375rem .5rem;border-radius:8px;
  color:var(--m-dim);
  transition:background-color .16s ease,color .16s ease;
}
.c-item:hover{background:rgba(228,226,222,.05);color:var(--m-bone)}
.c-item.c-item-sel{background:var(--m-mist-soft);color:var(--m-bone)}
.c-item-title{color:inherit}
.c-item-time{color:var(--m-faint);opacity:.8;transition:opacity .16s ease}
.c-item:hover .c-item-time{opacity:1}
.c-item-meta{color:var(--m-faint);opacity:.7}
.c-item-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}

.c-group-head{
  display:flex;align-items:center;gap:.375rem;
  padding:.5rem .5rem .1875rem;
  font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;
  color:var(--m-faint);
}
.c-group-count{margin-left:auto;font-size:10px;letter-spacing:0;font-family:ui-monospace,monospace}

.c-menu{
  position:absolute;z-index:30;left:0;right:0;min-width:190px;
  border-radius:10px;padding:4px;
  background:var(--m-panel);
  border:1px solid var(--m-line);
  box-shadow:0 12px 30px -12px rgba(0,0,0,.6);
  backdrop-filter:blur(12px);
}
.c-menu-item{
  display:flex;align-items:center;gap:.5rem;width:100%;
  padding:.375rem .5rem;border-radius:8px;
  font-size:12px;color:var(--m-dim);text-align:left;
  transition:background-color .16s ease,color .16s ease;
}
.c-menu-item:hover{background:rgba(228,226,222,.06);color:var(--m-bone)}
.c-menu-item.c-on{color:var(--m-mist)}
.c-foot-chip{
  padding:.125rem .375rem;border-radius:6px;
  color:var(--m-faint);transition:background-color .16s ease,color .16s ease;
}
.c-foot-chip:hover{background:rgba(228,226,222,.06);color:var(--m-bone)}

#c-list-search{outline:none}
#c-list-search:focus{border-color:rgba(124,155,181,.55)}

/* topbar 的次要資訊帶（配額晶片 / 模型名）——寬度門檻自訂，
   不用 Tailwind 的 breakpoint，避免 CDN 行為不確定導致 topbar 換行溢出。 */
.c-strip{display:none}
@media (min-width:1400px){ #quota-capsule-strip{display:flex} }
/* 配額帶的晶片由 legacy 產生（emerald/indigo/sky/teal），
   這裡換成莫蘭迪色階，讓整頁只有一套配色語言。 */
#quota-capsule-strip{flex-shrink:1;min-width:0}
#quota-capsule-strip span{border-radius:6px;white-space:nowrap;flex-shrink:0}
#quota-capsule-strip span:nth-child(1){background:var(--m-mist-soft)!important;color:var(--m-mist)!important;border-color:rgba(124,155,181,.3)!important}
#quota-capsule-strip span:nth-child(2){background:var(--m-lilac-soft)!important;color:var(--m-lilac)!important;border-color:rgba(169,154,181,.3)!important}
#quota-capsule-strip span:nth-child(3){background:var(--m-sage-soft)!important;color:var(--m-sage)!important;border-color:rgba(143,168,148,.3)!important}
#quota-capsule-strip span:nth-child(4){background:var(--m-apricot-soft)!important;color:var(--m-apricot)!important;border-color:rgba(201,161,126,.3)!important}

/* ══════════════════════════════════════════════════════════════════
   三欄七宮格工作台版面 (左3: 歷史談話/資料庫/額度設定 | 中2: 對話串/輸入框 | 右2: Agent狀態/Using檔案)
   ══════════════════════════════════════════════════════════════════ */
.c-zone-header{
  display:flex;align-items:center;gap:.5rem;
  padding:.55rem .75rem;border-bottom:1px solid var(--m-line);
  background:var(--m-panel-2);flex-shrink:0;
}
.c-zone-title{
  font-size:12px;font-weight:700;letter-spacing:.04em;
  color:var(--m-bone);display:inline-flex;align-items:center;gap:.4rem;
}
.c-zone-tag{
  font-size:10px;font-family:ui-monospace,monospace;
  padding:.05rem .4rem;border-radius:5px;
  background:var(--m-mist-soft);color:var(--m-mist);
  border:1px solid rgba(124,155,181,.28);
}

/* 右側欄：桌面常駐顯示為第三欄（上：Agent 狀態 / 下：Using 檔案），窄視窗為覆蓋抽屜 */
.c-drawer{
  width:370px;flex-shrink:0;overflow:hidden;
  border-left:1px solid var(--m-line);
}
@media (min-width:1280px){
  .c-drawer{width:390px}
}
@media (max-width:1080px){
  .c-drawer{
    position:fixed;top:0;right:0;bottom:0;z-index:45;
    width:380px;max-width:92vw;
    transform:translateX(100%);
    transition:transform .22s cubic-bezier(.16,1,.3,1);
    box-shadow:-18px 0 50px -12px rgba(0,0,0,.55);
  }
  .c-drawer.c-open{transform:none}
}
#c-drawer-scrim{display:none}
@media (max-width:1080px){
  #c-drawer-scrim{
    position:fixed;inset:0;z-index:44;background:rgba(8,10,14,.5);
    opacity:0;pointer-events:none;transition:opacity .2s ease;
    display:block;
  }
  #c-drawer-scrim.c-on{opacity:1;pointer-events:auto}
}

/* 右下 Using 檔案區：讓 legacy workspace-control-bar 適配窄欄常駐顯示 */
#c-using-files-host #workspace-control-bar{
  background:transparent !important;border:none !important;padding:.5rem !important;
  box-shadow:none !important;
}
#c-using-files-host #workspace-diff-panel{
  display:flex !important;flex-direction:column !important;
  gap:.625rem !important;padding-top:.5rem !important;
}
#c-using-files-host #workspace-diff-panel > div{
  width:100% !important;
}
#c-using-files-host #ws-files-list{
  max-height:11rem !important;
}
#c-using-files-host #ws-diff-viewer{
  max-height:9rem !important;
}
html:not(.dark) .c-item:hover{background:rgba(27,35,48,.045)}
html:not(.dark) .c-menu-item:hover{background:rgba(27,35,48,.05)}
html:not(.dark) .c-foot-chip:hover{background:rgba(27,35,48,.05)}
"""

# ══════════════════════════════════════════════════════════════════════
# VIVID LAYER — 繽紛色彩層：在莫蘭迪基底上加入高彩度功能色
# 導航 / 協同模式 / 文檔類型 / 對話串歸檔 / 全頁視圖 各有專屬色相
# ══════════════════════════════════════════════════════════════════════
_VIVID_CSS: Final[str] = """
/* Morandi 色版：所有 v-* 功能色都映射回莫蘭迪基底 */
:root{
  --v-violet:#A99AB5; --v-sky:#7C9BB5; --v-emerald:#8FA894; --v-amber:#C9A17E;
  --v-rose:#BC8B8B; --v-cyan:#7C9BB5; --v-blue:#A99AB5; --v-orange:#C9A17E;
  --v-brand-grad:linear-gradient(135deg,#7C9BB5 0%,#8FA894 100%);
  --v-doc-docx:#9FB3C4; --v-doc-pdf:#C4A6A6; --v-doc-md:#A3BCA6; --v-doc-xlsx:#AECBAD;
  --v-doc-pptx:#D0B394; --v-doc-zip:#B3A6C4; --v-doc-html:#D6C49A; --v-doc-default:#8D99A6;
}

/* ── 品牌漸層 logo ── */
.v-brand-mark{
  background:var(--v-brand-grad);
  box-shadow:0 4px 14px -5px rgba(124,155,181,.6);
}
.v-brand-name{
  background:var(--v-brand-grad);
  -webkit-background-clip:text;background-clip:text;color:transparent;
}

/* ── 側欄導航：每項專屬色相 ── */
.v-nav{position:relative;display:flex;align-items:center;gap:.625rem;width:100%;
  padding:.5rem .625rem;border-radius:.65rem;border:1px solid transparent;
  color:var(--m-dim);transition:all .18s ease;text-align:left}
.v-nav:hover{background:var(--m-faint-bg);color:var(--m-bone)}
.v-nav.v-on{color:var(--m-bone);background:var(--m-faint-bg);border-color:var(--m-line)}
.v-nav .v-nav-ic{width:1.9rem;height:1.9rem;border-radius:.55rem;display:grid;place-items:center;flex-shrink:0;
  color:#16181D;font-size:.72rem}
.v-nav.v-on .v-nav-ic{box-shadow:0 3px 10px -3px currentColor}
.v-nav .v-nav-dot{margin-left:auto;width:.4rem;height:.4rem;border-radius:9999px;opacity:0;transition:opacity .18s}
.v-nav.v-on .v-nav-dot{opacity:1}
.v-ic-command{background:linear-gradient(135deg,#9FB3C4,#7C9BB5)}
.v-ic-agents{background:linear-gradient(135deg,#A3BCA6,#8FA894)}
.v-ic-db{background:linear-gradient(135deg,#D0B394,#C9A17E)}
.v-ic-quota{background:linear-gradient(135deg,#C4A6A6,#BC8B8B)}
.v-ic-new{background:var(--v-brand-grad);color:#fff}

/* ── 對話串列表：專案分組色點 ── */
.v-proj-dot{width:.5rem;height:.5rem;border-radius:9999px;flex-shrink:0}

/* ── 協同模式按鈕：各自色彩 ── */
.c-collab-btn[data-mode="auto_loop"]{--mode-c:#8FA894}
.c-collab-btn[data-mode="relay"]{--mode-c:#7C9BB5}
.c-collab-btn[data-mode="critique"]{--mode-c:#BC8B8B}
.c-collab-btn[data-mode="parallel"]{--mode-c:#C9A17E}
.c-collab-btn.m-btn-primary{background:color-mix(in srgb,var(--mode-c,#7C9BB5) 22%,transparent)!important;
  color:var(--mode-c,#7C9BB5)!important;border:1px solid color-mix(in srgb,var(--mode-c,#7C9BB5) 55%,transparent)!important;
  box-shadow:0 0 12px -4px color-mix(in srgb,var(--mode-c,#7C9BB5) 60%,transparent)}
.c-collab-btn:not(.m-btn-primary):hover{border-color:color-mix(in srgb,var(--mode-c,#7C9BB5) 40%,transparent);color:var(--mode-c,#7C9BB5)}

/* ── 文檔類型徽章：依副檔名上色 ── */
.v-doc-badge{display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;
  min-width:2.6rem;padding:.1rem .4rem;border-radius:.4rem;font-family:'JetBrains Mono',ui-monospace,monospace;
  font-size:.6rem;font-weight:800;letter-spacing:.04em;color:#16181D}
.v-doc-badge[data-ext="DOCX"]{background:var(--v-doc-docx)} .v-doc-badge[data-ext="PDF"]{background:var(--v-doc-pdf)}
.v-doc-badge[data-ext="MD"]{background:var(--v-doc-md)}   .v-doc-badge[data-ext="XLSX"]{background:var(--v-doc-xlsx)}
.v-doc-badge[data-ext="CSV"]{background:var(--v-doc-xlsx)} .v-doc-badge[data-ext="PPTX"]{background:var(--v-doc-pptx)}
.v-doc-badge[data-ext="ZIP"]{background:var(--v-doc-zip)}  .v-doc-badge[data-ext="HTML"]{background:var(--v-doc-html)}
.v-doc-badge[data-ext="PY"]{background:var(--v-doc-html)}  .v-doc-badge[data-ext="JSON"]{background:var(--v-doc-html)}
.v-doc-badge:not([data-ext]){background:var(--v-doc-default)}
.v-doc-badge[data-ext=""]:not([data-ext="DOCX"]):not([data-ext="PDF"]){background:var(--v-doc-default)}

/* ── 對話串內：本輪產出文檔（內聯清單，如附圖 1 樣式） ── */
.v-turn-docs{margin-top:.5rem;border:1px solid var(--m-line);border-radius:.7rem;
  background:var(--m-panel-2);overflow:hidden}
.v-turn-docs-head{display:flex;align-items:center;gap:.4rem;padding:.4rem .65rem;
  font-size:.66rem;color:var(--m-faint);border-bottom:1px solid var(--m-line);
  background:color-mix(in srgb,#7C9BB5 8%,transparent)}
.v-turn-doc-row{display:flex;align-items:center;gap:.5rem;padding:.42rem .65rem;
  border-bottom:1px solid var(--m-line);transition:background .15s}
.v-turn-doc-row:last-child{border-bottom:0}
.v-turn-doc-row:hover{background:var(--m-faint-bg)}
.v-turn-doc-row .v-doc-name{flex:1;min-width:0;font-size:.72rem;color:var(--m-bone);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v-turn-doc-row .v-doc-size{font-family:monospace;font-size:.62rem;color:var(--m-faint);flex-shrink:0}
.v-turn-doc-row .v-dl{flex-shrink:0;font-size:.64rem;font-weight:700;padding:.18rem .5rem;border-radius:.45rem;
  background:var(--m-sage-soft);color:var(--m-sage);border:1px solid rgba(143,168,148,.45);transition:all .15s}
.v-turn-doc-row .v-dl:hover{background:#8FA894;color:#16181D}
.v-turn-doc-row .v-pv{flex-shrink:0;font-size:.64rem;padding:.18rem .5rem;border-radius:.45rem;
  background:var(--m-faint-bg);color:var(--m-dim);border:1px solid var(--m-line);transition:all .15s}
.v-turn-doc-row .v-pv:hover{color:var(--v-sky);border-color:rgba(56,189,248,.45)}

/* ── 全頁視圖（Agent / 資料庫 / 額度 / 執行明細） ── */
#c-fullpage{z-index:46}
#c-fullpage .v-fp-head{border-bottom:1px solid var(--m-line);
  background:color-mix(in srgb,#16181D 88%,transparent);backdrop-filter:blur(14px)}
.v-fp-tab{padding:.35rem .8rem;border-radius:.6rem;font-size:.72rem;font-weight:600;
  color:var(--m-dim);border:1px solid transparent;transition:all .15s}
.v-fp-tab:hover{color:var(--m-bone);background:var(--m-faint-bg)}
.v-fp-tab.v-on{color:var(--m-bone);background:var(--m-faint-bg);border-color:var(--m-line)}

/* ── 資料庫：對話串歸檔卡片 ── */
.v-thread-card{border:1px solid var(--m-line);border-radius:.9rem;background:var(--m-panel-2);
  overflow:hidden;transition:border-color .15s}
.v-thread-card:hover{border-color:color-mix(in srgb,#7C9BB5 40%,var(--m-line))}
.v-thread-head{display:flex;align-items:center;gap:.6rem;padding:.6rem .8rem;cursor:pointer;user-select:none}
.v-thread-head:hover{background:var(--m-faint-bg)}
.v-thread-title{font-size:.76rem;font-weight:700;color:var(--m-bone);min-width:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v-thread-meta{font-family:monospace;font-size:.62rem;color:var(--m-faint);flex-shrink:0}
.v-thread-body{border-top:1px solid var(--m-line)}

/* ── 執行明細頁 ── */
.v-detail-sec{border:1px solid var(--m-line);border-radius:.8rem;background:var(--m-panel-2);padding:.8rem .9rem}
.v-detail-sec h4{font-size:.7rem;font-weight:800;letter-spacing:.05em;color:var(--m-faint);
  text-transform:uppercase;margin-bottom:.5rem;display:flex;align-items:center;gap:.4rem}
.v-detail-pre{white-space:pre-wrap;word-break:break-word;font-size:.74rem;line-height:1.65;color:var(--m-dim);
  max-height:26rem;overflow-y:auto}

html:not(.dark) .v-turn-docs{background:rgba(255,255,255,.6)}
html:not(.dark) .v-thread-card{background:rgba(255,255,255,.65)}
html:not(.dark) .v-nav{color:#5A6472}
html:not(.dark) .v-nav.v-on{color:#1F2430}
"""


# ── 新版外層版面 ───────────────────────────────────────────────────────────

_SIDEBAR: Final[str] = """
<aside id="c-sidebar" class="w-[280px] xl:w-[296px] shrink-0 bg-ink-800/85 backdrop-blur border-r m-line flex flex-col h-full">

  <!-- ═══ 品牌列：K.A.V.I.S. 漸層識別 + 節點狀態 ═══ -->
  <div class="shrink-0 px-3 pt-3 pb-2 flex items-center gap-2.5 border-b m-line">
    <div class="v-brand-mark w-9 h-9 rounded-xl grid place-items-center text-sm font-black shrink-0" style="color:#fff">K</div>
    <div class="min-w-0 flex-1">
      <div class="v-brand-name text-base font-black tracking-tight leading-none">K.H.A.V.I.S.</div>
      <div class="text-[10px] mt-0.5 flex items-center gap-1" style="color:var(--m-faint)">
        <span class="w-1.5 h-1.5 rounded-full m-live" style="background:var(--v-emerald)"></span>智慧工作台 · 節點在線
      </div>
    </div>
    <button type="button" onclick="toggleTheme()" class="w-7 h-7 rounded-lg m-btn-ghost grid place-items-center transition shrink-0" title="切換深淺色主題">
      <i id="theme-icon" class="fas fa-sun text-[11px]" style="color:var(--v-amber)"></i>
    </button>
    <button type="button" onclick="cToggleSidebar(false)" id="c-sidebar-close" class="w-7 h-7 rounded-lg m-btn-ghost grid place-items-center shrink-0 xl:hidden" aria-label="關閉側欄">
      <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke-linecap="round"/></svg>
    </button>
  </div>

  <!-- ═══ 主操作：新工作任務 + 功能導航（繽紛色相） ═══ -->
  <div class="shrink-0 px-3 pt-3 pb-2 space-y-1.5 border-b m-line">
    <button type="button" onclick="cNewTask()" id="c-new-task"
            class="v-nav v-on !border-0 justify-center !py-2 font-bold text-xs"
            style="background:var(--v-brand-grad);color:#16181D;box-shadow:0 6px 18px -8px rgba(124,155,181,.7)"
            title="開始新對話">
      <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" stroke-linecap="round"/></svg>
      新工作任務
    </button>

    <button type="button" onclick="cOpenPage('command')" data-nav="command" class="tab-btn v-nav">
      <span class="v-nav-ic v-ic-command"><svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke-linejoin="round"/></svg></span>
      <span class="text-xs font-semibold">對話串</span>
      <span class="v-nav-dot" style="background:var(--v-sky)"></span>
    </button>

    <button type="button" onclick="cOpenPage('agents')" id="nav-agents" data-nav="agents" class="tab-btn v-nav">
      <span class="v-nav-ic v-ic-agents"><svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="M12 7v4m0 0-5 5m5-5 5 5" stroke-linecap="round"/></svg></span>
      <span class="text-xs font-semibold min-w-0">Agent 狀態</span>
      <span class="text-[9px] font-mono px-1 py-0.5 rounded shrink-0" style="background:var(--m-sage-soft);color:var(--m-sage)"><span id="c-fleet-count">7</span>/7</span>
      <span class="v-nav-dot" style="background:var(--v-emerald)"></span>
    </button>

    <button type="button" onclick="cOpenPage('artifacts')" id="nav-artifacts" data-nav="artifacts" class="tab-btn v-nav">
      <span class="v-nav-ic v-ic-db"><svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/></svg></span>
      <span class="text-xs font-semibold min-w-0">資料庫</span>
      <span id="artifacts-badge" class="text-[9px] font-mono px-1 py-0.5 rounded shrink-0" style="background:var(--m-apricot-soft);color:var(--m-apricot)">--</span>
      <span class="v-nav-dot" style="background:var(--v-amber)"></span>
    </button>

    <button type="button" onclick="cOpenPage('quotas')" id="nav-quotas" data-nav="quotas" class="tab-btn v-nav">
      <span class="v-nav-ic v-ic-quota"><svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 5-6" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
      <span class="text-xs font-semibold min-w-0">額度、設定</span>
      <span class="text-[9px] font-mono shrink-0" style="color:var(--v-emerald)" id="c-quota-pct">額度 95%</span>
      <span class="v-nav-dot" style="background:var(--v-rose)"></span>
    </button>
  </div>

  <!-- ═══ 歷史談話：依專案分組的對話串清單 ═══ -->
  <div class="flex-1 min-h-0 flex flex-col">
    <div class="px-3 pt-2.5 pb-1 flex items-center gap-1.5 shrink-0">
      <span class="c-zone-title">歷史談話</span>
      <span class="c-zone-tag">HISTORY</span>
      <div class="flex-1"></div>
      <button type="button" onclick="cToggleSearch()" id="c-search-btn"
              class="w-6 h-6 shrink-0 rounded-lg m-btn-ghost grid place-items-center transition" title="搜尋歷史談話" aria-label="搜尋歷史談話">
        <svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="m20 20-3.5-3.5" stroke-linecap="round"/></svg>
      </button>
    </div>

    <div class="px-3 pb-1.5 space-y-1.5 shrink-0">
      <div class="relative">
        <button type="button" onclick="cToggleViewMenu()" id="c-view-btn"
                class="flex-1 min-w-0 flex items-center gap-1.5 px-2 py-1 rounded-lg m-btn-ghost text-[11px] transition w-full"
                aria-haspopup="menu" aria-expanded="false">
          <svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M4 6h16M4 12h10M4 18h6" stroke-linecap="round"/></svg>
          <span class="truncate" id="c-view-label">時間線</span>
          <svg class="ml-auto shrink-0" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>

        <div id="c-view-menu" class="c-menu hidden" role="menu" aria-label="檢視方式">
          <button type="button" class="c-menu-item c-on" data-view="timeline" onclick="cSetView('timeline')" role="menuitem">
            <span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:var(--v-sky)"></span>時間線<span class="ml-auto text-[10px] opacity-70">依日期</span>
          </button>
          <button type="button" class="c-menu-item" data-view="project" onclick="cSetView('project')" role="menuitem">
            <span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:var(--v-emerald)"></span>依專案<span class="ml-auto text-[10px] opacity-70">分組</span>
          </button>
          <button type="button" class="c-menu-item" data-view="backend" onclick="cSetView('backend')" role="menuitem">
            <span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:var(--v-violet)"></span>依執行電腦<span class="ml-auto text-[10px] opacity-70">分組</span>
          </button>
          <button type="button" class="c-menu-item" data-view="mode" onclick="cSetView('mode')" role="menuitem">
            <span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:var(--v-amber)"></span>依協同模式<span class="ml-auto text-[10px] opacity-70">分組</span>
          </button>
          <button type="button" class="c-menu-item" data-view="status" onclick="cSetView('status')" role="menuitem">
            <span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:var(--v-rose)"></span>依狀態<span class="ml-auto text-[10px] opacity-70">分組</span>
          </button>
        </div>
      </div>

      <div id="c-search-row" class="hidden">
        <input id="c-list-search" type="search" placeholder="搜尋談話、專案或 Agent…"
               oninput="cFilterList(this.value)" autocomplete="off"
               class="w-full px-2 py-1 text-[11px] rounded-lg m-line" style="background:var(--m-panel-2);color:var(--m-bone)">
      </div>

      <div class="flex items-center gap-1 text-[10px] font-mono">
        <button type="button" onclick="cFilter('all')" data-cfilter="all" class="c-foot-chip m-nav-active">全部</button>
        <button type="button" onclick="cFilter('running')" data-cfilter="running" class="c-foot-chip">執行 <span id="c-count-running">0</span></button>
        <button type="button" onclick="cFilter('done')" data-cfilter="done" class="c-foot-chip">完成 <span id="c-count-done">0</span></button>
        <button type="button" onclick="cFilter('failed')" data-cfilter="failed" class="c-foot-chip">失敗 <span id="c-count-failed">0</span></button>
      </div>
    </div>

    <div id="c-item-list" class="flex-1 overflow-y-auto m-scroll px-2 pb-2 text-xs"></div>
  </div>

  <!-- ═══ 底部：額度水位 + 快捷 ═══ -->
  <div class="shrink-0 border-t m-line px-3 py-2 space-y-1.5" style="background:var(--m-panel-2)">
    <div class="flex items-center justify-between text-[10px] font-mono" style="color:var(--m-faint)">
      <span>額度水位</span>
      <button type="button" onclick="cOpenPage('quotas')" class="hover:underline" style="color:var(--v-rose)">額度熔斷 →</button>
    </div>
    <div class="h-1.5 rounded-full overflow-hidden" style="background:var(--m-track)">
      <div id="c-quota-bar" class="h-full rounded-full transition-all" style="width:94%;background:linear-gradient(90deg,#8FA894,#7C9BB5)"></div>
    </div>
    <div id="c-sidebar-quota-pills" class="grid grid-cols-2 gap-1 text-[10px] font-mono">
      <div class="px-2 py-1 rounded m-line flex items-center justify-between" style="background:var(--m-panel-2)">
        <span style="color:var(--m-dim)">MiniMax</span><span style="color:var(--v-emerald)">95%</span>
      </div>
      <div class="px-2 py-1 rounded m-line flex items-center justify-between" style="background:var(--m-panel-2)">
        <span style="color:var(--m-dim)">GLM-5.3</span><span style="color:var(--v-sky)">92%</span>
      </div>
      <div class="px-2 py-1 rounded m-line flex items-center justify-between" style="background:var(--m-panel-2)">
        <span style="color:var(--m-dim)">GPT GO</span><span style="color:var(--v-violet)">90%</span>
      </div>
      <div class="px-2 py-1 rounded m-line flex items-center justify-between" style="background:var(--m-panel-2)">
        <span style="color:var(--m-dim)">AGY Pro</span><span style="color:var(--v-amber)">98%</span>
      </div>
    </div>
    <button type="button" onclick="cOpenPage('agents')" id="nav-wizard" class="tab-btn w-full px-2 py-1.5 rounded-lg m-btn-ghost text-[11px] font-medium transition text-center">
      模型與服務商設定
    </button>
  </div>
</aside>
"""

_TOPBAR: Final[str] = """
<header class="h-11 shrink-0 bg-ink-800/60 backdrop-blur border-b m-line flex items-center justify-between px-3 sm:px-4 gap-3">
  <div class="flex items-center gap-2 text-xs shrink-0">
    <button type="button" onclick="cToggleSidebar(true)" id="c-menu-btn"
            class="w-7 h-7 shrink-0 rounded-lg m-btn-ghost grid place-items-center"
            aria-label="開啟側欄" aria-controls="c-sidebar" aria-expanded="false">
      <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16" stroke-linecap="round"/></svg>
    </button>
    <button type="button" onclick="cOpenPage('command')" id="nav-command"
            class="tab-btn c-zone-title px-2.5 py-1 rounded-lg m-nav-active transition flex items-center gap-1.5 whitespace-nowrap shrink-0" title="返回對話串主畫面">
      <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" style="color:var(--m-mist)" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke-linejoin="round"/></svg>
      <span>對話串</span>
    </button>
    <button type="button" onclick="cOpenPage('agents')" id="c-top-btn-agents"
            class="px-2.5 py-1 rounded-lg m-btn-ghost text-[11px] font-semibold transition inline-flex items-center gap-1.5 shrink-0 whitespace-nowrap"
            title="進入 Agent 狀態、模型新增/刪除/調整與服務商管理專頁">
      <span>🤖 Agent 狀態<span class="hidden 2xl:inline">與模型/服務商</span></span>
    </button>
    <button type="button" onclick="cOpenPage('artifacts')" id="c-top-btn-db"
            class="px-2.5 py-1 rounded-lg m-btn-ghost text-[11px] font-semibold transition inline-flex items-center gap-1.5 shrink-0 whitespace-nowrap"
            title="進入資料庫產物專頁">
      <span>📚 資料庫</span>
    </button>
    <svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" class="hidden md:inline" style="color:var(--m-faint)" aria-hidden="true"><path d="m9 6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <span class="font-medium truncate text-[11px] hidden md:inline" id="c-view-title" style="color:var(--m-faint)">主對話視窗</span>
  </div>

  <div class="flex items-center justify-end gap-2 min-w-0 flex-1 overflow-hidden">
    <span id="c-exec-timer" class="font-mono text-[10px] px-1.5 py-0.5 rounded shrink-0"
          style="background:var(--m-mist-soft);color:var(--m-mist)">0.0s</span>
    <span id="c-exec-status-text" class="hidden">待命就緒</span>

    <div id="c-task-filter" class="hidden lg:inline-flex rounded-lg p-0.5 m-line text-[11px] shrink-0" style="background:var(--m-ink-700)" role="group" aria-label="任務篩選">
      <button type="button" onclick="cFilter('all')" class="c-filter-btn px-2 py-0.5 rounded-md font-medium" data-filter="all" aria-pressed="true">全部</button>
      <button type="button" onclick="cFilter('running')" class="c-filter-btn px-2 py-0.5 rounded-md" data-filter="running" aria-pressed="false">執行中</button>
      <button type="button" onclick="cFilter('done')" class="c-filter-btn px-2 py-0.5 rounded-md" data-filter="done" aria-pressed="false">已完成</button>
      <button type="button" onclick="cFilter('failed')" class="c-filter-btn px-2 py-0.5 rounded-md" data-filter="failed" aria-pressed="false">失敗</button>
    </div>

    <div id="quota-capsule-strip" class="c-strip items-center gap-1.5 text-[10px] font-mono min-w-0 overflow-x-auto"
         style="color:var(--m-faint);scrollbar-width:none"></div>

    <span id="c-model-claude" class="hidden"><strong id="card-title-claude">—</strong></span>
    <span id="c-model-codex" class="hidden"><strong id="card-title-codex">—</strong></span>
    <span id="c-model-antigravity" class="hidden"><strong id="card-title-antigravity">—</strong></span>

    <button type="button" onclick="cToggleDrawer()" id="c-drawer-btn"
            class="w-7 h-7 rounded-lg m-btn-ghost grid place-items-center transition shrink-0 relative xl:hidden"
            title="展開右側 Using 檔案面板" aria-label="右側面板" aria-expanded="false">
      <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/></svg>
      <span id="c-drawer-dot" class="hidden absolute top-1 right-1 w-1.5 h-1.5 rounded-full m-live" style="background:var(--m-mist)"></span>
    </button>
  </div>
</header>
"""

# 摺疊面板外框
_COLLAPSE_TOP: Final[str] = """
<details class="m-collapse frost m-line rounded-2xl overflow-hidden">
  <summary class="px-4 py-3 flex items-center gap-2.5 hover:bg-white/[.02] transition">
    <svg class="m-caret" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>
"""
_COLLAPSE_BOTTOM: Final[str] = """
  </details>
"""

_COMPOSER_CHROME_TOP: Final[str] = """
<!-- ═══════════════════════════════════════════════════════════════
     中下區塊 5：輸入框 (Prompt Input & Collaborative Composer)
     ═══════════════════════════════════════════════════════════════ -->
<div id="c-composer-wrap" class="shrink-0 border-t m-line px-3 sm:px-5 py-2.5" style="background:var(--m-panel);backdrop-filter:blur(12px)">
  <div class="max-w-4xl mx-auto">
    <div class="composer frost-2 m-line rounded-2xl p-3 transition">

      <!-- 輸入框標題 + 協同模式 + 參與 Agent -->
      <div class="flex items-center gap-2 flex-wrap mb-2 pb-2" style="border-bottom:1px solid var(--m-line)">
        <span class="c-zone-title mr-1">
          <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--m-mist)" viewBox="0 0 24 24"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" stroke-linejoin="round"/></svg>
          輸入框
        </span>
        <div class="inline-flex rounded-lg p-0.5 m-line text-[11px]" style="background:var(--m-panel-2)" role="group" aria-label="協同模式">
          <button type="button" class="c-collab-btn m-btn-primary px-2.5 py-1 rounded-md font-semibold transition" data-mode="auto_loop"  onclick="setCollabMode('auto_loop')">自動閉環</button>
          <button type="button" class="c-collab-btn m-btn-ghost px-2.5 py-1 rounded-md transition" data-mode="relay"    onclick="setCollabMode('relay')">接力</button>
          <button type="button" class="c-collab-btn m-btn-ghost px-2.5 py-1 rounded-md transition" data-mode="critique" onclick="setCollabMode('critique')">互評</button>
          <button type="button" class="c-collab-btn m-btn-ghost px-2.5 py-1 rounded-md transition" data-mode="parallel" onclick="setCollabMode('parallel')">並發</button>
        </div>
        <span id="c-collab-desc" class="text-[11px] truncate hidden sm:inline" style="color:var(--m-faint)"></span>
        <button type="button" onclick="cNewTask()" class="px-2 py-0.5 rounded-md text-[11px] m-btn-ghost transition inline-flex items-center gap-1" title="開啟全新的第 1 輪對話（不帶入舊對話上下文）">
          <span>＋ 新對話</span>
        </button>
        <div class="flex-1"></div>
        <div id="c-agent-chips" class="flex items-center gap-1.5 flex-wrap"></div>
      </div>
"""

_COMPOSER_CHROME_BOTTOM: Final[str] = """
    </div>
    <div class="flex items-center justify-between mt-1.5 px-1 text-[11px] font-mono" style="color:var(--m-faint)">
      <span id="c-composer-hint">shared-app · ⌘/Ctrl+⏎ 執行 · ⏎ 換行</span>
      <span id="c-context-badge" class="hidden"></span>
    </div>
  </div>
</div>
"""


# 必須在「既有 JS 之後、覆寫版之前」抓取原始函式參照。
# 原因：function 宣告會在各自 script 區塊求值前 hoist，因此若在同一個
# script 裡寫 `const x = switchTab; function switchTab(){}`，取到的會是
# 自己 → 無限遞迴。獨立成一個 script 區塊即可安全取得原始參照。
_LEGACY_CAPTURE_JS: Final[str] = """
<script>
  const __legacySwitchTab         = switchTab;
  const __legacySetCollabMode     = setCollabMode;
  const __legacySetPipelineStep   = setPipelineStepState;
  const __legacySubmitDispatch    = submitDispatch;
</script>
"""


# ── 新增的 client 邏輯（附加在既有 JS 之後，後者不會被覆蓋） ────────────────
_C_EXTRA_JS: Final[str] = """
<script>
// ══════════════════════════════════════════════════════════════════
// 選項 C：任務卡片流 — 專屬邏輯
// 附加於既有 workbench JS 之後；switchTab / setCollabMode 於此覆寫，
// 以套用莫蘭迪選中態並同步新的 composer。
// ══════════════════════════════════════════════════════════════════

const C_AGENT_COLORS = {
  claude:      ['--m-mist', '#7C9BB5'],
  codex:       ['--m-lilac', '#A99AB5'],
  antigravity: ['--m-sage', '#8FA894'],
  minimax:     ['--m-apricot','#C9A17E'],
  opencode:    ['--m-rose',  '#BC8B8B'],
  khavis:      ['--m-butter','#D9C89E'],
  'kavis-tencent': ['--m-lilac', '#A99AB5']
};

let cTasks = [];            // 任務卡片資料
let cFilterKey = 'all';
let cRunningTask = null;    // 執行中的任務卡 id
let cAborted = false;       // 使用者主動中止
let _cAbortController = null;

// ── Toast：取代會阻塞整頁的 browser alert() ───────────────────────
function cToast(msg, kind) {
  const host = document.getElementById('c-toast-host');
  if (!host) return;
  const el = document.createElement('div');
  el.className = 'c-toast' + (kind === 'err' ? ' c-err' : (kind === 'ok' ? ' c-ok' : ''));
  el.textContent = String(msg == null ? '' : msg);
  host.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(6px)';
    setTimeout(() => { if (el.parentElement) el.parentElement.removeChild(el); }, 260);
  }, kind === 'err' ? 7000 : 4500);
}

// ── fetch 包裝：讓「中止」真的能打斷等待中的 dispatch ───────────────
// dispatch 是單次阻塞請求，沒有後端取消 API；這裡用 AbortController
// 中斷客戶端等待，讓 UI 不再被鎖死（後端仍可能自行完成任務）。
const _nativeFetch = (typeof fetch === 'function')
  ? fetch.bind(typeof window !== 'undefined' ? window : null)
  : null;
if (_nativeFetch) {
  window.fetch = function (input, init) {
    let url = '';
    try { url = typeof input === 'string' ? input : ((input && input.url) || ''); } catch (e) {}
    const opts = Object.assign({}, init || {});
    if (url.indexOf('/api/workbench/') !== -1) {
      let tok = '';
      try {
        tok = (typeof sessionStorage !== 'undefined' && sessionStorage.getItem('khavis_console_token'))
           || (typeof localStorage !== 'undefined' && localStorage.getItem('khavis_console_token'))
           || '';
      } catch (e) {}
      if (!tok) {
        const inp = document.getElementById('manus-console-token');
        if (inp && inp.value) tok = inp.value.trim();
      }
      if (tok) {
        opts.headers = Object.assign({}, opts.headers || {}, { 'X-KHAVIS-Console-Token': tok });
      }
    }
    if (url.indexOf('/api/workbench/dispatch') !== -1) {
      if (typeof AbortController !== 'undefined') {
        try {
          _cAbortController = new AbortController();
          opts.signal = _cAbortController.signal;
        } catch (e) { /* 不支援就略過中止功能 */ }
      }
      return _nativeFetch(input, opts);
    }
    return _nativeFetch(input, opts);
  };
}

// ── 覆寫 switchTab：主對話 + 全頁視圖（如外掛市集頁） ─────────────────────
const _switchTabLegacy = __legacySwitchTab;

// 全頁視圖定義：tab → (來源 section, 標題, 色彩 icon)
const C_FP_META = {
  agents:   { sec: 'view-agents',    title: 'Agent 狀態 · 模型與服務商管理', ic: 'v-ic-agents' },
  wizard:   { sec: 'view-wizard',    title: '部署與服務商金鑰設定',          ic: 'v-ic-db' },
  artifacts:{ sec: 'view-artifacts', title: '資料庫 · 依對話串歸檔的產出文檔', ic: 'v-ic-db' },
  quotas:   { sec: 'view-quotas',    title: '額度與熔斷監控',                ic: 'v-ic-quota' },
};

function cFullpageShow(tab) {
  const fp = document.getElementById('c-fullpage');
  const content = document.getElementById('c-fullpage-content');
  const meta = C_FP_META[tab];
  if (!fp || !content || !meta) return;
  // 執行明細頁與功能頁互斥
  const detail = document.getElementById('c-turn-detail-page');
  if (detail) detail.classList.add('hidden');
  // 把來源 section 搬進全頁容器（一次性；搬過的元素持續保留在裡面）
  let sec = document.getElementById(meta.sec);
  if (sec && sec.parentElement !== content) {
    sec.classList.remove('hidden');
    sec.classList.add('block', 'max-w-5xl', 'mx-auto', 'px-4', 'sm:px-6', 'py-5');
    content.appendChild(sec);
  } else if (sec) {
    sec.classList.remove('hidden');
  }
  content.querySelectorAll('.tab-content').forEach(el => { if (el !== sec) el.classList.add('hidden'); });
  const ic = document.getElementById('c-fullpage-ic');
  if (ic) ic.className = 'v-nav-ic ' + meta.ic;
  const ti = document.getElementById('c-fullpage-title');
  if (ti) ti.textContent = meta.title;
  fp.classList.remove('hidden');
  if (tab === 'agents' && typeof cLoadAgentsAndProvidersPage === 'function') cLoadAgentsAndProvidersPage();
  if (tab === 'artifacts' && typeof cLoadDatabasePage === 'function') cLoadDatabasePage();
}

function cFullpageHide() {
  const fp = document.getElementById('c-fullpage');
  if (fp) fp.classList.add('hidden');
}

const C_NAV_BY_TAB = { command: 'nav-command', agents: 'nav-agents', artifacts: 'nav-artifacts', quotas: 'nav-quotas', wizard: 'nav-wizard' };

function switchTab(tab) {
  _switchTabLegacy(tab);

  // 側欄導航激活態（含繽紛 v-on 色彩）
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.remove('m-nav-active');
    b.setAttribute('aria-current', 'false');
  });
  document.querySelectorAll('#c-sidebar .v-nav').forEach(b => {
    b.classList.remove('v-on');
    if (b.dataset.nav === tab) b.classList.add('v-on');
  });
  const navId = C_NAV_BY_TAB[tab];
  if (navId) {
    const el = document.getElementById(navId);
    if (el) {
      el.classList.add('m-nav-active');
      el.setAttribute('aria-current', 'page');
    }
  }

  const titles = {
    command: '主對話視窗',
    artifacts: '📚 資料庫 · 對話串歸檔',
    quotas: '📊 額度與熔斷監控',
    agents: '🤖 Agent 狀態 · 模型與服務商管理',
    wizard: '🚀 部署與服務商金鑰設定'
  };
  const t = document.getElementById('c-view-title');
  if (t) t.textContent = titles[tab] || '主對話視窗';

  // 全頁視圖：非對話首頁都以獨立頁呈現（Esc／返回鍵回到對話串）
  if (tab === 'command') cFullpageHide();
  else cFullpageShow(tab);

  // 窄螢幕下側欄是抽屜，切換頁籤後自動收合
  cToggleSidebar(false);
}

function cTabFromLocation() {
  try {
    const path = location.pathname || '';
    if (path === '/agents') return 'agents';
    if (path === '/database') return 'artifacts';
    const page = new URLSearchParams(location.search).get('page');
    if (page === 'quotas') return 'quotas';
  } catch (_) {}
  return 'command';
}

function cOpenPage(tab, pushUrl = true) {
  switchTab(tab);
  if (pushUrl && typeof history !== 'undefined' && typeof history.pushState === 'function') {
    try {
      const urlMap = { command: '/workbench', agents: '/agents', artifacts: '/database', quotas: '/workbench?page=quotas', wizard: '/agents' };
      const targetUrl = urlMap[tab] || '/workbench';
      if (typeof location !== 'undefined' && location.pathname + location.search !== targetUrl) {
        history.pushState({ page: tab }, '', targetUrl);
      }
    } catch (_) {}
  }
}

// ── Agent 模型勾選（從供應商抓清單 → 使用者勾選 → 勾選結果為權威清單） ──
let cPickerAgent = null;
let cPickerModels = [];      // [{ref, model, provider_label}]
let cPickerSelected = new Set();

async function cOpenAgentModelPicker(agentId) {
  if (!agentId) return;
  cPickerAgent = agentId;
  const fp = document.getElementById('c-fullpage');
  const content = document.getElementById('c-fullpage-content');
  if (!fp || !content) return;
  content.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  const detail = document.getElementById('c-turn-detail-page');
  if (detail) detail.classList.add('hidden');
  let page = document.getElementById('c-agent-model-picker-page');
  if (!page) {
    page = document.createElement('div');
    page.id = 'c-agent-model-picker-page';
    content.appendChild(page);
  }
  page.classList.remove('hidden');
  const label = (C_AGENT_LABELS[agentId] || agentId);
  const ti = document.getElementById('c-fullpage-title');
  if (ti) ti.textContent = `模型勾選 · ${label}`;
  const ic = document.getElementById('c-fullpage-ic');
  if (ic) ic.className = 'v-nav-ic v-ic-agents';
  fp.classList.remove('hidden');
  content.scrollTop = 0;

  page.innerHTML = '<div class="max-w-3xl mx-auto px-4 py-6 text-[12px] font-mono" style="color:var(--m-faint)">⏳ 正在從供應商抓取模型清單…</div>';
  try {
    const res = await fetch('/api/workbench/agent_models', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'fetch', agent: agentId })
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      page.innerHTML = `<div class="max-w-3xl mx-auto px-4 py-6 text-[12px] text-rose-300">抓取失敗：${escapeHtml(data.detail || ('HTTP ' + res.status))}</div>`;
      return;
    }
    cPickerModels = data.models || [];
    cPickerSelected = new Set(data.selected || []);
    // 已勾選但供應商清單沒有的（手動新增/舊清單）也列出，標示為自訂
    cPickerSelected.forEach(ref => {
      if (!cPickerModels.some(m => m.ref === ref)) {
        const id = ref.includes('::') ? ref.split('::').pop() : ref;
        cPickerModels.push({ ref, model: id, provider_label: '自訂 / 手動' });
      }
    });
    cRenderAgentModelPicker(data.agent_label || label, data.active || '', data.unreachable_providers || []);
  } catch (err) {
    page.innerHTML = `<div class="max-w-3xl mx-auto px-4 py-6 text-[12px] text-rose-300">連線錯誤：${escapeHtml(err.message)}</div>`;
  }
}

function cRenderAgentModelPicker(agentLabel, activeRef, unreachable) {
  const page = document.getElementById('c-agent-model-picker-page');
  if (!page) return;
  const groups = {};
  cPickerModels.forEach(m => {
    const g = m.provider_label || '其他';
    (groups[g] = groups[g] || []).push(m);
  });
  const selCount = cPickerSelected.size;
  page.innerHTML = `
    <div class="max-w-3xl mx-auto px-4 sm:px-6 py-5 space-y-3">
      <div class="v-detail-sec">
        <div class="flex items-center justify-between gap-2">
          <div>
            <div class="text-xs font-bold" style="color:var(--m-bone)">為「${escapeHtml(agentLabel)}」勾選可用模型</div>
            <div class="text-[10px] mt-0.5" style="color:var(--m-faint)">從供應商即時清單勾選；勾選結果會成為此 Agent 的權威模型清單（× 刪除不再被復活）</div>
          </div>
          <span class="text-[11px] font-mono" style="color:var(--m-sage)">已勾選 <span id="c-picker-count">${selCount}</span> 個</span>
        </div>
        <div class="flex items-center gap-1.5 mt-2.5">
          <input id="c-picker-filter" type="search" oninput="cRenderPickerList()" placeholder="搜尋過濾模型…" autocomplete="off"
                 class="flex-1 bg-transparent border border-white/10 rounded px-2 py-1 text-[11px] font-mono text-white placeholder-slate-500 focus:outline-none focus:border-sky-400" />
          <button type="button" onclick="cSaveAgentModelSelection()" class="px-3.5 py-1.5 rounded-lg m-btn-primary text-xs font-bold shrink-0">💾 儲存勾選</button>
        </div>
        ${activeRef ? `<div class="text-[10px] font-mono mt-1.5" style="color:var(--m-faint)">目前使用：<span style="color:var(--m-sage)">${escapeHtml(modelRefLabel(activeRef))}</span>（若取消勾選會自動切到第一個勾選項）</div>` : ''}
        ${unreachable && unreachable.length ? `<div class="text-[10px] mt-1.5" style="color:var(--m-apricot)">⚠️ 無法抓取：${escapeHtml(unreachable.join('、'))}（金鑰未設定或供應商離線）</div>` : ''}
      </div>

      <div class="flex items-center gap-1.5">
        <input id="c-picker-manual" type="text" placeholder="手動新增自訂模型 ID…" autocomplete="off"
               onkeydown="if(event.key==='Enter'){event.preventDefault();cPickerAddManual();}"
               class="flex-1 bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-[11px] font-mono text-white placeholder-slate-500 focus:outline-none focus:border-emerald-400" />
        <button type="button" onclick="cPickerAddManual()" class="px-2.5 py-1.5 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/30 text-emerald-300 border border-emerald-500/35 text-[11px] font-bold shrink-0">＋加入</button>
      </div>

      <div id="c-picker-list" class="space-y-2.5"></div>
    </div>`;
  cRenderPickerList();
}

function cRenderPickerList() {
  const host = document.getElementById('c-picker-list');
  if (!host) return;
  const kw = ((document.getElementById('c-picker-filter') || {}).value || '').trim().toLowerCase();
  const groups = {};
  cPickerModels.forEach(m => {
    const hit = !kw || m.ref.toLowerCase().includes(kw) || (m.provider_label || '').toLowerCase().includes(kw);
    if (hit) (groups[m.provider_label || '其他'] = groups[m.provider_label || '其他'] || []).push(m);
  });
  const keys = Object.keys(groups).sort();
  if (!keys.length) { host.innerHTML = '<div class="text-[11px] py-4 text-center" style="color:var(--m-faint)">沒有符合的模型</div>'; return; }
  host.innerHTML = keys.map(g => `
    <div class="v-thread-card">
      <div class="v-thread-head" style="cursor:default">
        <span class="v-proj-dot" style="background:var(--v-sky)"></span>
        <span class="v-thread-title">${escapeHtml(g)}</span>
        <span class="v-thread-meta">${groups[g].length} 個模型</span>
      </div>
      <div class="v-thread-body">
        ${groups[g].map(m => `
          <label class="v-turn-doc-row" style="cursor:pointer">
            <input type="checkbox" ${cPickerSelected.has(m.ref) ? 'checked' : ''}
                   onchange="cPickerToggle('${escapeHtml(m.ref)}', this.checked)" class="accent-emerald-400 shrink-0" />
            <span class="v-doc-name">${escapeHtml(m.model)}</span>
            ${cPickerSelected.has(m.ref) ? '<span class="v-thread-meta" style="color:var(--m-sage)">✓ 已選</span>' : ''}
          </label>`).join('')}
      </div>
    </div>`).join('');
  const cnt = document.getElementById('c-picker-count');
  if (cnt) cnt.textContent = String(cPickerSelected.size);
}

function cPickerToggle(ref, on) {
  if (on) cPickerSelected.add(ref); else cPickerSelected.delete(ref);
  const cnt = document.getElementById('c-picker-count');
  if (cnt) cnt.textContent = String(cPickerSelected.size);
}

function cPickerAddManual() {
  const inp = document.getElementById('c-picker-manual');
  const id = ((inp && inp.value) || '').trim();
  if (!id) { cToast('請輸入模型 ID', 'err'); return; }
  if (!/^[A-Za-z0-9._:\\-\\/]{1,120}$/.test(id)) { cToast('模型 ID 格式無效', 'err'); return; }
  // 手動項用裸 ID 當 ref（與舊新增行為一致，dispatch 才能正確解析）
  const ref = id;
  if (!cPickerModels.some(m => m.ref === ref)) cPickerModels.push({ ref, model: id, provider_label: '自訂 / 手動' });
  cPickerSelected.add(ref);
  if (inp) inp.value = '';
  cRenderPickerList();
}

async function cSaveAgentModelSelection() {
  if (!cPickerAgent) return;
  const models = Array.from(cPickerSelected);
  if (!models.length) { cToast('請至少勾選一個模型', 'err'); return; }
  try {
    const res = await fetch('/api/workbench/agent_models', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'set', agent: cPickerAgent, models })
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      cToast('儲存失敗：' + (data.detail || ('HTTP ' + res.status)), 'err');
      return;
    }
    cToast(`✅ 已更新模型清單（${models.length} 個）`, 'ok');
    cFullpageHide();
    if (typeof location !== 'undefined' && location.pathname !== '/workbench') {
      try { history.pushState({ page: 'command' }, '', '/workbench'); } catch (_) {}
    }
    switchTab('command');
    setTimeout(() => { cOpenPage('agents'); }, 60);
  } catch (err) {
    cToast('儲存連線錯誤：' + err.message, 'err');
  }
}

function cCloseFullpage() {
  cFullpageHide();
  // 全頁關閉 = 回到主對話串（網址同步，不觸發 push 迴圈）
  if (typeof location !== 'undefined' && location.pathname !== '/workbench') {
    try { history.pushState({ page: 'command' }, '', '/workbench'); } catch (_) {}
  }
  switchTab('command');
}

// ── 執行明細全頁：單輪任務的角色流水線 / 交叉審查與比較明細 ───────────
function cOpenTurnDetail(id) {
  const t = (cTasks || []).find(x => x.id === id);
  if (!t) { cToast('找不到這一輪的明細資料', 'err'); return; }
  const fp = document.getElementById('c-fullpage');
  const content = document.getElementById('c-fullpage-content');
  if (!fp || !content) return;

  // 隱藏功能頁 sections，只顯示明細頁
  content.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  let page = document.getElementById('c-turn-detail-page');
  if (!page) {
    page = document.createElement('div');
    page.id = 'c-turn-detail-page';
    content.appendChild(page);
  }
  page.classList.remove('hidden');

  const agents = (t.agents || []).map(a => {
    const hex = (C_AGENT_COLORS[a] || [])[1] || '#7C9BB5';
    return `<span class="c-conn-badge" style="background:${hex}26;color:${hex}">${escapeHtml(C_AGENT_LABELS[a] || a)}</span>`;
  }).join('') || '<span class="text-[11px]" style="color:var(--m-faint)">（未記錄）</span>';
  const round = t.step || '—';

  page.innerHTML = `
    <div class="max-w-4xl mx-auto px-4 sm:px-6 py-5 space-y-3">
      <div class="v-detail-sec">
        <h4><span>🧭</span>任務（第 ${escapeHtml(String(round))} 輪）</h4>
        <div class="text-[13px] leading-relaxed" style="color:var(--m-bone)">${escapeHtml(t.prompt || '（未記錄）')}</div>
        <div class="flex items-center gap-1.5 flex-wrap mt-2.5">
          ${agents}
          ${t.mode ? `<span class="c-conn-badge" style="background:var(--m-lilac-soft);color:var(--m-lilac)">${escapeHtml(t.mode)}</span>` : ''}
          ${t.project ? `<span class="c-conn-badge" style="background:var(--m-faint-bg);color:var(--m-dim)">${escapeHtml(t.project)}</span>` : ''}
          ${t.duration ? `<span class="c-conn-badge" style="background:var(--m-faint-bg);color:var(--m-dim)">${escapeHtml(t.duration)}</span>` : ''}
          <span class="c-conn-badge" style="background:var(--m-faint-bg);color:var(--m-dim)">${escapeHtml(cStatusMeta(t.status).label || t.status || '')}</span>
        </div>
      </div>

      <div class="v-detail-sec">
        <h4><span>📎</span>本輪產出文檔</h4>
        ${cOutputFilesHtml(t) || '<div class="text-[11px]" style="color:var(--m-faint)">這一輪沒有產出文檔</div>'}
      </div>

      <div class="v-detail-sec">
        <h4><span>🏁</span>共識結論 (Consensus)</h4>
        <pre class="v-detail-pre">${escapeHtml(t.raw || t.result || '（無）')}</pre>
      </div>

      <div class="v-detail-sec">
        <h4><span>⚔️</span>Agent 交叉審查與比較明細 (Review)</h4>
        <pre class="v-detail-pre">${escapeHtml(t.review || '（這一輪沒有交叉審查明細）')}</pre>
      </div>
    </div>`;

  const ti = document.getElementById('c-fullpage-title');
  if (ti) ti.textContent = `執行明細 · 第 ${round} 輪`;
  const ic = document.getElementById('c-fullpage-ic');
  if (ic) ic.className = 'v-nav-ic v-ic-command';
  fp.classList.remove('hidden');
  content.scrollTop = 0;
}

// ── 覆寫 setCollabMode：同步 composer 內的模式列 ───────────────────
const _setCollabModeLegacy = __legacySetCollabMode;
function setCollabMode(mode) {
  _setCollabModeLegacy(mode);
  document.querySelectorAll('.c-collab-btn').forEach(b => {
    const on = b.dataset.mode === mode;
    b.classList.toggle('m-btn-primary', on);
    b.classList.toggle('font-semibold', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    if (!on) b.classList.add('m-btn-ghost');
  });
  const d = document.getElementById('c-collab-desc');
  if (d && COLLAB_MODE_DESCS[mode]) d.textContent = COLLAB_MODE_DESCS[mode];
}

// ── 側欄抽屜（窄視窗） ─────────────────────────────────────────────
function cToggleSidebar(open) {
  const sb = document.getElementById('c-sidebar');
  const scrim = document.getElementById('c-scrim');
  if (sb) sb.classList.toggle('c-open', !!open);
  if (scrim) scrim.classList.toggle('c-on', !!open);
  const btn = document.getElementById('c-menu-btn');
  if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

// ── 右側執行明細抽屜 ───────────────────────────────────────────────
function cToggleDrawer(force) {
  const d = document.getElementById('c-drawer');
  if (!d) return;
  const show = (typeof force === 'boolean') ? force : !d.classList.contains('c-open');
  d.classList.toggle('c-open', show);
  const scrim = document.getElementById('c-drawer-scrim');
  if (scrim) scrim.classList.toggle('c-on', show);
  const btn = document.getElementById('c-drawer-btn');
  if (btn) btn.setAttribute('aria-expanded', show ? 'true' : 'false');
  if (show) cSetDrawerDot(false);
}

function cSetDrawerDot(on) {
  const dot = document.getElementById('c-drawer-dot');
  if (dot) dot.classList.toggle('hidden', !on);
}

// ── 左側項目清單（參考 MiniMax Code 的 session list） ───────────────
let cViewKey = 'timeline';
let cSearchQuery = '';
let cSelectedId = null;
let cActiveProject = 'shared-app';

/* 對話串（thread）模型 —— 對應 MiniMax Code 的 session list */
let cThreads = new Map();     // threadId -> { id, tasks[], title, ... }
let cThreadId = null;         // 目前派工所屬的對話串
let cThreadStep = 0;          // 該對話串已完成的輪數
let cSelectedThread = null;   // 中央正在顯示的對話串
let cLiveThreadId = null;     // 本次頁面生命週期內最後派工的對話串
let cCenterMode = 'thread';   // 'thread' | 'overview'
let cIsNewDraft = false;      // 使用者主動點選「＋ 新對話」時保持空白新對話狀態，不被舊歷史覆蓋

const C_VIEWS = {
  timeline: { label:'時間線' },
  project:  { label:'依專案' },
  backend:  { label:'依執行電腦' },
  mode:     { label:'依協同模式' },
  status:   { label:'依狀態' }
};

const C_STATUS_LABEL = { running:'執行中', done:'已完成', failed:'失敗', aborted:'已中止' };

function cDayLabel(t) {
  const day = t.day || '';
  if (!day) return '最近';
  if (day === cToday()) return '今天';
  const d = new Date();
  d.setDate(d.getDate() - 1);
  const p = n => String(n).padStart(2, '0');
  const yest = d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  if (day === yest) return '昨天';
  return day;
}

// 每個任務可能同時屬於多個分組（例如一次派工跑了 3 個 Agent）
function cGroupKeys(t, viewKey) {
  // 舊的 dispatch 紀錄沒有記 project（記錄欄位是後來才補的），
  // 當時的 dispatch 其實就跑在當時的工作區專案 → 用目前專案回推。
  if (viewKey === 'project') return [t.project || cActiveProject || '未指定專案'];
  if (viewKey === 'backend') return (t.agents && t.agents.length) ? t.agents : ['未知'];
  if (viewKey === 'mode')    return [t.mode || '未標記'];
  if (viewKey === 'status')  return [C_STATUS_LABEL[t.status] || '未知'];
  return [cDayLabel(t)];
}

function cRelTime(t) {
  if (!t.time) return '';
  if (t.day === cToday()) {
    const parts = String(t.time).split(':').map(Number);
    const now = new Date();
    const itemS = (parts[0] || 0) * 3600 + (parts[1] || 0) * 60 + (parts[2] || 0);
    const nowS = now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
    const diff = Math.max(0, nowS - itemS);
    if (diff < 60) return '剛剛';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小時前';
  }
  return String(t.time).slice(0, 5);
}

// ── 對話串模型 ────────────────────────────────────────────────────────
// 每個 dispatch 帶 thread id；舊的紀錄沒有 thread → 一筆任務自成一串。
function cThreadKeyOf(t) {
  // 舊紀錄沒有 thread id → 一筆任務自成一串。
  // handle 只到秒，同秒的兩筆會撞號，所以要加上 ts 才唯一。
  if (t.thread) return t.thread;
  return 'legacy-' + (t.id || '') + '-' + (t.ts || '');
}

function cBuildThreads() {
  const map = new Map();
  cTasks.forEach(t => {
    const key = cThreadKeyOf(t);
    let th = map.get(key);
    if (!th) {
      th = { id:key, tasks:[], title:'', project:'', agents:[], mode:'',
             status:'done', day:t.day, time:t.time, duration:'' };
      map.set(key, th);
    }
    th.tasks.push(t);
    if (!th.title) th.title = t.prompt || '（無標題對話）';
    if (!th.project) th.project = t.project || '';
    if (!th.mode) th.mode = t.mode || '';
    (t.agents || []).forEach(a => { if (th.agents.indexOf(a) < 0) th.agents.push(a); });
  });
  map.forEach(th => {
    th.tasks.sort((a, b) => ((a.step || 0) - (b.step || 0)));
    const first = th.tasks[0];
    if (first && first.prompt) th.title = first.prompt;
    const last = th.tasks[th.tasks.length - 1];
    if (last) {
      th.status = last.status;
      th.day = last.day || th.day;
      th.time = last.time || th.time;
      th.duration = last.duration || th.duration;
    }
    th.turns = th.tasks.length;
  });
  cThreads = map;
  // 預設顯示最近的一串（但若使用者主動點了「＋ 新對話」即 cIsNewDraft=true，則保持空白新對話）
  if (!cIsNewDraft && (!cSelectedThread || !cThreads.has(cSelectedThread))) {
    let newest = null, newestTs = -1;
    cThreads.forEach(th => {
      const last = th.tasks[th.tasks.length - 1];
      const ts = last ? (last.ts || 0) : 0;
      if (ts >= newestTs) { newestTs = ts; newest = th; }
    });
    if (newest) cSelectedThread = newest.id;
  }
}

function cThreadGroupKeys(th, viewKey) {
  if (viewKey === 'project') return [th.project || cActiveProject || '未指定專案'];
  if (viewKey === 'backend') return th.agents.length ? th.agents : ['未知'];
  if (viewKey === 'mode')    return [th.mode || '未標記'];
  if (viewKey === 'status')  return [C_STATUS_LABEL[th.status] || '未知'];
  return [cDayLabel({ day: th.day })];
}

function cThreadRow(th) {
  const st = cStatusMeta(th.status);
  const sel = cSelectedThread === th.id;
  const bits = [];
  if (th.project) bits.push(th.project);
  if (th.mode) bits.push(th.mode);
  if (th.agents.length) bits.push(th.agents.join('+'));
  if (th.turns > 1) bits.push(th.turns + ' 輪');
  const time = cRelTime({ day: th.day, time: th.time });
  return `<button type="button" onclick="cSelectThread('${escapeHtml(th.id)}')" data-c-thread="${escapeHtml(th.id)}"
            class="c-item ${sel ? 'c-item-sel' : ''}">
    <div class="flex items-center gap-2">
      <span class="c-item-dot" style="background:${st.color}"></span>
      <span class="c-item-title text-[12px] truncate flex-1">${escapeHtml(th.title)}</span>
      <span class="c-item-time text-[10px] font-mono shrink-0">${escapeHtml(time)}</span>
    </div>
    ${bits.length ? `<div class="c-item-meta text-[10px] truncate mt-0.5 pl-3.5">${escapeHtml(bits.join(' · '))}</div>` : ''}
  </button>`;
}

function cRenderItemList() {
  const host = document.getElementById('c-item-list');
  if (!host) return;
  const q = String(cSearchQuery || '').trim().toLowerCase();
  const isFailed = st => st === 'failed' || st === 'aborted';

  cBuildThreads();
  const base = [];
  cThreads.forEach(th => {
    if (cFilterKey !== 'all') {
      const ok = cFilterKey === 'failed' ? isFailed(th.status) : th.status === cFilterKey;
      if (!ok) return;
    }
    if (q) {
      const hay = ((th.title || '') + ' ' + (th.project || '') + ' ' + (th.mode || '') + ' ' +
                   th.agents.join(' ') + ' ' +
                   th.tasks.map(t => t.prompt || '').join(' ')).toLowerCase();
      if (hay.indexOf(q) === -1) return;
    }
    base.push(th);
  });

  if (!base.length) {
    host.innerHTML = `<div class="px-2 py-8 text-center text-[11px]" style="color:var(--m-faint)">
      ${q ? '沒有符合的對話' : '尚無對話紀錄'}</div>`;
    return;
  }

  const order = [];
  const groups = new Map();
  base.forEach(th => {
    cThreadGroupKeys(th, cViewKey).forEach(k => {
      let g = groups.get(k);
      if (!g) { g = { key:k, items:[] }; groups.set(k, g); order.push(g); }
      g.items.push(th);
    });
  });

  let html = '';
  order.forEach(g => {
    html += `<div class="c-group-head"><span class="truncate">${escapeHtml(g.key)}</span>` +
            `<span class="c-group-count">${g.items.length}</span></div>`;
    g.items.forEach(th => { html += cThreadRow(th); });
  });
  host.innerHTML = html;
  cRenderConversation();
}

function cSelectThread(id) {
  cIsNewDraft = false;
  cSelectedThread = id;
  cCenterMode = 'thread';
  const th = cThreads.get(id);
  if (th) {
    // 進入對話串時把該串設為後續派工的接續對象，並同步最後一輪記憶
    cThreadId = id;
    cThreadStep = th.tasks.length;
    const last = th.tasks[th.tasks.length - 1];
    if (last && typeof previousTurnContext !== 'undefined') {
      previousTurnContext = {
        prompt: last.prompt || '',
        consensus: last.consensus || '',
        thread: id,
        step: th.tasks.length
      };
      if (typeof syncContinuationBanner === 'function') syncContinuationBanner();
    }
  }
  switchTab('command');
  cUpdateComposerHint();
  cRenderConversation();
  cRenderItemList();
}

// 「新任務」= 開一串全新的對話（完全清除舊對話記憶與輪數，從第 1 輪獨立開始）
function cNewTask() {
  cIsNewDraft = true;
  cThreadId = null;
  cThreadStep = 0;
  cSelectedThread = null;
  cLiveThreadId = null;
  cCenterMode = 'thread';
  if (typeof clearContinuationContext === 'function') {
    clearContinuationContext();
  } else if (typeof previousTurnContext !== 'undefined') {
    previousTurnContext = null;
  }
  const lrc = document.getElementById('live-results-container');
  if (lrc) lrc.innerHTML = '';
  const pi = document.getElementById('prompt-input');
  if (pi) { pi.value = ''; pi.style.height = 'auto'; }
  cUpdateComposerHint();
  cRenderItemList();
  cRenderConversation();
  focusComposer();
  cToast('已開啟全新對話（第 1 輪），不會帶入先前對話紀錄', 'ok');
}

// ── 中央對話串（右：用戶提問 / 左：Agent 結果優先 + 預設收合的交叉審查） ─────────
function cSplitResultAndReview(t) {
  let raw = String(t.consensus || '').trim();
  let review = String(t.review_details || '').trim();

  // 1. 移除模型偶發殘留的 <tool_call> / <//tool_call> 或裸工具 JSON，將其歸入可收合的執行明細
  const rawToolMatch = raw.match(/(?:<+\\/?tool_call>\\s*)?\\{\\s*"tool"\\s*:\\s*"[^"]+"[\\s\\S]*?\\}\\s*(?:<+\\/+?tool_call>|$)/g);
  if (rawToolMatch && rawToolMatch.length) {
    const toolDump = rawToolMatch.join('\\n');
    raw = raw.replace(/(?:<+\\/?tool_call>\\s*)?\\{\\s*"tool"\\s*:\\s*"[^"]+"[\\s\\S]*?\\}\\s*(?:<+\\/+?tool_call>|$)/g, '')
             .replace(/<+\\/+?tool_call>/g, '').trim();
    if (!review) review = '⚡ 【工具呼叫紀錄】\\n' + toolDump;
    else if (review.indexOf(toolDump.slice(0, 60)) === -1) review += '\\n\\n⚡ 【工具呼叫紀錄】\\n' + toolDump;
  }

  // 2. 分離 <!-- KHAVIS_REVIEW_SPLIT --> 標記
  if (raw.indexOf('<!-- KHAVIS_REVIEW_SPLIT -->') !== -1) {
    const parts = raw.split('<!-- KHAVIS_REVIEW_SPLIT -->');
    raw = (parts[0] || '').trim();
    const tail = parts.slice(1).join('\\n').trim();
    if (tail) review = review ? (review + '\\n\\n' + tail) : tail;
  }

  // 3. 若含有「⚡ **【自主工具執行紀錄」區塊，自動移入下方收合的審查與執行明細，避免主對話氣泡出現滿滿的指令碼
  const logMarker = '⚡ **【自主工具執行紀錄';
  if (raw.indexOf(logMarker) !== -1) {
    const idxLog = raw.indexOf(logMarker);
    const beforeLog = raw.slice(0, idxLog).trim();
    const afterLog = raw.slice(idxLog).trim();
    if (beforeLog) {
      raw = beforeLog;
      review = review ? (review + '\\n\\n' + afterLog) : afterLog;
    } else {
      // 舊紀錄把 log 放在最前面：尋找 ``` 結尾後的人類文字
      const fenceParts = afterLog.split('```');
      if (fenceParts.length >= 3) {
        const logOnly = fenceParts.slice(0, 2).join('```') + '```';
        const humanAfter = fenceParts.slice(2).join('```').trim();
        raw = humanAfter || '';
        review = review ? (review + '\\n\\n' + logOnly) : logOnly;
      } else {
        raw = '';
        review = review ? (review + '\\n\\n' + afterLog) : afterLog;
      }
    }
  }

  if (raw) {
    // 若舊紀錄或合流文本中含有「多 Agent 交叉審查」段落與「最終完成交付成果」段落，自動分離並確保「結果優先」
    const idxReview = raw.indexOf('【🔍 多 Agent 交叉審查');
    const idxFinal = raw.indexOf('【🏆 最終完成交付成果】');
    if (idxReview !== -1 && idxFinal !== -1) {
      if (idxReview < idxFinal) {
        const extracted = raw.slice(idxReview, idxFinal).trim();
        raw = raw.slice(idxFinal).replace(/^1?\\.\\s*\\*\\*【🏆 最終完成交付成果】\\*\\*[:：]?\\s*/, '【🏆 最終完成交付成果】\\n').trim();
        if (!review) review = extracted;
      } else {
        const extracted = raw.slice(idxReview).trim();
        raw = raw.slice(0, idxReview).trim();
        if (!review) review = extracted;
      }
    } else if (idxReview !== -1) {
      const extracted = raw.slice(idxReview).trim();
      const head = raw.slice(0, idxReview).trim();
      if (head) {
        raw = head;
        if (!review) review = extracted;
      }
    }
  }

  if (!raw && review && t.status === 'done') {
    raw = '✅ 任務已完成，此輪沒有額外的文字說明。如需查看執行細節，可在上方開啟「顯示進階明細」。';
  }

  if (!review && Array.isArray(t.agents) && t.agents.length > 0 && t.status === 'done') {
    review = `參與協同與交叉審查節點：${t.agents.map(a => C_AGENT_LABELS[a] || a).join(' + ')}` +
             (t.source ? `\n收斂來源：${t.source}` : '') +
             (t.duration ? `\n總耗時：${t.duration}` : '');
  }
  return { mainResult: raw, reviewText: review };
}

// ── 進階明細開關：一般用戶只看成果；開啟後才顯示 Agent、耗時、審查與 Diff ──
let cAdvanced = false;
try { cAdvanced = localStorage.getItem('khavis.advanced') === '1'; } catch (_) {}

function cToggleAdvanced() {
  cAdvanced = !cAdvanced;
  try { localStorage.setItem('khavis.advanced', cAdvanced ? '1' : '0'); } catch (_) {}
  document.body.classList.toggle('c-adv', cAdvanced);
  cRenderConversation();
}

// ── 成果文件：位於「以此任務接續」與「專案檔案 Diff」之間、預設展示的 Output 產出文檔下載欄 ──
const cFileCache = Object.create(null);   // full_path -> 文件內容（避免重複請求）
const cFileOpen = Object.create(null);    // `${taskId}|${path}` -> 使用者是否展開
const C_FILE_PREVIEW_CHARS = 20000;

function cGetTurnOutputFiles(t) {
  const direct = Array.isArray(t && t.outputFiles) ? t.outputFiles.filter(f => f && f.path && !String(f.path).endsWith('/')) : [];
  if (direct.length) return direct;
  if (typeof cAllArtifactsCache !== 'undefined' && Array.isArray(cAllArtifactsCache) && cAllArtifactsCache.length) {
    const wsOutputs = cAllArtifactsCache.filter(item => {
      const rp = String(item.rel_path || item.path || '');
      return new RegExp('(?:^|/)(?:deliverables|output|outputs)/', 'i').test(rp);
    });
    if (wsOutputs.length) {
      return wsOutputs.map(item => ({
        path: item.rel_path || item.name || item.path,
        full_path: item.path || item.rel_path,
        status: 'A',
        size: item.size || '',
        badge: item.badge || ''
      }));
    }
  }
  return [];
}

function cFileExtBadge(pathStr, fallbackBadge) {
  if (fallbackBadge) return String(fallbackBadge).toUpperCase();
  const m = String(pathStr || '').match(/\\.([A-Za-z0-9]+)$/);
  return m ? m[1].toUpperCase() : 'OUTPUT';
}

function cOutputFilesHtml(t) {
  const files = cGetTurnOutputFiles(t);
  if (!files.length) return '';   // 本輪沒有產出文檔 → 不顯示任何區塊
  const rows = files.map(f => {
    const full = f.full_path || f.path;
    const baseName = String(f.path || '').split('/').pop() || f.path;
    const badge = cFileExtBadge(f.path, f.badge);
    const dlUrl = '/api/workbench/artifacts/download?path=' + encodeURIComponent(full);
    return `<div class="v-turn-doc-row">
      <span class="v-doc-badge" data-ext="${escapeHtml(badge)}">${escapeHtml(badge)}</span>
      <span class="v-doc-name" title="${escapeHtml(f.path)}">${escapeHtml(baseName)}</span>
      <span class="v-doc-size">${escapeHtml(f.size || '')}</span>
      <a href="${escapeHtml(dlUrl)}" download="${escapeHtml(baseName)}" class="v-dl"
         title="下載此文檔至電腦" onclick="event.stopPropagation()">下載</a>
      <button type="button" class="v-pv" title="預覽內容"
              onclick="openArtifact('${escapeHtml(full)}','${escapeHtml(baseName)}')">預覽</button>
    </div>`;
  }).join('');
  return `<div class="v-turn-docs c-output-download-panel">
    <div class="v-turn-docs-head">
      <span>📎</span><span>本輪產出文檔 · ${files.length} 份</span>
      <span class="flex-1"></span>
      <button type="button" class="hover:underline" style="color:var(--v-amber)" onclick="cOpenPage('artifacts')">資料庫歸檔 →</button>
    </div>
    ${rows}
  </div>`;
}

async function cLoadFileBody(det) {
  const path = det.getAttribute('data-path') || '';
  const body = det.querySelector('.c-file-body');
  if (!path || !body) return;
  if (cFileCache[path] != null) { body.textContent = cFileCache[path]; return; }
  try {
    const res = await fetch('/api/workbench/artifacts/read?path=' + encodeURIComponent(path));
    const data = await res.json();
    if (data && data.ok) {
      let text = String(data.content || '');
      if (text.length > C_FILE_PREVIEW_CHARS) {
        text = text.slice(0, C_FILE_PREVIEW_CHARS) + `\n\n…（內容較長，僅顯示前 ${C_FILE_PREVIEW_CHARS} 字，請按「預覽內容」查看全文）`;
      }
      cFileCache[path] = text || '（此文件是空的）';
      body.textContent = cFileCache[path];
    } else {
      body.textContent = '無法顯示此文件：' + ((data && data.error) || '未知原因');
    }
  } catch (err) {
    body.textContent = '讀取文件失敗：' + err.message;
  }
}

function cOnFileToggle(det) {
  const key = det.getAttribute('data-key') || '';
  if (key) cFileOpen[key] = det.open;
  if (det.open) cLoadFileBody(det);
}

// 重新渲染後，把預設展開的文件內容補載進來
function cHydrateOpenFiles() {
  document.querySelectorAll('details.c-file[open]').forEach(cLoadFileBody);
}

function cTurnHtml(t, idx) {
  const st = cStatusMeta(t.status);
  const id = escapeHtml(t.id || '');
  const roundNum = t.step || (idx + 1);
  const agents = (t.agents || []).map(a => {
    const hex = (C_AGENT_COLORS[a] || [])[1] || '#7C9BB5';
    return `<span class="c-conn-badge" style="background:${hex}26;color:${hex}">${escapeHtml(C_AGENT_LABELS[a] || a)}</span>`;
  }).join('');
  const meta = [];
  if (t.mode) meta.push(escapeHtml(t.mode));
  if (t.duration) meta.push(escapeHtml(t.duration));
  if (t.project) meta.push(escapeHtml(t.project));

  let footerFlow = '';
  if (t.status === 'running') {
    footerFlow = `<div class="c-turn-actions">
      <button type="button" onclick="cOpenExec()" class="c-action m-btn-ghost">明細</button>
      <button type="button" onclick="cAbortTask()" class="c-action"
        style="background:var(--m-rose-soft);color:var(--m-rose-text);border:1px solid rgba(188,139,139,.35)">中止</button>
    </div>`;
  } else if (t.status === 'failed' || t.status === 'aborted') {
    footerFlow = `<div class="c-turn-actions">
      <button type="button" onclick="cContinue('${id}')" class="c-action m-btn-primary font-semibold">重試</button>
    </div>`;
  } else {
    footerFlow = `<div class="c-turn-footer-flow">
      <div class="c-turn-actions">
        <button type="button" onclick="cContinue('${id}')" class="c-action transition"
          style="background:transparent;color:#8FACBF;border:1px solid rgba(124,155,181,.38)">以此任務接續</button>
      </div>
      ${cOutputFilesHtml(t)}
      <div class="c-turn-actions">
        <button type="button" onclick="cOpenTurnDetail('${id}')" class="c-action m-btn-ghost"
                title="角色流水線、交叉審查與比較明細（全頁檢視）">🔍 執行明細</button>
        <button type="button" onclick="cShowDiff()" class="c-action m-btn-ghost">專案檔案 Diff</button>
      </div>
    </div>`;
  }

  const { mainResult, reviewText } = cSplitResultAndReview(t);

  let result = '';
  if (mainResult) {
    result = `<div class="c-turn-bubble">${escapeHtml(mainResult)}</div>`;
  } else if (t.status === 'running') {
    result = `<div class="c-turn-bubble" style="color:var(--m-mist)"><span class="inline-flex items-center gap-1 mr-1.5" aria-hidden="true"><span class="m-dot"></span><span class="m-dot"></span><span class="m-dot"></span></span>⏳ Agent 團隊正在雲端主機與專案工作區執行任務中，完成後將自動顯示結果…</div>`;
  } else if (t.body) {
    result = `<div class="c-turn-bubble">${t.body}</div>`;
  } else {
    result = `<div class="c-turn-bubble" style="color:var(--m-faint)">（此輪沒有產出成果）</div>`;
  }

  // 審查明細只在「進階明細」開啟時才提供；一般用戶只看到成果
  const reviewDrawer = (cAdvanced && reviewText) ? `
    <details class="c-review-collapse m-collapse">
      <summary>
        <span class="inline-flex items-center gap-1.5">
          <svg class="m-caret" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>
          <span>🔍 點選展開 / 隱藏 Agent 交叉審查與比較明細</span>
        </span>
        <span class="text-[10px] font-mono" style="color:var(--m-faint)">預設收起 · 點擊展開</span>
      </summary>
      <div class="c-review-body">${escapeHtml(reviewText)}</div>
    </details>` : '';

  return `<div class="c-turn" data-round="${roundNum}">
    <div class="c-turn-divider">
      <span class="px-2 py-0.5 rounded font-bold" style="background:var(--m-mist-soft);color:var(--m-mist)">第 ${roundNum} 輪問答</span>
      <span>${escapeHtml(t.day || '')} ${escapeHtml(t.time || '')}</span>
      <span class="flex-1"></span>
      <span style="color:${st.color}">${st.label}</span>
    </div>

    <!-- 右邊：用戶提問 -->
    <div class="c-turn-user">
      <div class="c-turn-label">
        <span class="c-conn-badge" style="background:transparent;color:var(--m-faint)">${escapeHtml(t.time || '')}</span>
        <span class="c-conn-badge" style="background:var(--m-mist-soft);color:var(--m-mist)">👤 你的提問</span>
      </div>
      <div class="c-turn-bubble">${escapeHtml(t.prompt || '')}</div>
    </div>

    <!-- 左邊：只呈現成果（回覆內容 + 位於「以此任務接續」與「專案檔案 Diff」間預設展示的 Output 產出文檔欄） -->
    <div class="c-turn-agent">
      <div class="c-turn-label">
        <span class="c-conn-badge" style="background:var(--m-sage-soft);color:var(--m-sage)">📦 成果</span>
        ${cAdvanced ? agents : ''}
        ${cAdvanced ? meta.map(m => `<span class="c-conn-badge" style="background:transparent;color:var(--m-faint)">${m}</span>`).join('') : ''}
      </div>
      ${result}
      ${reviewDrawer}
      ${footerFlow}
    </div>
  </div>`;
}

function cRenderConversation() {
  const head = document.getElementById('c-conv-header');
  const turns = document.getElementById('c-conv-turns');
  const conv = document.getElementById('c-conversation');
  const stream = document.getElementById('c-task-stream');
  const slot = document.getElementById('c-live-slot');
  if (!head || !turns || !conv) return;

  const overview = cCenterMode === 'overview';
  if (conv) conv.classList.toggle('hidden', overview);
  if (stream) stream.classList.toggle('hidden', !overview);
  if (overview) {
    head.classList.remove('hidden');
    head.innerHTML = `<span class="c-conv-title">任務總覽</span>
      <span class="c-conn-badge" style="background:transparent;color:var(--m-faint)">${cTasks.length} 個任務 · ${cThreads.size} 個對話串</span>
      <span class="flex-1"></span>
      <button type="button" onclick="cSetCenterMode('thread')" class="c-action m-btn-ghost">回到對話串</button>`;
    return;
  }

  const th = cSelectedThread ? cThreads.get(cSelectedThread) : null;
  if (!th) {
    head.innerHTML = `<span class="c-conv-title">✨ 新對話（第 1 輪）</span>
      <span class="c-conn-badge" style="background:var(--m-sage-soft);color:var(--m-sage)">獨立新任務 · 不帶舊對話記憶</span>
      <span class="flex-1"></span>
      <button type="button" onclick="cSetCenterMode('overview')" class="c-action m-btn-ghost">任務總覽</button>`;
    turns.innerHTML = `<div class="rounded-2xl p-6 text-center m-line" style="background:var(--m-panel-2);color:var(--m-dim)">
      <div class="text-sm font-semibold mb-1" style="color:var(--m-bone)">✨ 已準備好開始全新的第 1 輪對話</div>
      <div class="text-xs" style="color:var(--m-faint)">請直接在下方輸入框輸入您的問題或目標，Agent 團隊將獨立執行並呈現結果（不會混入先前的舊對話）。</div>
    </div>`;
    if (slot) slot.classList.add('hidden');
    return;
  }

  const agents = th.agents.map(a => {
    const hex = (C_AGENT_COLORS[a] || [])[1] || '#7C9BB5';
    return `<span class="c-conn-badge" style="background:${hex}26;color:${hex}">${escapeHtml(C_AGENT_LABELS[a] || a)}</span>`;
  }).join('');

  head.innerHTML = `<span class="c-conv-title truncate">${escapeHtml(th.title)}</span>
    <span class="c-conn-badge" style="background:transparent;color:var(--m-faint)">${th.turns} 輪</span>
    ${cAdvanced ? agents : ''}
    ${(cAdvanced && th.project) ? `<span class="c-conn-badge" style="background:transparent;color:var(--m-faint)">${escapeHtml(th.project)}</span>` : ''}
    ${(cAdvanced && th.mode) ? `<span class="c-conn-badge" style="background:var(--m-mist-soft);color:var(--m-mist)">${escapeHtml(th.mode)}</span>` : ''}
    <span class="flex-1"></span>
    <button type="button" onclick="cToggleAdvanced()" class="c-action m-btn-ghost" aria-pressed="${cAdvanced ? 'true' : 'false'}" title="顯示或隱藏 Agent 名稱、耗時、交叉審查等技術細節（預設隱藏，只看成果）">${cAdvanced ? '🔍 隱藏進階明細' : '🔍 顯示進階明細'}</button>
    <button type="button" onclick="cSetCenterMode('overview')" class="c-action m-btn-ghost">任務總覽</button>`;

  turns.innerHTML = th.tasks.map((t, i) => cTurnHtml(t, i)).join('');
  cHydrateOpenFiles();
  // live slot：只有「本頁面剛派工的那一串」才顯示 legacy 的 hero card，
  // 否則會顯示跟目前對話串無關的成果。
  if (slot) {
    const showSlot = !!cLiveThreadId && cLiveThreadId === th.id;
    slot.classList.toggle('hidden', !showSlot);
  }
}

function cSetCenterMode(mode) {
  cCenterMode = mode === 'overview' ? 'overview' : 'thread';
  switchTab('command');
  cRenderConversation();
  cRenderStream();
}

function cSetView(key) {
  cViewKey = C_VIEWS[key] ? key : 'timeline';
  const v = C_VIEWS[cViewKey];
  const label = document.getElementById('c-view-label');
  if (label) label.textContent = v.label;
  document.querySelectorAll('.c-menu-item[data-view]').forEach(b => {
    b.classList.toggle('c-on', b.dataset.view === cViewKey);
  });
  cToggleViewMenu(false);
  cRenderItemList();
}

function cToggleViewMenu(force) {
  const menu = document.getElementById('c-view-menu');
  if (!menu) return;
  const show = (typeof force === 'boolean') ? force : menu.classList.contains('hidden');
  menu.classList.toggle('hidden', !show);
  const btn = document.getElementById('c-view-btn');
  if (btn) btn.setAttribute('aria-expanded', show ? 'true' : 'false');
  if (show) cToggleProjectMenu(false);
}

function cToggleProjectMenu(force) {
  const menu = document.getElementById('c-project-menu');
  if (!menu) return;
  const show = (typeof force === 'boolean') ? force : menu.classList.contains('hidden');
  menu.classList.toggle('hidden', !show);
  const btn = document.getElementById('c-project-btn');
  if (btn) btn.setAttribute('aria-expanded', show ? 'true' : 'false');
  if (show) cToggleViewMenu(false);
}

function cToggleSearch() {
  const row = document.getElementById('c-search-row');
  if (!row) return;
  const show = row.classList.contains('hidden');
  row.classList.toggle('hidden', !show);
  if (show) {
    const inp = document.getElementById('c-list-search');
    if (inp) inp.focus();
  } else {
    cFilterList('');
  }
}

function cFilterList(q) {
  cSearchQuery = q || '';
  cRenderItemList();
}

// ── Agent 選取：composer 的 chip 與拓撲面板的 checkbox 雙向同步 ──────
function cToggleAgent(id, on) {
  const cb = document.querySelector('input[name="agent_target"][value="' + id + '"]');
  if (!cb) return;
  cb.checked = !!on;
  syncTopologyBeams(!!cRunningTask);
  cSyncAgentChips();
}

function cSyncAgentChips() {
  // chip 的選中態是 inline style，因此直接重繪最簡單也最不易出錯
  cRenderAgentChips();
  if (typeof cRenderAgentStatusPanel === 'function') cRenderAgentStatusPanel();
}

const C_AGENT_LABELS = {
  claude:'Claude', codex:'Codex', antigravity:'AGY',
  minimax:'minimax', opencode:'opencode', khavis:'K.A.V.I.S.',
  'kavis-tencent':'K.A.V.I.S. (Tencent)'
};

function cRenderAgentChips() {
  const host = document.getElementById('c-agent-chips');
  if (!host) return;
  const seen = [];
  document.querySelectorAll('input[name="agent_target"]').forEach(b => {
    if (!seen.some(v => v.value === b.value)) seen.push(b);
  });
  host.innerHTML = seen.map(b => {
    const hex = (C_AGENT_COLORS[b.value] || [])[1] || '#7C9BB5';
    const label = C_AGENT_LABELS[b.value] || b.value;
    const on = b.checked;
    // 切換目標在渲染時就算好：原本用 classList.contains('m-btn-primary') 判斷，
    // 而 chip 從來沒有這個 class → 點擊永遠只能選取、無法取消。
    return `<button type="button" class="c-agent-chip px-2 py-0.5 rounded-md text-[11px] transition inline-flex items-center gap-1"
              data-agent="${b.value}" aria-pressed="${on ? 'true' : 'false'}"
              onclick="cToggleAgent('${b.value}', ${on ? 'false' : 'true'})"
              style="background:${on ? hex + '2E' : 'var(--m-faint-bg)'};color:${on ? hex : 'var(--m-dim)'};border:1px solid ${on ? hex + '66' : 'var(--m-line)'}"
              title="${label} — ${on ? '已選取，點擊取消' : '點擊選取'}">${on ? '<span aria-hidden="true">✓</span>' : ''}${label}</button>`;
  }).join('')
  + `<button type="button" onclick="cAddAgent()" class="px-2 py-0.5 rounded-md text-[11px] m-btn-ghost transition" title="掛載外部 CLI / API Agent">＋ 加入</button>`;
}

function cAddAgent() {
  // 拓撲／自訂 Agent 面板已移到「Agent 狀態」專頁，先切過去再展開
  cOpenPage('agents');
  const d = document.getElementById('c-topology-collapse');
  if (d) {
    d.open = true;
    setTimeout(() => d.scrollIntoView({ behavior:'smooth', block:'center' }), 80);
  }
  const p = document.getElementById('custom-agent-panel');
  if (p) p.classList.remove('hidden');
}

function cSelectProject(name) {
  const sel = document.getElementById('ws-project-select');
  if (sel && sel.value !== name) {
    sel.value = name;
    switchSharedProject(name);
  }
  if (typeof currentProjectName !== 'undefined') currentProjectName = name;
  const active = document.getElementById('c-active-project');
  if (active) active.textContent = name;
  document.querySelectorAll('#c-project-menu .c-menu-item').forEach(b => {
    b.classList.toggle('c-on', (b.getAttribute('data-project') || '') === name);
  });
  cUpdateComposerHint();
  cToggleSidebar(false);
}

function focusComposer() {
  switchTab('command');
  const el = document.getElementById('prompt-input');
  if (el) { el.focus(); el.scrollIntoView({ behavior:'smooth', block:'nearest' }); }
}

function cUpdateComposerHint() {
  const h = document.getElementById('c-composer-hint');
  const badge = document.getElementById('c-context-badge');
  const proj = (typeof currentProjectName !== 'undefined' && currentProjectName) ? currentProjectName : 'shared-app';
  const isContinuing = Boolean(cThreadId && (cThreadStep || 0) > 0);
  if (h) {
    const ctx = isContinuing
      ? ' · 接續 ' + cThreadId.slice(0, 8) + ' 第 ' + ((cThreadStep || 0) + 1) + ' 輪'
      : ' · ✨ 全新對話（第 1 輪）';
    h.textContent = proj + ctx + ' · ⌘/Ctrl+⏎ 執行 · ⏎ 換行';
  }
  if (badge) {
    if (isContinuing) {
      badge.classList.remove('hidden');
      badge.innerHTML = `<button type="button" onclick="cNewTask()" class="px-2 py-0.5 rounded bg-amber-500/20 hover:bg-rose-500/30 text-amber-200 border border-amber-500/40 text-[10.5px] font-mono transition">🔗 正在接續第 ${(cThreadStep || 0) + 1} 輪 · ✕ 點此改開新對話</button>`;
    } else {
      badge.classList.add('hidden');
      badge.innerHTML = '';
    }
  }
}

// ── 任務卡片流 ──────────────────────────────────────────────────────
function cStatusMeta(status) {
  if (status === 'running') return { color:'var(--m-mist)', label:'執行中', live:true };
  if (status === 'failed')  return { color:'var(--m-rose)', label:'失敗', live:false };
  if (status === 'aborted') return { color:'var(--m-apricot)', label:'已中止', live:false };
  return { color:'var(--m-sage)', label:'已完成', live:false };
}

function cAgentDots(agents) {
  return (agents || []).map(a => {
    const [, hex] = C_AGENT_COLORS[a] || ['--m-mist', '#7C9BB5'];
    const initial = (a || '?').charAt(0).toUpperCase();
    return `<span class="w-4 h-4 rounded-full grid place-items-center text-[8px] font-bold"
              style="background:${hex}26;color:${hex};border:1px solid ${hex}55" title="${a}">${initial}</span>`;
  }).join('');
}

function cTaskActions(t, expanded) {
  const id = escapeHtml(t.id || '');
  if (t.status === 'running') {
    return `<button type="button" onclick="cOpenExec()" class="c-action m-btn-ghost">明細</button>
            <button type="button" onclick="cAbortTask()" class="c-action"
              style="background:var(--m-rose-soft);color:var(--m-rose-text);border:1px solid rgba(188,139,139,.35)">中止</button>`;
  }
  if (t.status === 'failed' || t.status === 'aborted') {
    return `<button type="button" onclick="cToggleExpand('${id}')" class="c-action m-btn-ghost">${expanded ? '收合' : '看原因'}</button>
            <button type="button" onclick="cRerun('${id}')" class="c-action m-btn-primary font-semibold">重試</button>`;
  }
  return `<button type="button" onclick="cContinue('${id}')" class="c-action transition"
            style="background:transparent;color:#8FACBF;border:1px solid rgba(124,155,181,.38)">以此任務接續</button>
          <button type="button" onclick="cRerun('${id}')" class="c-action m-btn-ghost">重新執行</button>`;
}

function cTaskCard(t, opts) {
  const expanded = !!(opts && opts.expanded);
  const agents = t.agents || [];
  const st = cStatusMeta(t.status);
  const id = escapeHtml(t.id || '');

  const meta = [];
  if (t.id) meta.push(`<span class="shrink-0">${id}</span>`);
  if (t.project) meta.push(`<span class="shrink-0" style="color:var(--m-line)">|</span><span class="truncate">${escapeHtml(t.project)}</span>`);
  if (t.duration) meta.push(`<span class="shrink-0" style="color:var(--m-line)">|</span><span class="shrink-0">${escapeHtml(t.duration)}</span>`);
  if (t.tokens) meta.push(`<span class="shrink-0" style="color:var(--m-line)">|</span><span class="shrink-0">${escapeHtml(t.tokens)} tok</span>`);
  if (t.summary) meta.push(`<span class="shrink-0" style="color:var(--m-line)">|</span><span class="truncate" style="color:${st.color}">${escapeHtml(t.summary)}</span>`);

  // 執行中：後端 dispatch 是單次阻塞請求，沒有可回報的階段進度。
  // 原本用 timer 假造「百分比」會在 4 秒內跳到 81%，這裡改成
  // 「不確定進度條 + 真實經過時間 + 真實狀態文字」。
  const liveBlock = t.status === 'running' ? `
    <div class="pt-2.5">
      <div class="h-1 rounded-full overflow-hidden m-indeterminate" style="background:var(--m-track)">
        <div id="c-progress-bar" class="h-full w-full" style="background:linear-gradient(90deg,#7C9BB5,#8FA894)"></div>
      </div>
      <div class="flex items-center gap-2 mt-2">
        <span class="text-[10px] shrink-0" style="color:var(--m-faint)">標準流程</span>
        <div id="c-stage-row" class="grid grid-cols-4 gap-1.5 text-[10.5px] flex-1 min-w-0"></div>
      </div>
      <div class="flex items-center gap-2 mt-2 text-[11px]" style="color:var(--m-dim)">
        <span class="inline-flex items-center gap-0.5 shrink-0" aria-hidden="true"><span class="m-dot"></span><span class="m-dot"></span><span class="m-dot"></span></span>
        <span id="c-live-status" class="truncate">執行中…</span>
        <span id="c-live-timer" class="ml-auto font-mono shrink-0" style="color:var(--m-faint)">0.0s</span>
      </div>
      <div id="c-console" class="mt-2 rounded-lg p-2.5 font-mono text-[10.5px] leading-[1.65] max-h-28 overflow-y-auto m-line"
           style="background:var(--m-panel-3)">
        <div style="color:var(--m-faint)">等待回應…</div>
      </div>
    </div>` : '';

  return `
  <article class="frost m-line rounded-2xl overflow-hidden m-raise c-card ${t.status==='running'?'m-in':''}"
           data-status="${t.status}" data-id="${id}">
    <div class="px-4 py-3">
      <!-- 第一行：狀態點 + 任務標題（標題永遠有自己的整行寬度，不會被動作鈕擠掉） -->
      <div class="flex items-start gap-2.5">
        <span class="w-2 h-2 rounded-full shrink-0 mt-1.5 ${st.live?'m-live':''}"
              style="background:${st.color}" title="${st.label}"></span>
        <div class="flex-1 min-w-0">
          <div class="flex items-start gap-2">
            <h3 class="c-card-title text-sm font-medium leading-snug flex-1 min-w-0"
                style="color:var(--m-bone)" title="${escapeHtml(t.prompt || '')}">${escapeHtml(t.prompt || '系統派工任務')}</h3>
            ${t.mode ? `<span class="m-chip shrink-0 mt-0.5" style="background:var(--m-mist-soft);color:var(--m-mist);border-color:rgba(124,155,181,.25)">${escapeHtml(t.mode)}</span>` : ''}
          </div>
          <div class="c-meta-line flex items-center gap-1.5 mt-1.5 text-[10px] font-mono" style="color:var(--m-faint)">
            ${meta.join(' ')}
            ${agents.length ? `<span class="inline-flex -space-x-1 shrink-0">${cAgentDots(agents)}</span>` : ''}
          </div>
        </div>
        <button type="button" onclick="cToggleExpand('${id}')"
                class="w-7 h-7 shrink-0 rounded-lg m-btn-ghost grid place-items-center transition"
                aria-expanded="${expanded ? 'true' : 'false'}" title="${expanded ? '收合' : '展開'}">
          <svg class="c-caret ${expanded?'c-caret-open':''}" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
      </div>

      ${liveBlock}

      <!-- 第二行：動作列 + 預設展示的 Output 產出文檔下載欄 + 專案檔案 Diff -->
      <div class="mt-2.5 flex items-center gap-1.5 flex-wrap justify-end">
        ${cTaskActions(t, expanded)}
      </div>
      ${t.status === 'done' ? `<div class="mt-2.5">${cOutputFilesHtml(t)}</div>
      <div class="mt-2 flex flex-wrap gap-1.5">
        <button type="button" onclick="cShowDiff()" class="c-action m-btn-ghost">專案檔案 Diff</button>
      </div>` : ''}
    </div>

    <div class="c-body ${expanded ? '' : 'hidden'} px-4 pb-3.5">
      ${t.body || '<div class="text-[11px]" style="color:var(--m-faint)">（此任務沒有額外輸出）</div>'}
      <div class="flex flex-wrap gap-1.5 mt-2.5">
        <button type="button" onclick="cShowDiff()" class="c-action m-btn-ghost">專案檔案 Diff</button>
        <button type="button" onclick="cOpenExec()" class="c-action m-btn-ghost">完整輸出</button>
      </div>
    </div>
  </article>`;
}

function cRenderStream() {
  const host = document.getElementById('c-task-stream');
  if (!host) return;
  const main = document.getElementById('c-main');
  const keepScroll = main ? main.scrollTop : 0;
  const isFailed = t => t.status === 'failed' || t.status === 'aborted';
  const list = cFilterKey === 'all'
    ? cTasks
    : cTasks.filter(t => cFilterKey === 'failed' ? isFailed(t) : t.status === cFilterKey);

  if (!list.length) {
    host.innerHTML = `<div class="text-center py-12" style="color:var(--m-faint)">
      <div class="text-sm mb-1">${cFilterKey==='all'?'尚無任務紀錄':'此篩選沒有任務'}</div>
      <div class="text-xs">在下方輸入你的目標，按 ⏎ 開始第一個任務</div>
    </div>`;
    cUpdateCounts();
    cRenderItemList();
    return;
  }

  let html = '';
  let lastDay = null;
  list.forEach(t => {
    if (t.day !== lastDay) {
      lastDay = t.day;
      html += `<div class="flex items-center gap-3 text-[11px] py-2" style="color:var(--m-faint)">
        <div class="h-px flex-1" style="background:var(--m-line)"></div><span>${escapeHtml(t.day||'')}</span>
        <div class="h-px flex-1" style="background:var(--m-line)"></div></div>`;
    }
    html += cTaskCard(t, { expanded: !!t.expanded });
  });
  host.innerHTML = html;
  cHydrateOpenFiles();
  if (main) main.scrollTop = keepScroll;   // 重新渲染不要把使用者捲回原位
  cUpdateCounts();
  cRenderItemList();
}

function cUpdateCounts() {
  const running = cTasks.filter(t => t.status === 'running').length;
  const done = cTasks.filter(t => t.status === 'done').length;
  const failed = cTasks.filter(t => t.status === 'failed' || t.status === 'aborted').length;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = String(v); };
  set('c-count-running', running);
  set('c-count-done', done);
  set('c-count-failed', failed);
}

function cFilter(key) {
  cFilterKey = key;
  document.querySelectorAll('.c-filter-btn').forEach(b => {
    const on = b.dataset.filter === key;
    b.style.background = on ? 'rgba(124,155,181,.18)' : 'transparent';
    b.style.color      = on ? '#8FACBF' : 'var(--m-dim)';
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  document.querySelectorAll('[data-cfilter]').forEach(b => {
    const on = b.dataset.cfilter === key;
    b.classList.toggle('m-nav-active', on);
    b.classList.toggle('m-nav', !on);
  });
  cRenderStream();
  cRenderItemList();
}

// ── 卡片動作 ────────────────────────────────────────────────────────
function cToggleExpand(id) {
  const t = cTasks.find(x => x.id === id);
  if (!t) return;
  t.expanded = !t.expanded;      // 展開狀態必須存進資料，否則下次重繪就被收合
  cRenderStream();
}

function cContinue(id) {
  const t = cTasks.find(x => x.id === id);
  if (!t) return;
  // 續做哪一串對話，就切到哪一串
  cThreadId = cThreadKeyOf(t);
  cThreadStep = t.step || 1;
  cSelectedThread = cThreadId;
  // 真的帶入上一輪成果讓後端接續（原本只塞了一個 placeholder 字串）
  if (t.consensus) {
    try {
      const prev = (typeof previousTurnContext !== 'undefined' && previousTurnContext) ? previousTurnContext : null;
      previousTurnContext = {
        prompt: t.prompt,
        consensus: t.consensus,
        step: prev ? (prev.step || 1) + 1 : 1
      };
      if (typeof syncContinuationBanner === 'function') syncContinuationBanner();
    } catch (e) { /* 取不到就退回純文字接續 */ }
  }
  focusComposer();
  const el = document.getElementById('prompt-input');
  if (el) {
    el.value = t.prompt || '';
    el.focus();
    try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) {}
  }
  cToast(t.consensus ? '已帶入上一輪脈絡，可直接繼續下指令' : '已載入任務內容，可直接繼續下指令', 'ok');
}

function cRerun(id) {
  const t = cTasks.find(x => x.id === id);
  if (!t) return;
  if (cRunningTask) { cToast('目前已有任務在執行中，請等待完成或先中止', 'err'); return; }
  cThreadId = cThreadKeyOf(t);
  cThreadStep = t.step || 1;
  cSelectedThread = cThreadId;
  const el = document.getElementById('prompt-input');
  if (el) el.value = t.prompt || '';
  focusComposer();
  submitDispatch();
}

function cAbortTask() {
  if (!_cAbortController) { cToast('此任務已結束或無法中止', 'err'); return; }
  cAborted = true;
  try { _cAbortController.abort(); } catch (e) {}
  cToast('已中止等待（後端可能仍在完成任務）', 'err');
}

function cOpenExec() {
  cToggleDrawer(true);
}

function cShowDiff() {
  const ws = document.getElementById('c-workspace-collapse');
  if (ws) {
    ws.open = true;
    if (typeof ws.scrollIntoView === 'function') ws.scrollIntoView({ behavior:'smooth', block:'nearest' });
  }
  const v = document.getElementById('ws-diff-viewer');
  if (v && typeof v.scrollIntoView === 'function') v.scrollIntoView({ behavior:'smooth', block:'center' });
  cToast('已展開工作區的專案檔案 Diff', 'ok');
}

// ── 側欄工作區清單 ──────────────────────────────────────────────────
async function cLoadProjects() {
  const menu = document.getElementById('c-project-menu');
  if (!menu) return;
  try {
    const res = await fetch('/api/workbench/workspace');
    if (!res.ok) return;
    const data = await res.json();
    const active = data.active_project || 'shared-app';
    cActiveProject = active;
    const projects = data.projects || [];
    const activeEl = document.getElementById('c-active-project');
    if (activeEl) activeEl.textContent = active;
    const cnt = document.getElementById('c-project-count');
    if (cnt) cnt.textContent = String(projects.length);
    menu.innerHTML = projects.map(p => `
      <button type="button" data-project="${escapeHtml(p)}"
              onclick="cSelectProject('${escapeHtml(p)}');cToggleProjectMenu(false)"
              class="c-menu-item ${p === active ? 'c-on' : ''}">
        <span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:${p === active ? 'var(--m-mist)' : 'var(--m-faint)'}"></span>
        <span class="truncate">${escapeHtml(p)}</span>
      </button>`).join('');
  } catch (_) { /* workspace 不可用時保留預設項目 */ }
}

// ── 從 /api/workbench/history 載入任務流 ─────────────────────────────
function cToday() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

function cClockNow() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

async function cLoadTasks() {
  try {
    const res = await fetch('/api/workbench/history');
    if (!res.ok) return;
    const data = await res.json();
    const items = data.history || [];
    if (!items.length) { cTasks = []; cRenderStream(); return; }

    cTasks = items.map((h, i) => {
      const ts = h.dispatched_at || '';
      const day = ts.indexOf('-') !== -1 ? ts.split(' ')[0] : '最近';
      // 用完整時間做 handle：同一分鐘內的多次派工必須有不同 id，
      // 否則「以此任務接續 / 重試」會永遠作用在第一張卡上。
      const hms = (ts.match(/\\d{2}:\\d{2}:\\d{2}/) || [''])[0];
      const lat = h.latency_ms != null ? `${(h.latency_ms/1000).toFixed(1)}s` : null;
      const ok = h.ok !== false;
      return {
        id: hms ? ('#' + hms.split(':').join('')) : ('#' + (0x8a3f - i * 0x11).toString(16)),
        prompt: h.prompt || '系統派工任務',
        source: h.consensus_source || 'K.A.V.I.S. Pipeline',
        project: h.project || '',
        thread: h.thread || '',
        step: h.step || 1,
        ts: h.ts || 0,
        day: day,
        time: hms,
        duration: lat,
        status: ok ? 'done' : 'failed',
        agents: Array.isArray(h.backends) ? h.backends : [],
        mode: h.collab_mode || '',
        summary: (h.consensus_source || (ok ? '成功' : '失敗')).slice(0, 28),
        consensus: h.consensus || '',
        review_details: h.review_details || '',
        outputFiles: Array.isArray(h.output_files) ? h.output_files : [],
        expanded: false,
        body: `<div class="rounded-xl p-3 space-y-2 m-line" style="background:var(--m-panel-2)">
                 <div class="text-[11px] font-mono leading-[1.7]" style="color:var(--m-dim)">
                   來源: ${escapeHtml(h.consensus_source || 'Fan-out')}
                   ${lat ? ` · 耗時 ${lat}` : ''}
                   ${h.collab_mode ? ` · 模式 ${escapeHtml(h.collab_mode)}` : ''}
                   ${Array.isArray(h.backends) && h.backends.length ? ` · ${escapeHtml(h.backends.join(' + '))}` : ''}
                 </div>
                 ${h.consensus ? `<div class="text-[11px] font-mono leading-[1.7] whitespace-pre-wrap max-h-40 overflow-y-auto mt-1" style="color:var(--m-dim)">${escapeHtml(h.consensus)}</div>` : ''}
               </div>`
      };
    });

    cRenderStream();
  } catch (_) { /* history 不可用時靜默退回空狀態 */ }
}

// ── 派工生命週期：把既有 submitDispatch 的結果接進任務流 ────────────
// 把對話串識別帶進 legacy 的 previous_context。
// 後端會沿用 thread id 並以 step+1 當作這一輪的輪次；
// build_contextual_prompt 對空的 prompt/consensus 會直接略過，
// 所以第一輪帶一個只有 thread 的 context 不會改變模型收到的 prompt。
function cPrepareThreadContext() {
  cIsNewDraft = false;
  if (!cThreadId || !cThreadStep) {
    cThreadId = 't' + Date.now().toString(36);
    cThreadStep = 0;
    if (typeof previousTurnContext !== 'undefined') {
      previousTurnContext = { prompt:'', consensus:'', thread: cThreadId, step: 0 };
    }
    if (typeof syncContinuationBanner === 'function') syncContinuationBanner();
    return;
  }
  if (typeof previousTurnContext === 'undefined') return;
  if (!previousTurnContext) previousTurnContext = { prompt:'', consensus:'', step:0 };
  previousTurnContext.thread = cThreadId;
  previousTurnContext.step = cThreadStep;
}

function cBeginTask(prompt, agents, mode) {
  // 前綴 r 讓執行中任務的 handle 不會和歷史紀錄（純數字）撞號
  const id = '#r' + (Date.now() % 0xffff).toString(16);
  const task = {
    id, prompt, agents, mode,
    project: (typeof currentProjectName !== 'undefined' ? currentProjectName : 'shared-app'),
    thread: cThreadId || '',
    step: (cThreadStep || 0) + 1,
    ts: Date.now() / 1000,
    day: cToday(), status: 'running', duration: null, time: cClockNow(),
    summary: '執行中', consensus: '', body: '', expanded: false
  };
  cTasks.unshift(task);
  cRunningTask = id;
  cLiveThreadId = task.thread || cLiveThreadId;
  cSelectedThread = task.thread || cSelectedThread;
  cFilter('all');
  cRenderConversation();
  cRenderStream();
  cUpdateComposerHint();
  if (typeof cRenderAgentStatusPanel === 'function') cRenderAgentStatusPanel();
  const main = document.getElementById('c-main');
  if (main) main.scrollTop = task.step > 1 ? main.scrollHeight : 0;
  return id;
}

// 當瀏覽器/代理連線因長任務超時中斷時，背景輪詢 /api/workbench/history 自動取回後端寫入的成果
function cPollRecoverTask(taskId, promptText, threadId) {
  let attempts = 0;
  const timer = setInterval(async () => {
    attempts += 1;
    if (attempts > 15) { clearInterval(timer); return; }
    try {
      const res = await fetch('/api/workbench/history');
      if (!res.ok) return;
      const data = await res.json();
      const items = data.history || [];
      const match = items.find(h =>
        (threadId && h.thread === threadId && h.consensus) ||
        (h.prompt === promptText && h.consensus && (Date.now()/1000 - (h.ts || 0) < 300))
      );
      if (match && match.consensus) {
        clearInterval(timer);
        const card = cTasks.find(t => t.id === taskId);
        if (card) {
          card.status = 'done';
          card.summary = '成功';
          card.consensus = match.consensus;
          card.outputFiles = Array.isArray(match.output_files) ? match.output_files : [];
          card.duration = match.latency_ms != null ? `${(match.latency_ms/1000).toFixed(1)}s` : card.duration;
          card.body = `<div class="rounded-xl p-3 space-y-2 m-line" style="background:var(--m-panel-2)">
            <div class="text-[11px] font-mono whitespace-pre-wrap leading-[1.7] max-h-56 overflow-y-auto" style="color:var(--m-dim)">${escapeHtml(match.consensus)}</div>
          </div>`;
        }
        if (typeof latestFinalDeliverableText !== 'undefined') latestFinalDeliverableText = match.consensus;
        cRenderStream();
        cRenderConversation();
        cSetDrawerDot(true);
        cToast('已從伺服器同步取回剛才完成的任務成果！', 'ok');
      }
    } catch (_) {}
  }, 4000);
}

// 包裹既有的 submitDispatch：任務卡的建立 / 收尾都由這裡驅動，
// 原本的 fetch、collab_mode、previous_context 邏輯完全不動。
const _submitDispatchLegacy = __legacySubmitDispatch;
async function submitDispatch() {
  if (cRunningTask) { cToast('目前已有任務在執行中，請等待完成或先中止', 'err'); return; }
  const el = document.getElementById('prompt-input');
  const prompt = el ? el.value.trim() : '';
  if (!prompt) { cToast('請先輸入任務指示', 'err'); return; }

  // legacy 原本用 alert() 說「請至少在上方拓撲圖點選一個 Agent」，
  // 但 C 版把拓撲面板收進摺疊區，使用者根本看不到該處 → 改成可操作的提示。
  const picked = Array.from(document.querySelectorAll('input[name="agent_target"]:checked')).map(c => c.value);
  if (!picked.length) {
    cToast('請至少選取一個參與 Agent（展開「Agent 拓撲」面板即可勾選）', 'err');
    const topo = document.getElementById('c-topology-collapse');
    if (topo) {
      topo.open = true;
      if (typeof topo.scrollIntoView === 'function') topo.scrollIntoView({ behavior:'smooth', block:'nearest' });
    }
    return;
  }

  cAborted = false;
  cPrepareThreadContext();
  cBeginTask(prompt, picked, typeof currentCollabMode !== 'undefined' ? currentCollabMode : '');

  const startedAt = performance.now();
  const taskId = cRunningTask;
  let secs = '0.0s';
  let ok = false;
  let dispatchError = null;
  let statusText = '';
  try {
    await _submitDispatchLegacy();
    secs = ((performance.now() - startedAt) / 1000).toFixed(1);
    const statusEl = document.getElementById('exec-status-text');
    statusText = statusEl ? String(statusEl.textContent || '') : '';
    ok = statusText.indexOf('✅') !== -1;
  } catch (e) {
    dispatchError = e;
  }

  const card = cTasks.find(t => t.id === taskId);
  if (card) card.duration = secs + 's';

  const consensus = (typeof latestFinalDeliverableText !== 'undefined' && latestFinalDeliverableText)
    ? latestFinalDeliverableText : '';

  const isServerReject = statusText.indexOf('執行失敗:') === 0;
  let body = '';
  if (consensus) {
    body = `<div class="rounded-xl p-3 space-y-2 m-line" style="background:var(--m-panel-2)">
              <div class="text-[11px] font-mono whitespace-pre-wrap leading-[1.7] max-h-56 overflow-y-auto" style="color:var(--m-dim)">${escapeHtml(consensus)}</div>
            </div>`;
  } else if (!ok) {
    // 原本失敗時 body 是空的，使用者只看到「失敗」兩個字，讀不到原因
    const reason = String(statusText || (dispatchError ? (dispatchError.message || dispatchError) : ''))
      .replace(/^執行失敗[:：]\\s*/, '')
      .replace(/^網路或伺服器通訊錯誤[:：]\\s*/, '')
      .trim();
    const errTitle = cAborted ? '已中止等待' : (isServerReject ? '執行未完成' : '連線逾時或執行未完成（正在背景自動同步伺服器成果…）');
    body = `<div class="rounded-xl p-3 m-line" style="background:var(--m-rose-soft);border-color:rgba(188,139,139,.35)">
              <div class="text-[11px] leading-[1.7]" style="color:var(--m-rose-text)">
                <b>${errTitle}</b><br>${escapeHtml(reason || '後端未回報詳細原因')}
              </div>
            </div>`;
  }

  cThreadStep = (cThreadStep || 0) + 1;
  if (typeof previousTurnContext !== 'undefined' && previousTurnContext) {
    previousTurnContext.step = cThreadStep;
    previousTurnContext.thread = cThreadId;
  }
  cFinishTask(ok && !dispatchError, body, consensus);
  cUpdateComposerHint();
  cRenderConversation();

  if (!ok && !cAborted && !isServerReject) {
    cPollRecoverTask(taskId, prompt, cThreadId);
  }

  if (dispatchError) { cToast('派工失敗：' + (dispatchError.message || '未知錯誤'), 'err'); }
  if (dispatchError) throw dispatchError;
  if (!ok) {
    const shortReason = String(statusText || '').replace(/^執行失敗[:：]\\s*/, '').trim();
    cToast(isServerReject ? ('執行失敗：' + (shortReason || '請檢查設定或稍後再試')) : '等待回應逾時，系統將在後端完成後自動回填結果', 'err');
    return;
  }

  cSetDrawerDot(true);  // 抽屜按鈕亮紅點，成果在右側等使用者展開
  cToast('任務完成，成果已產出（右上角可展開執行明細）', 'ok');
}

// 相容性：舊版 DOM 若仍存在 #c-stage-row 就更新它。
// C 版刻意「不」顯示階段百分比 —— 後端 dispatch 是單次阻塞請求，
// 沒有任何可回報的階段進度；原本每 1.1 秒自動前進一階的 timer 會在
// 4 秒內跳到 81%，嚴重誤導。這裡只呈現「標準流程的形狀」，
// 直到真的全部完成才轉為 ✓。
function cStageUpdate(idx) {
  const row = document.getElementById('c-stage-row');
  if (!row) return;
  const done = idx >= 4;                 // 只有「全部完成」是真實訊號
  const labels = ['規劃','實作','互評','驗收'];
  row.innerHTML = labels.map((l) => {
    const color = done ? 'var(--m-sage)' : 'var(--m-faint)';
    const mark  = done ? '✓' : '<span class="m-live" style="color:var(--m-mist)">·</span>';
    return `<div class="flex items-center gap-1 truncate" style="color:${color}">${mark}<span class="truncate">${l}</span></div>`;
  }).join('');
}

function cConsoleLine(html) {
  const box = document.getElementById('c-console');
  if (!box) return;
  box.insertAdjacentHTML('beforeend', `<div class="m-in">${html}</div>`);
  while (box.childElementCount > 8) box.removeChild(box.firstElementChild);
}

// 相容性保留：C 版改用不確定進度條（#c-progress-bar 是 shimmer 軌道），
// 不再設定百分比寬度。
function cProgress(pct) {
  const bar = document.getElementById('c-progress-bar');
  if (bar) bar.style.width = '100%';
}

function cFinishTask(ok, summaryHtml, consensus) {
  const card = cTasks.find(t => t.id === cRunningTask);
  if (card) {
    card.status = ok ? 'done' : (cAborted ? 'aborted' : 'failed');
    card.summary = ok ? '成功' : (cAborted ? '已中止' : '失敗');
    card.body = summaryHtml || '';
    card.consensus = consensus || '';
    card.review_details = (typeof latestReviewDetailsText !== 'undefined' && latestReviewDetailsText) ? latestReviewDetailsText : '';
    // 本輪產出文件：由 legacy submitDispatch 在成功時寫入 latestProducedFiles
    card.outputFiles = (ok && typeof latestProducedFiles !== 'undefined' && Array.isArray(latestProducedFiles))
      ? latestProducedFiles.slice() : [];
    card.expanded = true;
  }
  cRunningTask = null;
  _cAbortController = null;
  cRenderStream();
  if (typeof cRenderAgentStatusPanel === 'function') cRenderAgentStatusPanel();
  if (typeof cLoadSidebarDatabase === 'function') cLoadSidebarDatabase();
  if (typeof cLoadSidebarQuotas === 'function') cLoadSidebarQuotas();
}

// ── 攔截既有 pipeline stepper ──────────────────────────────────────
const _setPipelineStepStateLegacy = __legacySetPipelineStep;
function setPipelineStepState(idx, finished) {
  _setPipelineStepStateLegacy(idx, finished);
  if (cRunningTask) {
    cStageUpdate(finished ? 4 : 0);
    if (finished) cConsoleLine('<span style="color:var(--m-sage)">✓ 協同流程完成</span>');
  }
}

// ── 把 legacy 面板內的真實狀態鏡像到摘要列與執行中的卡片 ────────────
function cMirrorStatus() {
  const map = [['exec-status-text','c-exec-status-text'],
               ['exec-timer','c-exec-timer'],
               ['ws-file-count','c-ws-file-count'],
               ['exec-status-text','c-live-status'],
               ['exec-timer','c-live-timer']];
  map.forEach(([src, dst]) => {
    const a = document.getElementById(src), b = document.getElementById(dst);
    if (a && b && b.textContent !== a.textContent) b.textContent = a.textContent;
  });
}

// ── 右上「Agent 狀態」即時控制面板 ────────────────────────────────
const C_AGENT_ROLES = {
  claude:      'Claude Code · 極速實作與測試',
  codex:       'OpenAI Codex · 高速代碼生成',
  antigravity: 'Antigravity · 自主 Agentic CLI',
  minimax:     'MiniMax Code · 中文與長上下文',
  opencode:    'OpenCode · 跨模型開源編碼 CLI',
  khavis:      'LangGraph 7-Role 協同流水線',
  'kavis-tencent': '騰訊雲 openclaw × hermes 合併遠端節點'
};

function cRenderAgentStatusPanel() {
  const host = document.getElementById('c-agent-status-list');
  if (!host) return;
  const seen = [];
  document.querySelectorAll('input[name="agent_target"]').forEach(b => {
    if (!seen.some(v => v.value === b.value)) seen.push(b);
  });
  ['c-fleet-count', 'c-fleet-count-page'].forEach(cid => {
    const countEl = document.getElementById(cid);
    if (countEl) countEl.textContent = String(seen.length || 7);
  });

  host.innerHTML = seen.map(b => {
    const id = b.value;
    const hex = (C_AGENT_COLORS[id] || [])[1] || '#7C9BB5';
    const label = C_AGENT_LABELS[id] || id;
    const role = C_AGENT_ROLES[id] || '自訂 CLI / HTTP Agent';
    const on = !!b.checked;
    const isRunning = !!cRunningTask && on;
    const statusText = isRunning ? '執行中' : (on ? '已啟用' : '待命');
    const statusColor = isRunning ? 'var(--m-sage)' : (on ? hex : 'var(--m-faint)');
    const curModel = (typeof currentAgentModels !== 'undefined' && currentAgentModels[id])
      ? currentAgentModels[id]
      : (id === 'khavis' ? 'LangGraph 7-Role' : '');

    return `<div class="p-2 rounded-xl m-line transition flex items-center justify-between gap-2"
                 style="background:${on ? hex + '14' : 'var(--m-panel-2)'};border-color:${on ? hex + '55' : 'var(--m-line)'}">
      <button type="button" onclick="cToggleAgent('${escapeHtml(id)}', ${on ? 'false' : 'true'})"
              class="flex items-center gap-2 min-w-0 flex-1 text-left">
        <span class="w-2 h-2 rounded-full shrink-0 ${isRunning ? 'm-live' : ''}"
              style="background:${statusColor}"></span>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-1.5">
            <span class="text-xs font-semibold truncate" style="color:var(--m-bone)">${escapeHtml(label)}</span>
            <span class="text-[10px] font-mono truncate" style="color:var(--m-faint)">${escapeHtml(curModel)}</span>
          </div>
          <div class="text-[10px] truncate" style="color:var(--m-faint)">${escapeHtml(role)}</div>
        </div>
      </button>
      <button type="button" onclick="cToggleAgent('${escapeHtml(id)}', ${on ? 'false' : 'true'})"
              class="px-2 py-0.5 rounded text-[10px] font-mono shrink-0 transition"
              style="background:${on ? hex + '28' : 'var(--m-faint-bg)'};color:${statusColor};border:1px solid ${on ? hex + '55' : 'var(--m-line)'}">
        ${on ? '✓ ' : ''}${statusText}
      </button>
    </div>`;
  }).join('');
}

// ── 資料庫獨立頁面與搜尋過濾 ────────────────────────────────────────
let cAllArtifactsCache = [];
let cArtifactFilterBadge = 'all';

async function cLoadSidebarDatabase() {
  const host = document.getElementById('c-db-list');
  try {
    const res = await fetch('/api/workbench/artifacts');
    if (!res.ok) return;
    const data = await res.json();
    const items = data.artifacts || [];
    cAllArtifactsCache = items;
    const badge = document.getElementById('artifacts-badge');
    if (badge) badge.textContent = String(items.length);
    const pageCount = document.getElementById('c-db-page-count');
    if (pageCount) pageCount.textContent = String(items.length);
    if (typeof cRenderConversation === 'function') cRenderConversation();
    if (!host) return;
    if (!items.length) {
      host.innerHTML = '<div class="text-[11px] py-4 text-center" style="color:var(--m-faint)">尚無資料庫產物</div>';
      return;
    }
    host.innerHTML = items.map(item => `
      <button type="button"
              onclick="openArtifact('${escapeHtml(item.path)}', '${escapeHtml(item.name)}')"
              class="c-item flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0">
          <span class="text-[9px] font-mono px-1.5 py-0.5 rounded shrink-0"
                style="background:var(--m-mist-soft);color:var(--m-mist)">${escapeHtml(item.badge || 'DOC')}</span>
          <span class="text-[11px] truncate" style="color:var(--m-bone)">${escapeHtml(item.name)}</span>
        </div>
        <span class="text-[10px] font-mono shrink-0" style="color:var(--m-faint)">${escapeHtml(item.size || '')}</span>
      </button>
    `).join('');
  } catch (_) {}
}

let cDbOverview = { threads: [], ungrouped: [], total: 0 };

async function cLoadDatabasePage() {
  await cLoadSidebarDatabase();
  const flatGrid = document.getElementById('artifacts-grid');
  if (flatGrid) flatGrid.classList.add('hidden');   // 歸檔視圖取代平面網格
  try {
    const res = await _nativeFetch ? _nativeFetch('/api/workbench/database') : fetch('/api/workbench/database');
    const data = await res.json();
    if (data && data.ok) cDbOverview = { threads: data.threads || [], ungrouped: data.ungrouped || [], total: data.total || 0 };
  } catch (_) {}
  cRenderDatabaseThreads();
}

function cDocRowHtml(f) {
  const full = f.full_path || f.path;
  const baseName = String(f.path || '').split('/').pop() || f.path;
  const badge = cFileExtBadge(f.path, f.badge);
  const dlUrl = '/api/workbench/artifacts/download?path=' + encodeURIComponent(full);
  return `<div class="v-turn-doc-row">
    <span class="v-doc-badge" data-ext="${escapeHtml(badge)}">${escapeHtml(badge)}</span>
    <span class="v-doc-name" title="${escapeHtml(f.path)}">${escapeHtml(baseName)}</span>
    <span class="v-doc-size">${escapeHtml(f.size || '')}</span>
    <a href="${escapeHtml(dlUrl)}" download="${escapeHtml(baseName)}" class="v-dl">下載</a>
    <button type="button" class="v-pv" onclick="openArtifact('${escapeHtml(full)}','${escapeHtml(baseName)}')">預覽</button>
  </div>`;
}

function cRenderDatabaseThreads() {
  const host = document.getElementById('c-db-thread-groups');
  if (!host) return;
  const kw = (cArtifactFilterQuery || '').trim().toLowerCase();
  const badge = (cArtifactFilterBadge || 'all').toUpperCase();
  const matchFile = f => {
    const okBadge = badge === 'all' || String(f.badge || '').toUpperCase() === badge || cFileExtBadge(f.path, f.badge).toUpperCase() === badge;
    const okKw = !kw || String(f.path || '').toLowerCase().includes(kw) || String(f.name || '').toLowerCase().includes(kw);
    return okBadge && okKw;
  };
  const groups = [];
  (cDbOverview.threads || []).forEach(t => {
    const files = (t.files || []).filter(matchFile);
    if (files.length) groups.push({ title: t.title, project: t.project, at: t.dispatched_at, files });
  });
  const ungrouped = (cDbOverview.ungrouped || []).filter(matchFile);
  if (ungrouped.length) groups.push({ title: '📦 未歸檔文件（歷史紀錄找不到來源對話串）', project: '', at: '', files: ungrouped, muted: true });

  const projDots = ['#A99AB5','#7C9BB5','#8FA894','#C9A17E','#BC8B8B','#9FB3C4'];
  let dotIdx = 0;
  host.innerHTML = groups.map(g => {
    const dot = g.muted ? '#64748B' : projDots[dotIdx++ % projDots.length];
    const bodyId = 'v-thread-body-' + Math.abs([...g.title].reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7));
    return `<div class="v-thread-card">
      <div class="v-thread-head" onclick="cToggleThreadGroup('${bodyId}', this)">
        <span class="v-proj-dot" style="background:${dot}"></span>
        <span class="v-thread-title">${escapeHtml(g.title)}</span>
        <span class="v-thread-meta">${g.project ? escapeHtml(g.project) + ' · ' : ''}${g.files.length} 份${g.at ? ' · ' + escapeHtml(g.at) : ''}</span>
        <svg class="m-caret shrink-0" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </div>
      <div class="v-thread-body" id="${bodyId}">${g.files.map(cDocRowHtml).join('')}</div>
    </div>`;
  }).join('') || '<div class="text-[12px] py-8 text-center" style="color:var(--m-faint)">沒有符合的文檔</div>';

  const countEl = document.getElementById('c-db-page-count');
  if (countEl) countEl.textContent = String(groups.reduce((a, g) => a + g.files.length, 0));
}

function cToggleThreadGroup(bodyId, headEl) {
  const body = document.getElementById(bodyId);
  if (!body) return;
  const open = body.classList.toggle('hidden');
  if (headEl) {
    const caret = headEl.querySelector('.m-caret');
    if (caret) caret.style.transform = open ? '' : 'rotate(90deg)';
  }
}

let cArtifactFilterQuery = '';

function cFilterArtifactsPage(queryText, badgeType) {
  if (badgeType !== undefined) cArtifactFilterBadge = badgeType;
  const searchEl = document.getElementById('c-artifact-search');
  const q = String(queryText !== undefined ? queryText : (searchEl ? searchEl.value : '')).trim().toLowerCase();
  cArtifactFilterQuery = q;
  // 對話串歸檔視圖為主要呈現；平面網格保留但隱藏（舊邏輯相容）
  if (typeof cRenderDatabaseThreads === 'function') cRenderDatabaseThreads();
  const grid = document.getElementById('artifacts-grid');
  if (!grid) return;
  if (!cAllArtifactsCache.length) {
    grid.innerHTML = '<div class="text-slate-400 py-8 text-center col-span-3">尚無資料庫產物</div>';
    return;
  }
  const filtered = cAllArtifactsCache.filter(item => {
    const b = String(item.badge || 'DOC').toUpperCase();
    if (cArtifactFilterBadge !== 'all' && b !== cArtifactFilterBadge.toUpperCase()) return false;
    if (!q) return true;
    const hay = `${item.name || ''} ${item.path || ''} ${item.badge || ''}`.toLowerCase();
    return hay.indexOf(q) !== -1;
  });
  if (!filtered.length) {
    grid.innerHTML = '<div class="text-slate-400 py-8 text-center col-span-3">找不到符合篩選條件的資料庫產物</div>';
    return;
  }
  grid.innerHTML = filtered.map(item => `
    <div class="glass-panel p-4 rounded-xl border border-white/10 hover:border-sky-500/40 transition flex flex-col justify-between group">
      <div class="space-y-2">
        <div class="flex items-center justify-between">
          <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-sky-500/20 text-sky-300 font-semibold">
            ${escapeHtml(item.badge || 'DOC')}
          </span>
          <span class="text-[11px] text-slate-400 font-mono">${escapeHtml(item.size)}</span>
        </div>
        <h4 class="font-bold text-white text-sm group-hover:text-sky-400 transition flex items-center gap-2">
          <i class="fas ${item.icon || 'fa-file'} text-sky-400"></i>
          <span class="truncate">${escapeHtml(item.name)}</span>
        </h4>
        <p class="text-[11px] text-slate-400 font-mono truncate" title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</p>
      </div>
      <div class="pt-4 flex items-center justify-between border-t border-white/5 mt-3 gap-2">
        <span class="text-[10px] text-slate-400 font-mono truncate">${escapeHtml(item.mtime)}</span>
        <div class="flex items-center gap-1.5 shrink-0">
          <a href="/api/workbench/artifacts/download?path=${encodeURIComponent(item.path)}" download="${escapeHtml(item.name)}"
             class="px-2.5 py-1 rounded bg-emerald-500/20 hover:bg-emerald-500 hover:text-white text-xs text-emerald-300 border border-emerald-500/30 transition font-medium">
            📥 下載
          </a>
          <button onclick="openArtifact('${escapeHtml(item.path)}', '${escapeHtml(item.name)}')" class="px-2.5 py-1 rounded bg-white/5 hover:bg-sky-500 hover:text-white text-xs text-sky-400 transition font-medium">
            查看內容 &rarr;
          </button>
        </div>
      </div>
    </div>
  `).join('');
}

// ── Agent 狀態 · 模型新增/刪除/調整異動 · 服務商點選新增管理專頁 ────────
// 服務商模型不再全數列舉：改為「從供應商 /models 端點抓取清單 → 使用者勾選要呈現的模型」。
const C_PROVIDER_PRESETS = {
  juapi: {
    label: 'JUAPI TokenRouter',
    env: 'JUAPI_API_KEY',
    base_url_env: 'JUAPI_BASE_URL',
    base_url: 'https://www.juapi.net/v1',
    agents: 'Claude Code (Anthropic Messages) / Codex (OpenAI Responses)'
  },
  openrouter: {
    label: 'OpenRouter 全模型路由',
    env: 'OPENROUTER_API_KEY',
    base_url_env: 'OPENROUTER_BASE_URL',
    base_url: 'https://openrouter.ai/api/v1',
    agents: '所有 Agent 節點 (OpenCode / Codex / Claude / 自訂 HTTP)'
  },
  deepseek: {
    label: 'DeepSeek 官方 API',
    env: 'DEEPSEEK_API_KEY',
    base_url_env: 'DEEPSEEK_BASE_URL',
    base_url: 'https://api.deepseek.com/v1',
    agents: 'OpenCode / Codex / DeepSeek Coder'
  },
  anthropic: {
    label: 'Anthropic Claude 官方 API',
    env: 'ANTHROPIC_API_KEY',
    base_url_env: 'ANTHROPIC_BASE_URL',
    base_url: 'https://api.anthropic.com',
    agents: 'Claude Code CLI 官方直連'
  },
  groq: {
    label: 'Groq 極速推理雲',
    env: 'GROQ_API_KEY',
    base_url_env: 'GROQ_BASE_URL',
    base_url: 'https://api.groq.com/openai/v1',
    agents: 'OpenCode / 高速輕量推理節點'
  },
  xai: {
    label: 'xAI Grok 官方 API',
    env: 'XAI_API_KEY',
    base_url_env: 'XAI_BASE_URL',
    base_url: 'https://api.x.ai/v1',
    agents: 'Codex / OpenCode / Grok 推理節點'
  },
  gemini: {
    label: 'Google Gemini AI Studio',
    env: 'GEMINI_API_KEY',
    base_url_env: 'GEMINI_BASE_URL',
    base_url: 'https://generativelanguage.googleapis.com/v1beta/openai',
    agents: 'Antigravity / Gemini 節點'
  },
  ollama: {
    label: 'Ollama 本地 / 內網推理伺服器',
    env: 'OLLAMA_API_KEY',
    base_url_env: 'OLLAMA_BASE_URL',
    base_url: 'http://127.0.0.1:11434/v1',
    agents: '本地兜底與離線代碼生成'
  },
  custom: {
    label: '',
    env: '',
    base_url_env: '',
    base_url: 'https://',
    agents: '所有 CLI / HTTP Agent 節點'
  }
};

// ── 服務商模型選擇狀態：抓取清單 + 使用者勾選 ─────────────────────────
let cProvModelPool = [];            // 從供應商抓到（或手動加入）的候選模型
let cProvModelSelected = new Set(); // 使用者勾選要呈現（綁定）的模型

function cGetSelectedProvModels() {
  return Array.from(cProvModelSelected);
}

function cResetProvModels() {
  cProvModelPool = [];
  cProvModelSelected = new Set();
  const box = document.getElementById('c-prov-models-box');
  if (box) box.classList.add('hidden');
  const list = document.getElementById('c-prov-models-list');
  if (list) list.innerHTML = '';
  const st = document.getElementById('c-prov-models-status');
  if (st) st.textContent = '尚未抓取 · 填好 API Key 與 Base URL 後即可抓取';
  const cnt = document.getElementById('c-prov-models-count');
  if (cnt) cnt.textContent = '0';
}

function cSetProvSavedModels(modelsStr) {
  const ids = String(modelsStr || '').split(',').map(s => s.trim()).filter(Boolean);
  cProvModelSelected = new Set(ids);
  ids.forEach(id => { if (!cProvModelPool.includes(id)) cProvModelPool.push(id); });
  const box = document.getElementById('c-prov-models-box');
  if (box && ids.length) box.classList.remove('hidden');
  const st = document.getElementById('c-prov-models-status');
  if (st && ids.length) st.textContent = `已同步 ${ids.length} 個現存綁定模型，可抓取完整清單後調整勾選`;
  cRenderProvModelList();
}

function cRenderProvModelList() {
  const list = document.getElementById('c-prov-models-list');
  if (!list) return;
  const filterEl = document.getElementById('c-prov-models-filter');
  const kw = ((filterEl && filterEl.value) || '').trim().toLowerCase();
  const ids = cProvModelPool.filter(id => !kw || id.toLowerCase().includes(kw));
  if (!ids.length) {
    list.innerHTML = '<div class="text-[10px] text-slate-500 py-1">' +
      (cProvModelPool.length ? '沒有符合的模型' : '尚無清單：點「從供應商抓取模型清單」，或於下方手動加入') + '</div>';
  } else {
    list.innerHTML = ids.map(id => `
      <label class="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-white/5 cursor-pointer">
        <input type="checkbox" ${cProvModelSelected.has(id) ? 'checked' : ''} onchange="cToggleProvModel('${escapeHtml(id)}', this.checked)" class="accent-emerald-400 shrink-0" />
        <span class="text-[11px] font-mono text-slate-200 truncate">${escapeHtml(id)}</span>
      </label>`).join('');
  }
  const cnt = document.getElementById('c-prov-models-count');
  if (cnt) cnt.textContent = String(cProvModelSelected.size);
}

function cToggleProvModel(id, on) {
  if (on) cProvModelSelected.add(id);
  else cProvModelSelected.delete(id);
  const cnt = document.getElementById('c-prov-models-count');
  if (cnt) cnt.textContent = String(cProvModelSelected.size);
}

function cAddManualProvModel() {
  const inp = document.getElementById('c-prov-models-manual');
  const id = ((inp && inp.value) || '').trim();
  if (!id) { cToast('請輸入模型 ID', 'err'); return; }
  if (!/^[A-Za-z0-9._:\\-\\/]{1,80}$/.test(id)) { cToast('模型 ID 格式無效（限英數字與 . _ : - /）', 'err'); return; }
  if (!cProvModelPool.includes(id)) cProvModelPool.push(id);
  cProvModelSelected.add(id);
  if (inp) inp.value = '';
  const box = document.getElementById('c-prov-models-box');
  if (box) box.classList.remove('hidden');
  cRenderProvModelList();
  cToast(`已加入模型「${id}」並勾選`, 'ok');
}

async function cFetchProviderModels() {
  const getVal = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const env = getVal('c-prov-env').toUpperCase();
  if (!env) {
    cToast('請先填「金鑰環境變數名稱」（或點選上方服務商範本自動帶入）', 'err');
    return;
  }
  const st = document.getElementById('c-prov-models-status');
  if (st) st.textContent = '⏳ 正在向供應商抓取模型清單…';
  try {
    const res = await fetch('/api/workbench/provider_models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        env,
        base_url: getVal('c-prov-url'),
        base_url_env: getVal('c-prov-url-env').toUpperCase(),
        api_key: getVal('c-prov-key')
      })
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      const reason = data.detail || data.status || ('HTTP ' + res.status);
      if (st) st.textContent = '❌ 抓取失敗：' + reason;
      cToast('抓取模型清單失敗：' + reason, 'err');
      return;
    }
    cProvModelPool = Array.isArray(data.models) ? data.models.slice() : [];
    cProvModelSelected.forEach(id => { if (!cProvModelPool.includes(id)) cProvModelPool.push(id); });
    const box = document.getElementById('c-prov-models-box');
    if (box) box.classList.remove('hidden');
    cRenderProvModelList();
    if (st) st.textContent = `✅ 抓到 ${cProvModelPool.length} 個模型，請勾選要呈現的項目`;
    cToast(`✅ 已從「${data.label || env}」抓到 ${cProvModelPool.length} 個模型`, 'ok');
  } catch (err) {
    if (st) st.textContent = '❌ 連線錯誤：' + err.message;
    cToast('抓取模型清單連線錯誤：' + err.message, 'err');
  }
}

function cFillProviderPreset(presetKey) {
  const p = C_PROVIDER_PRESETS[presetKey] || C_PROVIDER_PRESETS.custom;
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
  setVal('c-prov-label', p.label);
  setVal('c-prov-env', p.env);
  setVal('c-prov-url-env', p.base_url_env);
  setVal('c-prov-url', p.base_url);
  setVal('c-prov-agents', p.agents);
  cResetProvModels();
  const keyInput = document.getElementById('c-prov-key');
  if (keyInput) {
    keyInput.value = '';
    keyInput.focus();
  }
  const formBox = document.getElementById('c-provider-form-box');
  if (formBox && typeof formBox.scrollIntoView === 'function') {
    formBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  cToast(`已帶入「${p.label || '自訂服務商'}」範本，貼上 API Key 後即可抓取並勾選模型！`, 'ok');
}

function cEditExistingProvider(envKey, label, baseUrl, baseUrlEnv, agentsDesc, modelsStr) {
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
  setVal('c-prov-label', label);
  setVal('c-prov-env', envKey);
  setVal('c-prov-url-env', baseUrlEnv);
  setVal('c-prov-url', baseUrl);
  setVal('c-prov-agents', agentsDesc);
  cResetProvModels();
  cSetProvSavedModels(modelsStr);
  const keyInput = document.getElementById('c-prov-key');
  if (keyInput) keyInput.focus();
  const formBox = document.getElementById('c-provider-form-box');
  if (formBox && typeof formBox.scrollIntoView === 'function') {
    formBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  cToast(`正在編輯服務商 ${label} (${envKey})`, 'ok');
}

async function cUpsertProvider() {
  const getVal = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const label = getVal('c-prov-label');
  const env = getVal('c-prov-env').toUpperCase();
  const api_key = getVal('c-prov-key');
  const base_url = getVal('c-prov-url');
  const base_url_env = getVal('c-prov-url-env').toUpperCase();
  const agents = getVal('c-prov-agents') || '通用 Agent 節點';
  const models = cGetSelectedProvModels().join(', ');
  const target_agent = getVal('c-prov-target-agent') || 'all';

  if (!label && !env) {
    cToast('請先點選上方服務商按鈕，或填寫「服務商名稱」與「環境變數名稱」', 'err');
    return;
  }
  try {
    const res = await fetch('/api/workbench/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'upsert_provider',
        label,
        env,
        api_key,
        base_url,
        base_url_env,
        agents,
        models,
        target_agent
      })
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      cToast('儲存服務商失敗：' + (data.detail || '請檢查欄位格式'), 'err');
      return;
    }
    const keyEl = document.getElementById('c-prov-key');
    if (keyEl) keyEl.value = '';
    cToast(`✅ 已成功儲存服務商「${label || env}」${models ? ' 並同步新增模型至選單' : ''}！`, 'ok');
    await cLoadAgentsAndProvidersPage();
  } catch (err) {
    cToast('儲存服務商連線錯誤：' + err.message, 'err');
  }
}

async function cSaveSingleProviderKey(envKey, baseUrlEnv) {
  const keyInp = document.querySelector(`input[data-c-prov-key="${envKey}"]`);
  const urlInp = document.querySelector(`input[data-c-prov-url="${envKey}"]`);
  const keys = {};
  const base_urls = {};
  if (keyInp && keyInp.value.trim()) keys[envKey] = keyInp.value.trim();
  if (urlInp && urlInp.value.trim() && baseUrlEnv) base_urls[baseUrlEnv] = urlInp.value.trim();
  if (!Object.keys(keys).length && !Object.keys(base_urls).length) {
    cToast('請先輸入要更新的 API Key 或 Base URL', 'err');
    return;
  }
  try {
    const res = await fetch('/api/workbench/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keys, base_urls })
    });
    const data = await res.json();
    if (res.ok && data.ok !== false) {
      cToast(`✅ 已更新 ${envKey} 金鑰設定並寫入 .env！`, 'ok');
      await cLoadAgentsAndProvidersPage();
    } else {
      cToast('儲存金鑰失敗：' + (data.detail || '未知錯誤'), 'err');
    }
  } catch (err) {
    cToast('儲存金鑰失敗：' + err.message, 'err');
  }
}

async function cDeleteProvider(envKey, isCustom) {
  try {
    const res = await fetch('/api/workbench/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'delete_provider',
        env: envKey,
        hide_builtin: !isCustom
      })
    });
    if (res.ok) {
      cToast(`🗑️ 已移除服務商 ${envKey} 並清除金鑰`, 'ok');
      await cLoadAgentsAndProvidersPage();
    }
  } catch (err) {
    cToast('移除服務商失敗：' + err.message, 'err');
  }
}

async function cRestoreBuiltinProviders() {
  try {
    const res = await fetch('/api/workbench/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'restore_providers' })
    });
    if (res.ok) {
      cToast('✅ 已還原所有預設服務商欄位', 'ok');
      await cLoadAgentsAndProvidersPage();
    }
  } catch (_) {}
}

// ── 模型 CRUD（直接在頁面上新增、刪除、改名、切換、還原預設） ─────────────
async function cSwitchAgentModel(agentId, modelName) {
  if (!agentId || !modelName || modelName === '__custom__') return;
  try {
    const payload = { models: Object.assign({}, typeof currentAgentModels !== 'undefined' ? currentAgentModels : {}, { [agentId]: modelName }) };
    const res = await fetch('/api/workbench/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (res.ok && data.models) {
      if (typeof currentAgentModels !== 'undefined') {
        Object.assign(currentAgentModels, data.models);
      }
      if (typeof syncModelUIElements === 'function') syncModelUIElements();
      cRenderAgentStatusPanel();
      cToast(`✅ 已將 ${agentId.toUpperCase()} 切換為模型「${modelName}」並寫入 .env`, 'ok');
      await cLoadAgentsAndProvidersPage();
    } else {
      cToast('切換模型失敗：' + (data.detail || '未知錯誤'), 'err');
    }
  } catch (err) {
    cToast('切換模型錯誤：' + err.message, 'err');
  }
}

async function cAddModelOption(agentIdOverride, modelNameOverride) {
  const agentSel = document.getElementById('c-model-crud-agent');
  const modelInp = document.getElementById('c-model-crud-input');
  const agent_id = agentIdOverride || (agentSel ? agentSel.value : 'all') || 'all';
  const model = (modelNameOverride != null ? String(modelNameOverride) : (modelInp ? modelInp.value : '')).trim();
  if (!model) {
    cToast('請輸入要新增的模型名稱（例如 deepseek-chat 或 MiniMax-M3.1-Flash）', 'err');
    return;
  }
  try {
    const res = await fetch('/api/workbench/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'add_model',
        agent_id,
        model,
        set_active: true
      })
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      cToast('新增模型失敗：' + (data.detail || '模型名稱格式不符'), 'err');
      return;
    }
    if (modelInp && !modelNameOverride) modelInp.value = '';
    if (data.models && typeof currentAgentModels !== 'undefined') {
      Object.assign(currentAgentModels, data.models);
    }
    if (typeof syncModelUIElements === 'function') syncModelUIElements();
    cToast(`✅ 已新增模型「${model}」至 ${agent_id === 'all' ? '全部 Agent' : agent_id.toUpperCase()}！`, 'ok');
    await cLoadAgentsAndProvidersPage();
  } catch (err) {
    cToast('新增模型失敗：' + err.message, 'err');
  }
}

async function cAddModelForAgent(agentId) {
  const inp = document.getElementById(`c-agent-new-model-${agentId}`);
  const val = inp ? inp.value.trim() : '';
  if (!val) {
    cToast('請在此 Agent 卡片下方輸入要新增的模型名稱', 'err');
    if (inp) inp.focus();
    return;
  }
  await cAddModelOption(agentId, val);
  if (inp) inp.value = '';
}

async function cDeleteModelOption(agentId, modelName) {
  if (!agentId || !modelName) return;
  try {
    const res = await fetch('/api/workbench/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'delete_model',
        agent_id: agentId,
        model: modelName
      })
    });
    const data = await res.json();
    if (res.ok && data.ok !== false) {
      if (data.models && typeof currentAgentModels !== 'undefined') {
        Object.assign(currentAgentModels, data.models);
      }
      if (typeof syncModelUIElements === 'function') syncModelUIElements();
      cToast(`🗑️ 已從 ${agentId.toUpperCase()} 移除模型「${modelName}」`, 'ok');
      await cLoadAgentsAndProvidersPage();
    } else {
      cToast('刪除模型失敗：' + (data.detail || '未知錯誤'), 'err');
    }
  } catch (err) {
    cToast('刪除模型錯誤：' + err.message, 'err');
  }
}

function cStartRenameModel(agentId, oldModel) {
  const box = document.getElementById(`c-rename-box-${agentId}`);
  const oldInp = document.getElementById(`c-rename-old-${agentId}`);
  const newInp = document.getElementById(`c-rename-new-${agentId}`);
  if (!box || !oldInp || !newInp) return;
  oldInp.value = oldModel;
  newInp.value = oldModel;
  box.classList.remove('hidden');
  newInp.focus();
  newInp.select();
}

async function cConfirmRenameModel(agentId) {
  const oldInp = document.getElementById(`c-rename-old-${agentId}`);
  const newInp = document.getElementById(`c-rename-new-${agentId}`);
  const old_model = oldInp ? oldInp.value.trim() : '';
  const new_model = newInp ? newInp.value.trim() : '';
  if (!old_model || !new_model) {
    cToast('請輸入修改後的模型名稱', 'err');
    return;
  }
  try {
    const res = await fetch('/api/workbench/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'rename_model',
        agent_id: agentId,
        old_model,
        new_model,
        set_active: true
      })
    });
    const data = await res.json();
    if (res.ok && data.ok !== false) {
      if (data.models && typeof currentAgentModels !== 'undefined') {
        Object.assign(currentAgentModels, data.models);
      }
      if (typeof syncModelUIElements === 'function') syncModelUIElements();
      cToast(`✏️ 已將 ${agentId.toUpperCase()} 模型「${old_model}」修改為「${new_model}」`, 'ok');
      await cLoadAgentsAndProvidersPage();
    } else {
      cToast('修改模型名稱失敗：' + (data.detail || '格式不符'), 'err');
    }
  } catch (err) {
    cToast('修改模型錯誤：' + err.message, 'err');
  }
}

async function cResetAllModelsDefault() {
  try {
    const res = await fetch('/api/workbench/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reset: true })
    });
    const data = await res.json();
    if (res.ok && data.models) {
      if (typeof currentAgentModels !== 'undefined') Object.assign(currentAgentModels, data.models);
      if (typeof syncModelUIElements === 'function') syncModelUIElements();
      cToast('✅ 已還原所有 Agent 的系統預設模型與選單！', 'ok');
      await cLoadAgentsAndProvidersPage();
    }
  } catch (err) {
    cToast('還原預設模型失敗：' + err.message, 'err');
  }
}

function cRenderProviderPresets() {
  const host = document.getElementById('c-prov-presets');
  if (!host) return;
  host.innerHTML = Object.keys(C_PROVIDER_PRESETS).map(key => {
    const p = C_PROVIDER_PRESETS[key];
    const name = key === 'custom' ? '＋ 自訂服務商' : '＋ ' + String(p.label).split(' ')[0];
    return `<button type="button" onclick="cFillProviderPreset('${escapeHtml(key)}')"
              class="px-2.5 py-1 rounded-lg bg-white/5 hover:bg-emerald-500/20 border border-white/10 hover:border-emerald-500/40 text-xs font-mono text-slate-200 transition"
              title="${escapeHtml(p.label || '手動輸入所有欄位')}">${escapeHtml(name)}</button>`;
  }).join('');
}

function cFillAgentSelects(agents) {
  // 把目前所有 Agent（含自訂）填進「新增模型」與「新增服務商」的目標下拉選單
  ['c-model-crud-agent', 'c-prov-target-agent'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const prev = sel.value || 'all';
    const adjustable = (agents || []).filter(a => a.adjustable);
    sel.innerHTML = '<option value="all">全部 Agent</option>' +
      adjustable.map(a => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name || a.id)}</option>`).join('');
    sel.value = adjustable.some(a => a.id === prev) ? prev : 'all';
  });
}

async function cLoadAgentsAndProvidersPage() {
  cRenderAgentStatusPanel();
  cRenderProviderPresets();
  const agentsGrid = document.getElementById('agents-fleet-grid');
  const provGrid = document.getElementById('c-providers-crud-grid');

  try {
    const [agRes, setupRes, capRes] = await Promise.all([
      fetch('/api/workbench/agents'),
      fetch('/api/workbench/setup'),
      fetch('/api/workbench/capabilities')
    ]);

    let capabilityData = {};
    if (capRes && capRes.ok) {
      capabilityData = await capRes.json();
      if (typeof currentModelCatalog !== 'undefined') currentModelCatalog = capabilityData.model_catalog || {};
      if (typeof currentModelOptions !== 'undefined') currentModelOptions = capabilityData.model_options || {};
      if (typeof currentAgentModels !== 'undefined') {
        Object.assign(currentAgentModels, capabilityData.active_model_refs || {});
      }
    }

    if (agRes && agRes.ok && agentsGrid) {
      const agData = await agRes.json();
      const agents = agData.agents || [];
      cFillAgentSelects(agents);
      const checkedSet = new Set(
        Array.from(document.querySelectorAll('input[name="agent_target"]:checked')).map(c => c.value)
      );

      agentsGrid.innerHTML = agents.map(a => {
        const isChecked = checkedSet.has(a.id);
        const activeModel = (typeof currentAgentModels !== 'undefined' && currentAgentModels[a.id])
          || (capabilityData.active_model_refs || {})[a.id]
          || a.model;
        const dynamicOptions = capabilityData.model_options || {};
        const opts = Array.isArray(dynamicOptions[a.id]) && dynamicOptions[a.id].length
          ? dynamicOptions[a.id]
          : (Array.isArray(a.options) && a.options.length ? a.options : [activeModel]);
        const optsHtml = opts.map(opt =>
          `<option value="${escapeHtml(opt)}" ${opt === activeModel ? 'selected' : ''}>${escapeHtml(modelRefLabel(opt))}</option>`
        ).join('');

        const pillsHtml = a.adjustable ? opts.map(opt => {
          const active = (opt === activeModel);
          return `
            <div class="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-mono border transition ${active ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40 font-bold' : 'bg-white/5 text-slate-300 border-white/10 hover:border-sky-400/40'}">
              <button type="button" onclick="cSwitchAgentModel('${escapeHtml(a.id)}', '${escapeHtml(opt)}')" title="點擊切換為此模型">
                ${active ? '✓ ' : ''}${escapeHtml(modelRefLabel(opt))}
              </button>
              <button type="button" onclick="cStartRenameModel('${escapeHtml(a.id)}', '${escapeHtml(opt)}')" class="text-slate-400 hover:text-sky-300 px-0.5" title="修改此模型名稱">
                ✏️
              </button>
              <button type="button" onclick="cDeleteModelOption('${escapeHtml(a.id)}', '${escapeHtml(opt)}')" class="text-slate-400 hover:text-rose-400 px-0.5 font-bold" title="刪除此模型選項">
                ×
              </button>
            </div>
          `;
        }).join('') : `<span class="text-xs font-mono text-cyan-300">${escapeHtml(a.model || a.plan)}</span>`;

        const crudControls = a.adjustable ? `
          <div class="space-y-2.5 pt-2 border-t border-white/10">
            <div>
              <div class="flex items-center justify-between text-[11px] text-slate-400 mb-1">
                <span>當前綁定模型 (下拉即切換):</span>
                <button type="button" onclick="cStartRenameModel('${escapeHtml(a.id)}', '${escapeHtml(activeModel)}')" class="text-sky-400 hover:underline text-[10px]">✏️ 修改當前模型名稱</button>
              </div>
              <div class="flex items-center gap-1.5">
                <select onchange="cSwitchAgentModel('${escapeHtml(a.id)}', this.value)" class="flex-1 bg-[#070B14] border border-sky-500/30 rounded-lg px-2.5 py-1.5 text-xs font-mono text-emerald-300 focus:outline-none">
                  ${optsHtml}
                </select>
                  <button type="button" onclick="cDeleteModelOption('${escapeHtml(a.id)}', '${escapeHtml(activeModel)}')" class="px-2.5 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/30 text-rose-300 border border-rose-500/30 text-xs font-mono shrink-0" title="從清單刪除當前選中的模型">
                  🗑️ 刪除
                </button>
              </div>
            </div>

            <!-- 內嵌改名輸入列 (點擊 ✏️ 時展開) -->
            <div id="c-rename-box-${escapeHtml(a.id)}" class="hidden p-2 rounded-lg bg-sky-950/30 border border-sky-500/30 space-y-1.5">
              <div class="text-[10px] text-sky-300 font-mono">修改模型名稱：</div>
              <input id="c-rename-old-${escapeHtml(a.id)}" type="hidden" value="" />
              <div class="flex items-center gap-1.5">
                <input id="c-rename-new-${escapeHtml(a.id)}" type="text" onkeydown="if(event.key==='Enter'){event.preventDefault();cConfirmRenameModel('${escapeHtml(a.id)}');}" class="flex-1 bg-[#050811] border border-sky-500/40 rounded px-2 py-1 text-xs font-mono text-white" />
                <button type="button" onclick="cConfirmRenameModel('${escapeHtml(a.id)}')" class="px-2.5 py-1 rounded bg-sky-500 hover:bg-sky-400 text-slate-950 font-bold text-xs">確認修改</button>
                <button type="button" onclick="document.getElementById('c-rename-box-${escapeHtml(a.id)}').classList.add('hidden')" class="px-2 py-1 rounded bg-white/5 text-slate-400 text-xs">取消</button>
              </div>
            </div>

            <!-- 已勾選模型 (點擊切換 / ✏️ 改名 / × 刪除；清單由供應商勾選維護) -->
            <div>
              <div class="flex items-center justify-between gap-2 mb-1">
                <div class="text-[10px] text-slate-400">已勾選模型 (點名稱切換 · ✏️ 改名 · × 刪除):</div>
                <button type="button" onclick="cOpenAgentModelPicker('${escapeHtml(a.id)}')" class="px-2 py-0.5 rounded-md bg-sky-500/15 hover:bg-sky-500/30 text-sky-300 border border-sky-500/35 text-[10px] font-bold shrink-0 transition"
                        title="從供應商即時清單勾選此 Agent 可用的模型">
                  ⚙️ 從供應商勾選
                </button>
              </div>
              <div class="flex flex-wrap gap-1.5">
                ${pillsHtml}
              </div>
            </div>

            <!-- 直接新增模型至此 Agent -->
            <div class="flex items-center gap-1.5 pt-1">
              <input id="c-agent-new-model-${escapeHtml(a.id)}" type="text" onkeydown="if(event.key==='Enter'){event.preventDefault();cAddModelForAgent('${escapeHtml(a.id)}');}" placeholder="輸入新模型名稱 (如 deepseek-chat)..." class="flex-1 bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs font-mono text-white placeholder-slate-500 focus:outline-none focus:border-emerald-400" />
              <button type="button" onclick="cAddModelForAgent('${escapeHtml(a.id)}')" class="px-3 py-1.5 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/35 text-emerald-300 border border-emerald-500/40 text-xs font-bold shrink-0">
                ＋ 新增模型
              </button>
            </div>
          </div>
        ` : `
          <div class="pt-2 border-t border-white/10 text-xs font-mono text-slate-400">
            架構模式: <span class="text-cyan-300 font-bold">${escapeHtml(modelRefLabel(activeModel || a.plan))}</span>
          </div>
        `;

        const deleteAgentBtn = a.custom ? `
          <button type="button" onclick="deleteCustomAgent('${escapeHtml(a.id)}')" class="px-2 py-0.5 rounded bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/40 text-rose-300 text-[10px] font-mono">
            🗑️ 移除自訂 Agent
          </button>
        ` : '';

        return `
          <div class="glass-panel p-5 rounded-2xl border ${isChecked ? 'border-emerald-500/40' : 'border-white/10'} space-y-3 transition flex flex-col justify-between">
            <div class="space-y-3">
              <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2">
                  <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-white/5 text-slate-300 font-bold">${escapeHtml(a.tier)}</span>
                  ${deleteAgentBtn}
                </div>
                <div class="flex items-center gap-2">
                  <button type="button" onclick="cToggleAgent('${escapeHtml(a.id)}', ${isChecked ? 'false' : 'true'}); setTimeout(cLoadAgentsAndProvidersPage, 80);"
                          class="px-2.5 py-1 rounded-lg text-xs font-mono font-bold transition ${isChecked ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40' : 'bg-white/5 text-slate-400 border border-white/10 hover:text-white'}">
                    ${isChecked ? '✓ 已啟用參與派工' : '○ 待命 (點此啟用)'}
                  </button>
                </div>
              </div>

              <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-sky-500/15 border border-sky-500/30 flex items-center justify-center text-sky-400 text-lg shrink-0">
                  <i class="fas ${escapeHtml(a.icon || 'fa-robot')}"></i>
                </div>
                <div class="min-w-0">
                  <h4 class="text-white font-bold text-base truncate">${escapeHtml(a.name)}</h4>
                  <p class="text-xs text-slate-400 truncate mt-0.5">${escapeHtml(a.desc)}</p>
                </div>
              </div>

              <div class="bg-black/30 p-3.5 rounded-xl text-xs font-mono space-y-2">
                ${crudControls}
                <div class="text-slate-400 flex justify-between pt-2 border-t border-white/5 text-[11px]">
                  <span>執行檔狀態:</span>
                  <span class="text-slate-300 truncate max-w-[200px]">${escapeHtml(a.path || a.version || a.status)}</span>
                </div>
              </div>
            </div>
          </div>
        `;
      }).join('');
    }

    if (setupRes && setupRes.ok && provGrid) {
      const setupData = await setupRes.json();
      const providers = setupData.providers || [];
      provGrid.innerHTML = providers.map(p => {
        const modelsStr = Array.isArray(p.models) ? p.models.join(', ') : '';
        return `
          <div class="p-4 rounded-2xl bg-black/40 border ${p.configured ? 'border-emerald-500/35' : 'border-amber-500/30'} space-y-2.5 flex flex-col justify-between">
            <div class="space-y-2">
              <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2 min-w-0">
                  <span class="font-bold text-white text-sm truncate">${escapeHtml(p.label)}</span>
                  ${p.custom ? '<span class="px-1.5 py-0.5 rounded text-[10px] font-mono bg-purple-500/20 text-purple-300 border border-purple-500/30">自訂服務商</span>' : ''}
                </div>
                <span class="px-2 py-0.5 rounded text-[10px] font-mono shrink-0 ${p.configured ? 'bg-emerald-500/20 text-emerald-300' : 'bg-amber-500/20 text-amber-300'}">
                  ${p.configured ? '✅ 已配置 (' + escapeHtml(p.masked) + ')' : '⚠️ 未設定'}
                </span>
              </div>
              <div class="text-[11px] text-slate-400 font-mono">環境變數: <span class="text-sky-300">${escapeHtml(p.env)}</span> · 適用: ${escapeHtml(p.agents)}</div>
              ${p.default_base_url ? `<div class="text-[11px] text-slate-400 font-mono truncate">Base URL: <span class="text-slate-300">${escapeHtml(p.default_base_url)}</span></div>` : ''}
              ${modelsStr ? `<div class="text-[11px] text-emerald-300/90 font-mono truncate">綁定模型: ${escapeHtml(modelsStr)}</div>` : ''}

              <div class="wizard-input-wrap flex items-center gap-1.5 pt-1">
                <input type="password" data-c-prov-key="${escapeHtml(p.env)}" data-wizard-env="${escapeHtml(p.env)}"
                       placeholder="${p.configured ? '目前: ' + escapeHtml(p.masked) + ' (輸入新 API Key 覆蓋)' : '在此貼上 ' + escapeHtml(p.env) + ' 金鑰...'}"
                       class="flex-1 bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 font-mono text-xs text-emerald-300 placeholder-slate-500 focus:outline-none focus:border-sky-400" />
                <button type="button" onclick="toggleWizardInputVis(this)" class="px-2 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white text-xs" title="顯示/隱藏金鑰">
                  <i class="fas fa-eye"></i>
                </button>
              </div>
              ${p.base_url_env ? `
                <div class="flex items-center gap-1.5">
                  <input type="text" data-c-prov-url="${escapeHtml(p.env)}" value="${escapeHtml(p.default_base_url || '')}"
                         placeholder="API Base URL (${escapeHtml(p.base_url_env)})"
                         class="flex-1 bg-[#050811] border border-white/10 rounded-lg px-2.5 py-1 font-mono text-[11px] text-slate-300 placeholder-slate-500 focus:outline-none focus:border-sky-400" />
                </div>
              ` : ''}
            </div>

            <div class="flex items-center justify-between gap-2 pt-2 border-t border-white/10">
              <div class="flex items-center gap-1.5">
                <button type="button" onclick="cEditExistingProvider('${escapeHtml(p.env)}', '${escapeHtml(p.label)}', '${escapeHtml(p.default_base_url || '')}', '${escapeHtml(p.base_url_env || '')}', '${escapeHtml(p.agents || '')}', '${escapeHtml(modelsStr)}')"
                        class="px-2 py-1 rounded-lg bg-white/5 hover:bg-white/10 text-slate-300 text-[11px] font-mono transition">
                  ✏️ 編輯詳情
                </button>
                <button type="button" onclick="cDeleteProvider('${escapeHtml(p.env)}', ${p.custom ? 'true' : 'false'})"
                        class="px-2 py-1 rounded-lg bg-rose-500/15 hover:bg-rose-500/30 text-rose-300 text-[11px] font-mono transition">
                  🗑️ ${p.custom ? '刪除服務商' : '清除/隱藏'}
                </button>
              </div>
              <button type="button" onclick="cSaveSingleProviderKey('${escapeHtml(p.env)}', '${escapeHtml(p.base_url_env || '')}')"
                      class="px-3 py-1 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs transition">
                💾 儲存金鑰
              </button>
            </div>
          </div>
        `;
      }).join('');
    }
  } catch (_) {}
}

// ── 左下「額度、設定」即時同步 ────────────────────────────────────
async function cLoadSidebarQuotas() {
  try {
    const res = await fetch('/api/quota');
    if (!res.ok) return;
    const data = await res.json();
    const quotas = data.quotas || {};
    const plans = [
      { key: 'MiniMax-M3', short: 'MiniMax', color: 'var(--m-sage)', fallback: 95 },
      { key: 'GLM-5.3', short: 'GLM-5.3', color: 'var(--m-mist)', fallback: 92 },
      { key: 'OpenAI-API', short: 'GPT GO', color: 'var(--m-lilac)', fallback: 90 },
      { key: 'Antigravity-Core', short: 'AGY Pro', color: 'var(--m-sage)', fallback: 98 }
    ];
    let total = 0;
    const pillsHtml = plans.map(p => {
      const q = quotas[p.key] || {};
      const pct = q.is_exhausted ? 5 : (q.remaining_requests != null && q.limit_requests
        ? Math.round((q.remaining_requests / q.limit_requests) * 100)
        : p.fallback);
      total += pct;
      return `<div class="px-2 py-1 rounded m-line flex items-center justify-between" style="background:var(--m-panel-2)">
        <span style="color:var(--m-dim)">${p.short}</span>
        <span style="color:${q.is_exhausted ? 'var(--m-rose)' : p.color}">${pct}%</span>
      </div>`;
    }).join('');
    const avg = Math.round(total / plans.length);
    const pctEl = document.getElementById('c-quota-pct');
    if (pctEl) pctEl.textContent = `額度 ${avg}%`;
    const barEl = document.getElementById('c-quota-bar');
    if (barEl) barEl.style.width = `${avg}%`;
    const pillsEl = document.getElementById('c-sidebar-quota-pills');
    if (pillsEl) pillsEl.innerHTML = pillsHtml;
  } catch (_) {}
}

// ── composer 瘦身：把「快速任務範本」收進摺疊區，省下垂直空間 ──────
function cCompactComposer() {
  const composer = document.querySelector('.composer');
  if (!composer || !composer.querySelectorAll) return;
  let row = null;
  const btns = composer.querySelectorAll('button');
  for (let i = 0; i < btns.length; i++) {
    const oc = btns[i].getAttribute('onclick') || '';
    if (oc.indexOf('setPromptTemplate') !== -1) {
      row = btns[i].parentElement ? btns[i].parentElement.parentElement : null;
      break;
    }
  }
  if (!row || !row.parentElement) return;
  if (row.parentElement.tagName === 'DETAILS') return;   // 已經收過了

  const det = document.createElement('details');
  det.className = 'm-collapse';
  det.id = 'c-template-collapse';
  const sum = document.createElement('summary');
  sum.className = 'px-1 py-1 flex items-center gap-1.5 cursor-pointer text-[11px]';
  sum.style.color = 'var(--m-faint)';
  sum.innerHTML = '<svg class="m-caret" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg><span>快速任務範本</span>';
  det.appendChild(sum);
  row.parentElement.insertBefore(det, row);
  det.appendChild(row);
  const labelEl = row.firstElementChild;
  if (labelEl) labelEl.style.display = 'none';
}

// ── 啟動 ────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.body.classList.toggle('c-adv', cAdvanced);
  cRenderAgentChips();
  cRenderAgentStatusPanel();
  cSetView('timeline');
  cFilter('all');
  // 把 legacy 的成果容器搬到中央對話串（同一個元素，legacy JS 照常寫入）
  try {
    const live = document.getElementById('live-results-container');
    const slot = document.getElementById('c-live-slot');
    if (live && slot && live.parentElement !== slot) slot.appendChild(live);
  } catch (e) { /* 搬不動就維持原位 */ }
  setInterval(cMirrorStatus, 400);
  cLoadTasks();
  cLoadProjects();
  cLoadSidebarDatabase();
  cLoadSidebarQuotas();
  cCompactComposer();
  cUpdateComposerHint();
  cRenderConversation();
  // 依網址決定起始頁：/agents、/database 直接進對應專頁，其餘為對話首頁
  switchTab(cTabFromLocation());
  window.addEventListener('popstate', () => switchTab(cTabFromLocation()));

  // Agent 勾選變動時回寫 composer chip 與右上 Agent 狀態面板
  document.addEventListener('change', e => {
    if (e.target && e.target.name === 'agent_target') {
      cSyncAgentChips();
      cRenderAgentStatusPanel();
      if (typeof loadThinkingCapabilities === 'function') loadThinkingCapabilities();
    }
  });
  // ⌘/Ctrl+Enter 送出，一般 Enter 換行（避免打字或換行時誤觸直接送出執行）
  const pi = document.getElementById('prompt-input');
  if (pi) {
    pi.addEventListener('keydown', e => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submitDispatch(); }
    });
    pi.addEventListener('input', () => { pi.style.height='auto'; pi.style.height=Math.min(pi.scrollHeight,160)+'px'; });
  }
  // Esc 關閉側欄抽屜 / 選單
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (typeof cCloseFullpage === 'function') cCloseFullpage();
    cToggleSidebar(false);
    cToggleViewMenu(false);
    cToggleProjectMenu(false);
  });
  // 點擊選單以外的地方就把選單收起來
  document.addEventListener('click', e => {
    const t = e.target;
    if (!t) return;
    const inside = id => {
      const el = document.getElementById(id);
      return !!(el && el.contains && el.contains(t));
    };
    if (!inside('c-view-menu') && !inside('c-view-btn')) cToggleViewMenu(false);
    if (!inside('c-project-menu') && !inside('c-project-btn')) cToggleProjectMenu(false);
  });
});
</script>
"""


_MANUS_CONSOLE_PANEL: Final[str] = """
<section id="manus-console" class="frost m-line rounded-2xl p-4 sm:p-5 space-y-3" aria-labelledby="manus-console-title">
  <div class="flex items-start justify-between gap-3">
    <div>
      <div class="flex items-center gap-2">
        <span class="m-chip" style="border-color:var(--m-mist);color:var(--m-mist)">MANUS API</span>
        <h2 id="manus-console-title" class="text-sm font-semibold" style="color:var(--m-bone)">雲端指令台</h2>
      </div>
      <p class="text-[11px] mt-1" style="color:var(--m-faint)">Manus 負責規劃與追問；騰訊雲保留為 K.H.A.V.I.S. 執行節點。</p>
    </div>
    <span id="manus-console-state" class="text-[11px] font-mono" style="color:var(--m-faint)" aria-live="polite">未連線</span>
  </div>
  <div class="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2">
    <label class="sr-only" for="manus-console-token">K.H.A.V.I.S. Console Token</label>
    <input id="manus-console-token" type="password" autocomplete="off" placeholder="輸入 Console Token（只存本分頁 sessionStorage）" class="w-full rounded-lg px-3 py-2 text-xs font-mono m-line" />
    <button type="button" onclick="manusConnect()" class="m-btn-primary rounded-lg px-4 py-2 text-xs font-semibold">連線檢查</button>
  </div>
  <div class="grid grid-cols-1 sm:grid-cols-[1fr_150px_auto] gap-2">
    <label class="sr-only" for="manus-command-input">Manus 指令</label>
    <textarea id="manus-command-input" rows="3" maxlength="20000" placeholder="例如：檢查 WQ 自動執行狀態，若 Cookie 過期只回報，不要提交 Alpha。" class="w-full rounded-lg px-3 py-2 text-sm m-line resize-y"></textarea>
    <label class="sr-only" for="manus-agent-profile">Manus Agent profile</label>
    <select id="manus-agent-profile" class="rounded-lg px-3 py-2 text-xs font-mono m-line">
      <option value="standard">standard</option>
      <option value="lite">lite</option>
      <option value="max">max</option>
      <option value="max-high">max-high</option>
      <option value="max-ultra">max-ultra</option>
    </select>
    <button type="button" onclick="manusCreateTask()" class="m-btn-primary rounded-lg px-4 py-2 text-xs font-semibold">建立 Manus 任務</button>
  </div>
  <div class="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2">
    <label class="sr-only" for="manus-task-id">Manus task ID</label>
    <input id="manus-task-id" type="text" autocomplete="off" placeholder="Task ID（建立後自動填入）" class="w-full rounded-lg px-3 py-2 text-xs font-mono m-line" />
    <button type="button" onclick="manusRefreshTask()" class="m-btn-ghost rounded-lg px-4 py-2 text-xs font-semibold">刷新狀態</button>
  </div>
  <div id="manus-task-summary" class="rounded-lg p-3 text-xs font-mono" style="background:var(--m-faint-bg);color:var(--m-dim)" aria-live="polite">尚未建立任務</div>
  <div class="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2">
    <label class="sr-only" for="manus-followup-input">Manus 追問</label>
    <textarea id="manus-followup-input" rows="2" maxlength="20000" placeholder="對目前 Manus 任務追加指令…" class="w-full rounded-lg px-3 py-2 text-sm m-line resize-y"></textarea>
    <button type="button" onclick="manusSendFollowup()" class="m-btn-ghost rounded-lg px-4 py-2 text-xs font-semibold">追加訊息</button>
  </div>
  <div id="manus-message-feed" class="max-h-64 overflow-y-auto m-scroll space-y-2" aria-live="polite"></div>
</section>
"""


_MANUS_CONSOLE_JS: Final[str] = """
(() => {
  const tokenKey = 'khavis_console_token';
  const stateEl = () => document.getElementById('manus-console-state');
  const taskEl = () => document.getElementById('manus-task-id');
  const safe = value => String(value == null ? '' : value).slice(0, 4000);
  let tokenFallback = '';
  const readSessionToken = () => {
    try {
      const s = typeof sessionStorage !== 'undefined' ? sessionStorage.getItem(tokenKey) : '';
      const l = typeof localStorage !== 'undefined' ? localStorage.getItem(tokenKey) : '';
      return s || l || tokenFallback;
    } catch (_) { return tokenFallback; }
  };
  const saveSessionToken = value => {
    tokenFallback = value;
    try { if (typeof sessionStorage !== 'undefined') sessionStorage.setItem(tokenKey, value); } catch (_) {}
    try { if (typeof localStorage !== 'undefined') localStorage.setItem(tokenKey, value); } catch (_) {}
  };

  function setState(text, ok = null) {
    const el = stateEl();
    if (!el) return;
    el.textContent = text;
    el.style.color = ok === true ? 'var(--m-sage)' : (ok === false ? 'var(--m-rose-text)' : 'var(--m-faint)');
  }

  async function request(path, options = {}) {
    const token = readSessionToken();
    if (!token) throw new Error('請先輸入 Console Token');
    const headers = Object.assign({}, options.headers || {}, {
      'X-KHAVIS-Console-Token': token,
      'Content-Type': 'application/json'
    });
    const response = await fetch(path, Object.assign({}, options, {headers}));
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok || data.ok === false) {
      throw new Error((data.error && (data.error.detail || data.error.title)) || `HTTP ${response.status}`);
    }
    return data;
  }

  window.manusConnect = async function manusConnect(silent = false) {
    const input = document.getElementById('manus-console-token');
    const token = (input && input.value.trim()) || readSessionToken() || '';
    if (!token) { setState('需要 Console Token', false); return; }
    saveSessionToken(token);
    try {
      const data = await request('/api/manus/status');
      setState(data.configured ? '已連線 · API Key 就緒' : '已連線 · 尚未配置 API Key', data.configured);
      if (!silent) document.getElementById('manus-command-input')?.focus();
    } catch (err) { setState(safe(err.message), false); }
  };

  window.manusCreateTask = async function manusCreateTask() {
    const input = document.getElementById('manus-command-input');
    const message = input && input.value.trim();
    const profile = document.getElementById('manus-agent-profile')?.value || 'standard';
    if (!message) { setState('請輸入指令', false); return; }
    setState('建立任務中…');
    try {
      const data = await request('/api/manus/tasks', {
        method: 'POST', body: JSON.stringify({message, agent_profile: profile, interactive_mode: true})
      });
      const task = data.task || {};
      const id = task.task_id || task.id || '';
      if (!id) throw new Error('Manus 未回傳 task ID');
      taskEl().value = id;
      input.value = '';
      setState(`任務已建立 · ${id}`, true);
      await manusRefreshTask(true);
    } catch (err) { setState(safe(err.message), false); }
  };

  window.manusRefreshTask = async function manusRefreshTask(silent = false) {
    const id = taskEl()?.value.trim();
    if (!id) { if (!silent) setState('請先輸入或建立 Task ID', false); return; }
    if (!silent) setState('同步任務狀態…');
    try {
      const [detail, messages] = await Promise.all([
        request(`/api/manus/tasks/${encodeURIComponent(id)}`),
        request(`/api/manus/tasks/${encodeURIComponent(id)}/messages?limit=50`)
      ]);
      const task = detail.task || {};
      const summary = document.getElementById('manus-task-summary');
      if (summary) summary.textContent = `status=${safe(task.status || 'unknown')}  title=${safe(task.title || '')}  updated=${safe(task.updated_at || '')}`;
      const feed = document.getElementById('manus-message-feed');
      if (feed) {
        feed.replaceChildren();
        (messages.messages || []).slice().reverse().forEach(event => {
          const row = document.createElement('div');
          row.className = 'rounded-lg p-2 text-xs font-mono';
          row.style.background = 'var(--m-faint-bg)';
          const type = safe(event.type || 'event');
          const payload = event.assistant_message || event.user_message || event.status_update || event.error_message || {};
          const content = payload.content || payload.brief || payload.description || payload.error_type || '';
          row.textContent = `${type}: ${safe(content)}`;
          feed.appendChild(row);
        });
      }
      setState(`任務 ${safe(task.status || 'unknown')}`, true);
    } catch (err) { setState(safe(err.message), false); }
  };

  window.manusSendFollowup = async function manusSendFollowup() {
    const id = taskEl()?.value.trim();
    const input = document.getElementById('manus-followup-input');
    const message = input && input.value.trim();
    if (!id || !message) { setState('需要 Task ID 與追問內容', false); return; }
    setState('送出追問中…');
    try {
      await request(`/api/manus/tasks/${encodeURIComponent(id)}/messages`, {
        method: 'POST', body: JSON.stringify({message})
      });
      input.value = '';
      setState('追問已送出', true);
      await manusRefreshTask(true);
    } catch (err) { setState(safe(err.message), false); }
  };

  document.addEventListener('DOMContentLoaded', () => {
    if (readSessionToken()) window.manusConnect(true);
  });
})();
"""


# ── 獨立專頁的補充標記（首頁只留按鈕，管理操作都在專頁上完成） ─────────────
# Agent 狀態專頁（上半）：即時狀態 + 模型新增/刪除/改名/切換/還原
_PAGE_AGENTS_TOP: Final[str] = """
        <div class="glass-panel p-4 rounded-2xl border border-white/10 space-y-3">
          <div class="flex items-center gap-2 flex-wrap">
            <h3 class="font-bold text-sm text-white">即時 Agent 狀態</h3>
            <span class="text-[10px] font-mono px-1.5 py-0.5 rounded" style="background:var(--m-sage-soft);color:var(--m-sage)"><span id="c-fleet-count-page">7</span>/7 在線</span>
            <span class="text-[11px] text-slate-400">點選卡片可啟用／停用該 Agent 參與派工</span>
          </div>
          <div id="c-agent-status-list" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-1.5"></div>
        </div>

        <div id="c-model-crud-box" class="glass-panel p-4 rounded-2xl border border-white/10 space-y-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h3 class="font-bold text-sm text-white">模型管理：新增 · 刪除 · 改名 · 切換</h3>
            <button type="button" onclick="cResetAllModelsDefault()" class="px-2.5 py-1 rounded-lg bg-white/5 hover:bg-white/10 text-xs text-slate-300 transition">↺ 還原所有 Agent 預設模型</button>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <select id="c-model-crud-agent" class="bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs font-mono text-white focus:outline-none focus:border-emerald-400" aria-label="新增模型的目標 Agent"><option value="all">全部 Agent</option></select>
            <input id="c-model-crud-input" type="text" onkeydown="if(event.key==='Enter'){event.preventDefault();cAddModelOption();}" placeholder="輸入新模型名稱（如 deepseek-chat）" class="flex-1 min-w-[200px] bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs font-mono text-white placeholder-slate-500 focus:outline-none focus:border-emerald-400" />
            <button type="button" onclick="cAddModelOption()" class="px-3 py-1.5 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/35 text-emerald-300 border border-emerald-500/40 text-xs font-bold shrink-0">＋ 新增模型</button>
          </div>
          <p class="text-[11px] text-slate-400">每張 Agent 卡片上也能直接新增、改名（✏️）、刪除（×）或點選切換模型，變更會立即寫入 .env。</p>
        </div>
"""

# Agent 狀態專頁（下半）：服務商新增/編輯/刪除 + 拓撲與自訂 Agent
_PAGE_AGENTS_BOTTOM: Final[str] = """
        <div id="c-provider-form-box" class="glass-panel p-4 sm:p-5 rounded-2xl border border-white/10 space-y-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h3 class="font-bold text-sm text-white">服務商管理：新增 / 編輯</h3>
            <button type="button" onclick="cRestoreBuiltinProviders()" class="px-2.5 py-1 rounded-lg bg-white/5 hover:bg-white/10 text-xs text-slate-300 transition">↺ 還原預設服務商</button>
          </div>
          <div>
            <div class="text-[11px] text-slate-400 mb-1.5">① 點選常用範本自動帶入欄位，或選「自訂服務商」手動輸入</div>
            <div id="c-prov-presets" class="flex flex-wrap gap-2"></div>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
            <label class="text-[11px] text-slate-400 space-y-1">服務商名稱
              <input id="c-prov-label" type="text" placeholder="例如 DeepSeek 官方 API" class="w-full bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-sky-400" /></label>
            <label class="text-[11px] text-slate-400 space-y-1">金鑰環境變數名稱
              <input id="c-prov-env" type="text" placeholder="例如 DEEPSEEK_API_KEY" class="w-full bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs font-mono text-white placeholder-slate-500 focus:outline-none focus:border-sky-400" /></label>
            <label class="text-[11px] text-slate-400 space-y-1 md:col-span-2">② API Key（留白則保留既有金鑰）
              <span class="wizard-input-wrap flex items-center gap-1.5">
                <input id="c-prov-key" type="password" autocomplete="off" placeholder="貼上 API Key…" class="flex-1 bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs font-mono text-emerald-300 placeholder-slate-500 focus:outline-none focus:border-sky-400" />
                <button type="button" onclick="toggleWizardInputVis(this)" class="px-2 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white text-xs" title="顯示/隱藏金鑰"><i class="fas fa-eye"></i></button>
              </span></label>
            <label class="text-[11px] text-slate-400 space-y-1">Base URL
              <input id="c-prov-url" type="text" placeholder="https://api.example.com/v1" class="w-full bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs font-mono text-white placeholder-slate-500 focus:outline-none focus:border-sky-400" /></label>
            <label class="text-[11px] text-slate-400 space-y-1">Base URL 環境變數名稱
              <input id="c-prov-url-env" type="text" placeholder="例如 DEEPSEEK_BASE_URL" class="w-full bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs font-mono text-white placeholder-slate-500 focus:outline-none focus:border-sky-400" /></label>
            <label class="text-[11px] text-slate-400 space-y-1">適用 Agent 說明
              <input id="c-prov-agents" type="text" placeholder="例如 OpenCode / Codex" class="w-full bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-sky-400" /></label>
            <label class="text-[11px] text-slate-400 space-y-1">③ 綁定模型（從供應商抓取清單，勾選要呈現的模型）
              <div class="space-y-2">
                <div class="flex items-center gap-1.5">
                  <button type="button" onclick="cFetchProviderModels()" class="px-3 py-1.5 rounded-lg bg-sky-500/20 hover:bg-sky-500/35 text-sky-300 border border-sky-500/40 text-xs font-bold shrink-0 transition">🔄 從供應商抓取模型清單</button>
                  <span id="c-prov-models-status" class="text-[10px] font-mono text-slate-500 truncate">尚未抓取 · 填好 API Key 與 Base URL 後即可抓取</span>
                </div>
                <div id="c-prov-models-box" class="hidden rounded-lg bg-[#050811] border border-white/15 p-2 space-y-2">
                  <input id="c-prov-models-filter" type="search" oninput="cRenderProvModelList()" placeholder="搜尋過濾模型…" autocomplete="off" class="w-full bg-transparent border border-white/10 rounded px-2 py-1 text-[11px] font-mono text-white placeholder-slate-500 focus:outline-none focus:border-sky-400" />
                  <div id="c-prov-models-list" class="max-h-44 overflow-y-auto space-y-0.5 pr-1"></div>
                  <div class="flex items-center gap-1.5">
                    <input id="c-prov-models-manual" type="text" onkeydown="if(event.key==='Enter'){event.preventDefault();cAddManualProvModel();}" placeholder="供應商無 /models 端點時，手動輸入模型 ID…" autocomplete="off" class="flex-1 bg-transparent border border-white/10 rounded px-2 py-1 text-[11px] font-mono text-white placeholder-slate-500 focus:outline-none focus:border-emerald-400" />
                    <button type="button" onclick="cAddManualProvModel()" class="px-2 py-1 rounded bg-emerald-500/20 hover:bg-emerald-500/35 text-emerald-300 border border-emerald-500/40 text-[11px] font-bold shrink-0 transition">＋加入</button>
                  </div>
                  <div class="text-[10px] font-mono text-slate-400">已勾選 <span id="c-prov-models-count" class="text-emerald-300">0</span> 個模型（只有勾選的會寫入服務商並同步到模型選單）</div>
                </div>
              </div></label>
            <label class="text-[11px] text-slate-400 space-y-1">模型加入哪個 Agent
              <select id="c-prov-target-agent" class="w-full bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs font-mono text-white focus:outline-none focus:border-sky-400"><option value="all">全部 Agent</option></select></label>
          </div>
          <div class="flex justify-end">
            <button type="button" onclick="cUpsertProvider()" class="px-4 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs transition">💾 儲存服務商</button>
          </div>
        </div>

        <div class="space-y-2">
          <h3 class="font-bold text-sm text-white">已設定的服務商</h3>
          <div id="c-providers-crud-grid" class="grid grid-cols-1 lg:grid-cols-2 gap-3"></div>
        </div>

        <details id="c-topology-collapse" class="m-collapse frost m-line rounded-2xl overflow-hidden">
          <summary class="px-4 py-3 flex items-center gap-2 cursor-pointer">
            <svg class="m-caret" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>
            <span class="text-sm font-semibold" style="color:var(--m-dim)">拓撲畫布 · 自訂 Agent 掛載</span>
          </summary>
          <div class="p-3 pt-0">{topology}</div>
        </details>
"""

# 資料庫專頁：搜尋與類型篩選列
_PAGE_DB_TOOLBAR: Final[str] = """
        <div class="flex flex-wrap items-center gap-2">
          <input id="c-artifact-search" type="search" oninput="cFilterArtifactsPage()" placeholder="搜尋檔名、路徑或類型…" autocomplete="off" class="flex-1 min-w-[220px] bg-[#050811] border border-white/15 rounded-lg px-3 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-sky-400" />
          <select id="c-artifact-badge" onchange="cFilterArtifactsPage(undefined, this.value)" class="bg-[#050811] border border-white/15 rounded-lg px-2.5 py-1.5 text-xs font-mono text-white focus:outline-none focus:border-sky-400" aria-label="產物類型"><option value="all">全部類型</option></select>
          <span class="text-[11px] font-mono text-slate-400">共 <span id="c-db-page-count">0</span> 筆</span>
        </div>
        <div id="c-db-thread-groups" class="space-y-2.5"></div>
"""


def _compose_views(views: str, topology: str) -> str:
    """在 legacy 的專頁區塊中補上服務商/模型管理表單與資料庫搜尋列。"""
    # 資料庫：搜尋列插在產物清單之前
    views = views.replace(
        '<div id="artifacts-grid"',
        _PAGE_DB_TOOLBAR + '        <div id="artifacts-grid"',
        1,
    )
    # Agent 專頁：狀態與模型管理插在卡片牆之前
    views = views.replace(
        '<div id="agents-fleet-grid"',
        _PAGE_AGENTS_TOP + '        <div id="agents-fleet-grid"',
        1,
    )
    # 「新增自訂 Agent」按鈕：不再跳回首頁，直接在專頁展開自訂面板
    views = views.replace(
        "switchTab('command'); document.getElementById('custom-agent-panel').classList.remove('hidden');",
        "cAddAgent();",
    )
    # Agent 專頁（下半）：緊接在 view-agents 區塊結尾 </section> 之前
    marker = '<section id="view-wizard"'
    idx = views.find(marker)
    if idx != -1:
        close_idx = views.rfind("</section>", 0, idx)
        if close_idx != -1:
            views = (
                views[:close_idx]
                + _PAGE_AGENTS_BOTTOM.replace("{topology}", topology)
                + views[close_idx:]
            )
    return views


def render_workbench_html_c() -> str:
    """Render the 3-column 7-panel Workbench layout (左3: 歷史談話/資料庫/額度設定 | 中2: 對話串/輸入框 | 右2: Agent狀態/Using檔案)."""
    workspace = _rebalance(_slice("workspace"))
    topology = _rebalance(_slice("topology"))
    composer_block = _rebalance(_slice("composer"))
    result = _slice("result")
    views = _compose_views(_slice("views"), topology)
    drawers = _slice("drawers")

    body = f"""<body class="font-sans antialiased h-screen flex overflow-hidden m-scroll">
{_SIDEBAR}

<div class="flex-1 flex flex-col min-w-0 h-full">
{_TOPBAR}

<!-- ═══════════════════════════════════════════════════════════════
     中上區塊 4：對話串 (Main Conversation Thread)
     ═══════════════════════════════════════════════════════════════ -->
<main id="c-main" class="flex-1 overflow-y-auto m-scroll">
  <div id="c-main-container" class="max-w-4xl mx-auto px-4 sm:px-6 py-4">

    <section id="view-command" class="tab-content block space-y-3">
      <!-- 中央 = 對話串（你的指令 → Agent 協同結果 → 追問） -->
      <div id="c-conversation" class="space-y-3">
        <div id="c-conv-header" class="c-conv-header"></div>
        <div id="c-conv-turns" class="space-y-3"></div>
        <!-- legacy 的 live-results-container 會被搬進來：
             hero card、「接下來怎麼辦」按鈕與追問框都在這一格，且永不重建。 -->
        <div id="c-live-slot" class="c-live-slot"></div>

        <details class="m-collapse frost m-line rounded-2xl overflow-hidden">
          <summary class="px-4 py-2.5 flex items-center gap-2 hover:bg-white/[.02] transition cursor-pointer">
            <svg class="m-caret" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>
            <span class="m-chip" style="border-color:var(--m-mist);color:var(--m-mist)">MANUS API</span>
            <span class="text-xs font-semibold" style="color:var(--m-dim)">雲端指令台 (Manus 規劃與遠端追問)</span>
          </summary>
          <div class="p-3 pt-0">{_MANUS_CONSOLE_PANEL}</div>
        </details>
      </div>

      <!-- 總覽模式：所有任務的卡片流（次要視圖） -->
      <div id="c-task-stream" class="space-y-3 hidden"></div>
    </section>

{views}
  </div>
</main>

{_COMPOSER_CHROME_TOP}
{composer_block}
{_COMPOSER_CHROME_BOTTOM}
</div>

<!-- ═══════════════════════════════════════════════════════════════
     右側常駐第三欄（上：Agent 狀態 / 下：Using 檔案）
     ═══════════════════════════════════════════════════════════════ -->
<aside id="c-drawer" class="c-drawer bg-ink-800/90 backdrop-blur flex flex-col h-full">

  <!-- 右上：執行流程明細。Agent 狀態／模型／服務商已移到獨立專頁，首頁只留入口按鈕 -->
  <div class="shrink-0 flex flex-col border-b m-line">
    <div class="c-zone-header">
      <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" style="color:var(--m-sage)" viewBox="0 0 24 24"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="M12 7v4m0 0-5 5m5-5 5 5" stroke-linecap="round"/></svg>
      <span class="c-zone-title">執行流程</span>
      <div class="flex-1"></div>
      <button type="button" onclick="cOpenPage('agents')" class="px-2 py-0.5 rounded text-[10px] m-btn-ghost shrink-0 whitespace-nowrap" title="進入 Agent 狀態、模型與服務商管理專頁">🤖 Agent 狀態 ↗</button>
      <button type="button" onclick="cToggleDrawer(false)" class="w-6 h-6 shrink-0 rounded-md m-btn-ghost grid place-items-center xl:hidden" aria-label="關閉右側欄">
        <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke-linecap="round"/></svg>
      </button>
    </div>

    <div id="c-drawer-body" class="max-h-[42vh] overflow-y-auto m-scroll p-2.5">
      <details class="m-collapse frost m-line rounded-xl overflow-hidden" id="c-exec-detail-collapse">
        <summary class="px-3 py-2 flex items-center gap-2 hover:bg-white/[.02] transition cursor-pointer">
          <svg class="m-caret" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>
          <span class="text-[11px] font-semibold" style="color:var(--m-dim)">7 角色流水線與延遲明細</span>
        </summary>
        <div class="p-2.5 pt-0 space-y-2">{result}</div>
      </details>
    </div>
  </div>

  <!-- 右下區塊 7：Using 檔案 (Active Workspace & Files / Diff) -->
  <div class="flex-1 min-h-0 flex flex-col">
    <div class="c-zone-header">
      <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" style="color:var(--m-mist)" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>
      <span class="c-zone-title">Using 檔案</span>
      <span class="text-[10px] font-mono px-1.5 py-0.5 rounded" style="background:var(--m-mist-soft);color:var(--m-mist)" id="c-ws-file-count">0</span>
      <div class="flex-1"></div>

      <!-- 工作區專案快速切換 -->
      <div class="relative">
        <button type="button" onclick="cToggleProjectMenu()" id="c-project-btn"
                class="flex items-center gap-1 px-2 py-0.5 rounded-md m-btn-ghost text-[10px] font-mono transition"
                aria-haspopup="menu" aria-expanded="false" title="切換工作區專案資料夾">
          <span class="truncate max-w-[96px]" id="c-active-project">shared-app</span>
          <span style="color:var(--m-faint)" id="c-project-count">--</span>
          <svg class="shrink-0" width="9" height="9" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <div id="c-project-menu" class="c-menu hidden" style="right:0;left:auto" role="menu" aria-label="切換專案"></div>
      </div>

      <button type="button" onclick="loadWorkspaceStatus()" class="px-2 py-0.5 rounded text-[10px] m-btn-ghost shrink-0" title="重新整理工作區檔案與 Diff">刷新</button>
    </div>

    <div id="c-using-files-host" class="flex-1 overflow-y-auto m-scroll p-2 space-y-2">
      <details class="m-collapse" id="c-workspace-collapse" open>
        <summary class="hidden">工作區</summary>
        <div>{workspace}</div>
      </details>
    </div>
  </div>
</aside>

<div id="c-drawer-scrim" onclick="cToggleDrawer(false)"></div>
<div id="c-scrim" onclick="cToggleSidebar(false)"></div>

<!-- ═══════════════════════════════════════════════════════════════
     全頁視圖（如外掛市集頁）：Agent / 資料庫 / 額度 / 執行明細
     點擊側欄功能後整頁呈現；「返回對話」回到主對話串。
     ═══════════════════════════════════════════════════════════════ -->
<div id="c-fullpage" class="hidden fixed inset-0 flex flex-col" style="background:var(--m-ink-900)">
  <header class="v-fp-head shrink-0 h-12 flex items-center gap-2 px-3 sm:px-5">
    <button type="button" onclick="cCloseFullpage()" class="w-8 h-8 rounded-lg m-btn-ghost grid place-items-center shrink-0" title="返回對話 (Esc)" aria-label="返回對話">
      <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
    <span id="c-fullpage-ic" class="v-nav-ic v-ic-agents" style="width:1.6rem;height:1.6rem"></span>
    <div class="min-w-0">
      <div id="c-fullpage-title" class="text-sm font-bold truncate" style="color:var(--m-bone)">頁面</div>
    </div>
    <div class="flex-1"></div>
    <div id="c-fullpage-actions" class="flex items-center gap-1.5 min-w-0"></div>
    <button type="button" onclick="cCloseFullpage()" class="w-8 h-8 rounded-lg m-btn-ghost grid place-items-center shrink-0" title="關閉" aria-label="關閉全頁視圖">
      <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke-linecap="round"/></svg>
    </button>
  </header>
  <div id="c-fullpage-content" class="flex-1 min-h-0 overflow-y-auto m-scroll"></div>
</div>

{drawers}
<div id="c-toast-host" aria-live="polite" aria-atomic="false"></div>
"""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant" class="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>K.H.A.V.I.S. · 任務工作台 (Task Stream)</title>

  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
  <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">

  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {{
      darkMode: 'class',
      theme: {{ extend: {{
        colors: {{
          ink: {{ 900:'#16181D', 800:'#1C1E25', 700:'#23262F', 600:'#2B2F3A' }}
        }},
        fontFamily: {{
          sans: ['Inter', 'Noto Sans TC', 'sans-serif'],
          mono: ['JetBrains Mono', 'monospace']
        }}
      }} }}
    }};
  </script>

  <style>
{_legacy_style()}
{_MORANDI_CSS}
{_VIVID_CSS}
  </style>
</head>
{body}

  <script>
{_WORKBENCH_CLIENT_JS}
  </script>
{_LEGACY_CAPTURE_JS}
{_C_EXTRA_JS}
  <script>
{_MANUS_CONSOLE_JS}
  </script>
</body>
</html>
"""
