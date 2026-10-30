/* Functional test for the workbench dispatch → next-step → follow-up chain.
 *
 * The static smoke test can't tell whether the "接下來怎麼辦" buttons and the
 * follow-up field actually *work* — only that the scripts parse. This harness
 * builds a DOM shim that parses innerHTML into real element trees, mocks the
 * dispatch API, then drives the exact user flow:
 *
 *   1. type a prompt  → submitDispatch()
 *   2. wait for result → assert next-step buttons + follow-up field rendered
 *   3. click a next-step button → assert a NEW dispatch fires with that prompt
 *   4. type in the follow-up field → submitFollowup() → assert a NEW dispatch
 *
 * Usage: node functional_test.js <page.html>
 */
const fs = require('fs');
const vm = require('vm');
const realFetch = globalThis.fetch;

/* ─────────── tiny HTML → element tree ─────────── */
function parseHTML(html, root) {
  const tagRe = /<(\/?)([a-zA-Z][\w-]*)((?:\s+[^<>]*?)?)(\/?)>/g;
  const stack = [root];
  let m;
  while ((m = tagRe.exec(html)) !== null) {
    const [, closing, rawTag, rawAttrs, selfClose] = m;
    const tag = rawTag.toLowerCase();
    if (closing) {
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName === tag.toUpperCase()) { stack.length = i; break; }
      }
      continue;
    }
    const el = mkEl(tag);
    // attributes: id, class, onclick, value, checked, placeholder
    const attrRe = /([\w-]+)\s*=\s*"([^"]*)"/g;
    let a;
    while ((a = attrRe.exec(rawAttrs)) !== null) {
      const [, k, v] = a;
      if (k === 'id') { el.id = v; reg(v, el); }
      else if (k === 'class') el.className = v;
      else if (k === 'onclick') el._onclick = v;
      else if (k === 'value') el.value = v;
      else if (k === 'checked') el.checked = true;
      else el.attrs[k] = v;
    }
    if (rawAttrs.includes('hidden') && !('checked' in el)) { /* keep */ }
    stack[stack.length - 1].appendChild(el);
    if (!selfClose && !VOID.has(tag)) stack.push(el);
  }
}
const VOID = new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr','path','circle','line','rect','polyline','polygon','ellipse','use','stop']);

function mkClassList() {
  const set = new Set();
  return {
    add: (...c) => c.forEach(x => set.add(x)),
    remove: (...c) => c.forEach(x => set.delete(x)),
    contains: c => set.has(c),
    toggle: (c, f) => { const on = f === undefined ? !set.has(c) : !!f; if (on) set.add(c); else set.delete(c); return on; },
    _set: set,
  };
}

const mkEl = t => new El(t);

const byId = new Map();
function reg(id, el) { el.id = id; byId.set(id, el); return el; }

let agentCheckboxes = [];

