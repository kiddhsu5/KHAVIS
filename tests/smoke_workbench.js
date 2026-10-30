/* Minimal DOM shim — runs the workbench page scripts headlessly to catch
   runtime errors (undefined functions, bad property access, ...).
   Not a full browser: just enough surface for this page's JS. */
const fs = require('fs');
const vm = require('vm');
const realFetch = globalThis.fetch;

const errors = [];
process.on('uncaughtException', e => errors.push('uncaught: ' + e.message));

function mkClassList(el) {
  const set = new Set();
  return {
    add: (...c) => c.forEach(x => set.add(x)),
    remove: (...c) => c.forEach(x => set.delete(x)),
    contains: c => set.has(c),
    toggle: (c, force) => {
      const on = force === undefined ? !set.has(c) : !!force;
      if (on) set.add(c); else set.delete(c);
      return on;
    },
    _set: set,
  };
}

class El {
  constructor(tag = 'div', id = '') {
    this.tagName = String(tag).toUpperCase();
    this.id = id;
    this.classList = mkClassList(this);
    this.style = new Proxy({}, { get: (t, k) => t[k] ?? '', set: (t, k, v) => (t[k] = v, true) });
    this.dataset = {};
    this.children = [];
    this.parentElement = null;
    this._text = '';
    this._html = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.scrollHeight = 100;
    this.options = [];
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); this._html = ''; }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this.children = []; }
  get className() { return [...this.classList._set].join(' '); }
  set className(v) { this.classList._set = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get firstElementChild() { return this.children[0] || null; }
  appendChild(c) { c.parentElement = this; this.children.push(c); return c; }
  insertAdjacentHTML(pos, html) { this._html += html; }
  insertBefore(n, ref) { const i = this.children.indexOf(ref); i<0 ? this.children.push(n) : this.children.splice(i,0,n); n.parentElement=this; return n; }
  removeChild(c) { this.children = this.children.filter(x => x !== c); }
  remove() { if (this.parentElement) this.parentElement.removeChild(this); }
  setAttribute() {} getAttribute() { return null; }
  addEventListener() {} removeEventListener() {}
  focus() {} blur() {} scrollIntoView() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }; }
}

// Registry of "real" elements that the markup declares, keyed by id.
const byId = new Map();
function reg(id, el) { el.id = id; byId.set(id, el); return el; }

// Ids the page markup provides (checked against the rendered HTML).
const html = fs.readFileSync(process.argv[2] || '/tmp/c.html', 'utf8');
const declaredIds = [...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]);
for (const id of new Set(declaredIds)) reg(id, new El('div', id));

// Elements the JS creates by id at runtime
for (const id of ['followup-inline-input', 'c-progress-bar', 'c-stage-row', 'c-console']) reg(id, new El('div', id));

const doc = {
  documentElement: new El('html'),
  body: new El('body'),
  head: new El('head'),
  title: '',
  getElementById: id => byId.get(id) || null,
  createElement: t => new El(t),
  createTextNode: t => ({ textContent: t }),
  addEventListener: (ev, cb) => { if (ev === 'DOMContentLoaded') doc._ready = cb; },
  removeEventListener: () => {},
  querySelector: sel => {
    if (sel.startsWith('#')) return byId.get(sel.slice(1)) || null;
    return null;
  },
  querySelectorAll: sel => {
    if (sel.startsWith('#')) { const e = byId.get(sel.slice(1)); return e ? [e] : []; }
    if (sel.includes('agent_target')) {
      // six agent checkboxes, matching the real page
      return ['claude', 'codex', 'antigravity', 'minimax', 'opencode', 'khavis'].map(v => {
        const cb = new El('input'); cb.name = 'agent_target'; cb.value = v;
        cb.checked = (v === 'antigravity' || v === 'khavis');
        return cb;
      });
    }
    return [];
  },
  execCommand: () => true,
};

const ctx = {
  document: doc,
  window: {},
  console,
  performance: { now: () => Date.now() },
  navigator: { clipboard: { writeText: () => {} } },
  location: { href: 'http://localhost/' },
  localStorage: { getItem: () => null, setItem: () => {} },
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout,
  setInterval: () => 0,
  clearInterval: () => {},
  alert: m => { errors.push('alert: ' + m); },
  // 真實打到預覽伺服器（背後是正式的 status server），以逼出完整程式路徑
  fetch: async (url) => {
    try {
      const r = await realFetch('http://127.0.0.1:8081' + String(url));
      return { ok: r.ok, status: r.status, json: () => r.json(), text: () => r.text() };
    } catch (e) {
      return { ok: false, status: 0, json: async () => ({}), text: async () => '' };
    }
  },
  Event: class { constructor(t) { this.type = t; } },
  MutationObserver: class { observe() {} disconnect() {} },
  requestAnimationFrame: fn => setTimeout(fn, 0),
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  tailwind: { config: {} },
  Math, Date, JSON, String, Number, Array, Object, Boolean, RegExp, Error, Promise, Set, Map, isNaN, parseInt, parseFloat, encodeURIComponent, decodeURIComponent, btoa: s => Buffer.from(s).toString('base64'), atob: s => Buffer.from(s, 'base64').toString('binary'),
};
ctx.window = ctx;
ctx.globalThis = ctx;
ctx.self = ctx;
vm.createContext(ctx);

const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
console.log(`running ${blocks.length} script blocks…`);

for (let i = 0; i < blocks.length; i++) {
  try {
    vm.runInContext(blocks[i], ctx, { filename: `block${i}.js` });
  } catch (e) {
    errors.push(`block ${i} threw: ${e.message}\n${(e.stack || '').split('\n').slice(1, 4).join('\n')}`);
  }
}

// fire DOMContentLoaded
if (doc._ready) {
  try { doc._ready(); } catch (e) { errors.push('DOMContentLoaded threw: ' + e.message + '\n' + (e.stack || '').split('\n').slice(1, 4).join('\n')); }
}

// C 版 toast（取代阻塞式 alert）必須真的把訊息塞進 #c-toast-host
try {
  if (typeof ctx.cToast !== 'function') {
    errors.push('cToast 未定義：toast 提示功能不存在');
  } else {
    const toastHost = doc.getElementById('c-toast-host');
    if (!toastHost) {
      errors.push('#c-toast-host 不存在：toast 無法顯示');
    } else {
      ctx.cToast('smoke test 訊息', 'ok');
      if (!toastHost.children || toastHost.children.length !== 1) {
        errors.push('cToast 沒有把訊息放進 #c-toast-host');
      } else if (!/smoke test/.test(toastHost.children[0].textContent || '')) {
        errors.push('cToast 建立的元素內容不正確');
      }
    }
  }
} catch (e) {
  errors.push('cToast 驗證失敗: ' + e.message);
}

// let async work settle
setTimeout(() => {
  console.log('\n=== RESULT ===');
  if (!errors.length) console.log('no runtime errors ✅');
  else { console.log(`${errors.length} problem(s):`); errors.forEach(e => console.log(' ✗ ' + e)); }
  console.log('\nsmoke test: ' + (errors.length ? 'FAILED' : 'PASSED'));
  process.exit(errors.length ? 1 : 0);
}, 1500);