class El {
  constructor(tag = 'div') {
    this.tagName = String(tag).toUpperCase();
    this.id = ''; this.classList = mkClassList(); this.attrs = {};
    this.style = new Proxy({}, { get: (t,k)=>t[k] ?? '', set: (t,k,v)=>(t[k]=v,true) });
    this.dataset = {}; this.children = []; this.parentElement = null;
    this._text = ''; this._html = ''; this.value = ''; this.checked = false;
    this.disabled = false; this.scrollHeight = 100; this.options = []; this._onclick = '';
  }
  get textContent() {
    if (this.children.length) return this.children.map(c => c.textContent).join('');
    return this._text;
  }
  set textContent(v) { this._text = String(v); this.children = []; this._html = ''; }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    this._html = String(v);
    this.children = [];
    if (v && v.includes('<')) parseHTML(v, this);
  }
  get className() { return [...this.classList._set].join(' '); }
  set className(v) { this.classList._set = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get firstElementChild() { return this.children[0] || null; }
  appendChild(c) { c.parentElement = this; this.children.push(c); return c; }
  insertBefore(n, ref) { const i = this.children.indexOf(ref); i < 0 ? this.children.push(n) : this.children.splice(i, 0, n); n.parentElement = this; return n; }
  removeChild(c) { this.children = this.children.filter(x => x !== c); }
  insertAdjacentHTML(pos, h) { this._html += h; parseHTML(h, this); }
  remove() { if (this.parentElement) this.parentElement.removeChild(this); }
  setAttribute() {} getAttribute() { return null; }
  addEventListener() {} removeEventListener() {}
  focus() {} blur() {} scrollIntoView() {}
  closest() { return null; }
  getBoundingClientRect() { return { top:0,left:0,width:0,height:0,bottom:0,right:0 }; }
  descendants() { let out = []; for (const c of this.children) { out.push(c); out = out.concat(c.descendants()); } return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const all = this.descendants();
    const selOne = s => {
      if (s.startsWith('#')) return e => e.id === s.slice(1);
      if (s.startsWith('.')) return e => e.classList.contains(s.slice(1));
      const m = /^([a-zA-Z][\w-]*)?(?:\[([\w-]+)="([^"]*)"\])?$/.exec(s);
      if (!m) return () => false;
      const [, tag, attr, val] = m;
      return e => {
        if (tag && e.tagName !== tag.toUpperCase()) return false;
        if (attr) return (e.attrs[attr] === val) || (attr === 'name' && e.name === val) || (attr === 'name' && e.getName === val);
        return true;
      };
    };
    if (sel.includes(',')) return all.filter(e => sel.split(',').some(s => selOne(s.trim())(e)));
    return all.filter(selOne(sel));
  }
}

/* ─────────── build the document ─────────── */
const html = fs.readFileSync(process.argv[2] || '/tmp/live_wb.html', 'utf8');
for (const id of new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]))) reg(id, new El('div'));

agentCheckboxes = ['claude','codex','antigravity','minimax','opencode','khavis'].map(v => {
  const cb = new El('input'); cb.name = 'agent_target'; cb.value = v;
  cb.checked = (v === 'antigravity' || v === 'khavis'); cb.setName = v;
  return cb;
});

const doc = {
  documentElement: new El('html'), body: new El('body'), head: new El('head'), title: '',
  getElementById: id => byId.get(id) || null,
  createElement: t => new El(t),
  createTextNode: t => ({ textContent: t }),
  addEventListener: (ev, cb) => { if (ev === 'DOMContentLoaded') doc._ready = cb; },
  removeEventListener: () => {},
  querySelector: sel => doc.querySelectorAll(sel)[0] || null,
  querySelectorAll: sel => {
    if (sel.includes('agent_target')) return agentCheckboxes;
    return [];
  },
  execCommand: () => true,
};

/* ─────────── dispatch mock ─────────── */
const dispatchLog = [];
function mockDispatchResponse(n) {
  return {
    ok: true,
    collab_mode: 'auto_loop',
    consensus: `第 ${n} 輪成果：已完成 health_check.py 並通過測試。`,
    consensus_source: 'Multi-Agent Synthesis',
    critique_summary: '交叉檢查通過',
    collaboration_steps: ['planner','coder_a','coder_b','debater','critic','verifier','learner'],
    next_actions: [
      { label: '🧪 自動執行測試與驗收剛才變更的檔案', prompt: `PROMPT_NEXT_${n}`, mode: 'auto_loop' },
      { label: '⚔️ 讓多 Agent 針對上述成果進行嚴格挑刺與補強', prompt: `PROMPT_CRITIQUE_${n}`, mode: 'critique' },
      { label: '📦 將上述方案完整落地為專案可執行腳本並驗證', prompt: `PROMPT_RELAY_${n}`, mode: 'relay' },
    ],
    isolation_mode: 'shared', project: 'shared-app',
    shared_dir: '/opt/khavis/workspace/shared-app',
    shared_changed_files: [{ path: 'health_check.py' }],
    shared_diff: '',
    results: [
      { ok: true, backend: 'claude', model: 'MiniMax-M3.1-Flash', cwd: '/w', latency_ms: 9000,
        text: 'wrote health_check.py (+126)', changed_files: [{ path: 'health_check.py' }] },
      { ok: true, backend: 'codex', model: 'glm-5.3-flashx', cwd: '/w', latency_ms: 7000, text: 'review ok' },
    ],
  };
}

let dispatchCount = 0;
const ctx = {
  document: doc, window: {}, console,
  performance: { now: () => Date.now() },
  navigator: { clipboard: { writeText: () => {} } },
  location: { href: 'http://localhost/workbench' },
  localStorage: { getItem: () => null, setItem: () => {} },
  setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout,
  setInterval: () => 0, clearInterval: () => {},
  alert: m => { ctx._alerts = (ctx._alerts || []); ctx._alerts.push(m); },
  _alerts: [],
  fetch: async (url, opts) => {
    const u = String(url);
    if (u.includes('/api/workbench/dispatch')) {
      let body = {};
      try { body = JSON.parse((opts && opts.body) || '{}'); } catch (_) {}
      dispatchLog.push(body);
      dispatchCount += 1;
      return { ok: true, status: 200, json: async () => mockDispatchResponse(dispatchCount) };
    }
    const empty = { ok: true, history: [], results: [], pools: [], quotas: [], agents: [], artifacts: [], projects: [], files: [], models: {}, defaults: {}, options: {}, providers: [] };
    return { ok: true, status: 200, json: async () => empty, text: async () => '' };
  },
  Event: class { constructor(t) { this.type = t; } },
  MutationObserver: class { observe() {} disconnect() {} },
  requestAnimationFrame: fn => setTimeout(fn, 0),
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  tailwind: { config: {} },
  Math, Date, JSON, String, Number, Array, Object, Boolean, RegExp, Error, Promise, Set, Map,
  isNaN, parseInt, parseFloat, encodeURIComponent, decodeURIComponent,
  btoa: s => Buffer.from(s).toString('base64'), atob: s => Buffer.from(s, 'base64').toString('binary'),
};
ctx.window = ctx; ctx.globalThis = ctx; ctx.self = ctx;
vm.createContext(ctx);

const errors = [];
for (const [i, m] of [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].entries()) {
  try { vm.runInContext(m[1], ctx, { filename: `block${i}.js` }); }
  catch (e) { errors.push(`block ${i}: ${e.message}`); }
}
if (doc._ready) { try { doc._ready(); } catch (e) { errors.push('DOMContentLoaded: ' + e.message); } }

/* ─────────── helpers ─────────── */
const $ = id => doc.getElementById(id);
const all = sel => {
  if (sel.includes('agent_target')) return agentCheckboxes;
  return doc.body.querySelectorAll(sel);
};
const fails = [];
function check(name, cond, extra) {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !extra ? '' : '  → ' + extra}`);
  if (!cond) fails.push(name);
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  await sleep(300);

  console.log('\n── 步驟 1：送出第一個任務 ──');
  const pi = $('prompt-input');
  check('prompt-input 存在', !!pi);
  pi.value = '幫我寫個健康檢查腳本';
  await ctx.submitDispatch();
  await sleep(400);

  const lrc = $('live-results-container');
  check('live-results-container 有內容', lrc && lrc.children.length > 0,
        `children=${lrc ? lrc.children.length : 'null'}`);

  const nextBtns = lrc.querySelectorAll('button').filter(b => (b._onclick || '').includes('runNextStepAction'));
  check('「接下來怎麼辦」按鈕已渲染', nextBtns.length > 0, `count=${nextBtns.length}`);
  if (nextBtns.length) console.log('     按鈕文字:', nextBtns.slice(0,3).map(b=>b.textContent.trim()).join(' | '));

  const follow = $('followup-inline-input');
  check('追問輸入框 (#followup-inline-input) 存在', !!follow);
  const followBtn = lrc.querySelectorAll('button').find(b => (b._onclick || '').includes('submitFollowup'));
  check('「繼續下一步」按鈕存在', !!followBtn);

  const dupIds = (() => {
    const seen = {}, dup = new Set();
    for (const e of doc.body.descendants()) if (e.id) { if (seen[e.id]) dup.add(e.id); seen[e.id] = 1; }
    return dup;
  })();
  check('無重複 DOM id', dupIds.size === 0, [...dupIds].join(','));

  console.log('\n── 步驟 2：點擊「接下來怎麼辦」按鈕 ──');
  const before = dispatchLog.length;
  // `let` 綁定不是 vm global 物件的屬性，必須用 runInContext 在同一個作用域讀
  const naLen = vm.runInContext('typeof latestNextActions !== "undefined" ? latestNextActions.length : -1', ctx);
  const naLabels = vm.runInContext('typeof latestNextActions !== "undefined" ? latestNextActions.map(a=>a.label) : []', ctx);
  check('latestNextActions 已填充', naLen > 0, `len=${naLen} ${JSON.stringify(naLabels)}`);
  if (nextBtns.length) {
    // 模擬 onclick
    await vm.runInContext(`runNextStepAction(0)`, ctx);
    await sleep(400);
    check('觸發了新的 dispatch', dispatchLog.length > before, `before=${before} after=${dispatchLog.length}`);
    const last = dispatchLog[dispatchLog.length - 1];
    check('新 dispatch 帶入按鈕的 prompt', last && last.prompt === 'PROMPT_NEXT_1', `got=${last && last.prompt}`);
    check('新 dispatch 帶入按鈕的 mode', last && last.collab_mode === 'auto_loop', `got=${last && last.collab_mode}`);
    check('帶入上一輪脈絡 (previous_context)', !!(last && last.previous_context));
  }

  console.log('\n── 步驟 3：使用追問輸入框 ──');
  const f2 = $('followup-inline-input');
  const before2 = dispatchLog.length;
  if (f2) {
    f2.value = '幫我實際執行上面的修改';
    await vm.runInContext('submitFollowup()', ctx);
    await sleep(400);
    check('追問觸發新的 dispatch', dispatchLog.length > before2, `before=${before2} after=${dispatchLog.length}`);
    const last2 = dispatchLog[dispatchLog.length - 1];
    check('追問內容成為新 prompt', last2 && last2.prompt === '幫我實際執行上面的修改', `got=${last2 && last2.prompt}`);
  } else {
    check('追問輸入框存在（第二步之後）', false, '找不到 #followup-inline-input');
  }

  console.log('\n── 步驟 4：再點一次「接下來怎麼辦」（第二輪） ──');
  const lrc2 = $('live-results-container');
  const nextBtns2 = lrc2.querySelectorAll('button').filter(b => (b._onclick || '').includes('runNextStepAction'));
  check('第二輪的按鈕仍存在', nextBtns2.length > 0, `count=${nextBtns2.length}`);
  const before3 = dispatchLog.length;
  if (nextBtns2.length) {
    await vm.runInContext('runNextStepAction(0)', ctx);
    await sleep(400);
    check('第二輪點擊仍能觸發 dispatch', dispatchLog.length > before3, `before=${before3} after=${dispatchLog.length}`);
    const last3 = dispatchLog[dispatchLog.length - 1];
    // 按鈕帶的是「上一輪回應」產生的 next_actions，所以期望值 = 上一輪序號
    const expectN = dispatchLog.length - 1;
    check('第二輪 prompt 正確', last3 && last3.prompt === `PROMPT_NEXT_${expectN}`,
          `got=${last3 && last3.prompt} expect=PROMPT_NEXT_${expectN}`);
  }

  console.log('\n── 步驟 5：防重入旗標必須釋放 ──');
  const stillRunning = vm.runInContext('cRunningTask', ctx);
  check('cRunningTask 已歸 null（否則後續點擊全部無反應）', stillRunning === null, `cRunningTask=${stillRunning}`);

  console.log('\n── 環境錯誤 ──');
  check('無 runtime error', errors.length === 0, errors.slice(0,3).join(' | '));
  check('無 alert 阻擋', ctx._alerts.length === 0, ctx._alerts.join(' | '));

  console.log(`\n總 dispatch 次數: ${dispatchLog.length}`);
  console.log('RESULT:', fails.length === 0 ? 'PASS ✅' : `FAIL ❌ (${fails.length}) → ${fails.join('; ')}`);
  process.exit(fails.length ? 1 : 0);
})();