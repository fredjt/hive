#!/usr/bin/env node
'use strict';
// tests/cdp_legs.js — T5 (0.22): the standing headless-browser gate for
// the 0.21+ dashboard floor.
//
// The 0.21 cut receipt proved the headless legs ONCE (a manual chromium
// session — Watch on a live spawn, Watch Global, refresh-stays, the
// gh-absent diagnostic). A receipt is evidence, not a gate: this script
// is the standing proof, run in CI (the browser-legs job in
// .github/workflows/ci.yml) against the RUNNER'S Chrome — the job
// installs nothing browser-related (the zero-dependency posture is a
// contract, pinned in tests/test_dashboard.py), and this driver uses
// NODE BUILTINS ONLY: fetch (the DevTools HTTP surface), the built-in
// WebSocket (the CDP channel, node >= 22), child_process (the browser
// launch), fs (the simulated company writes). No npm, no puppeteer, no
// build step — the same posture as the rest of the tree's test surface.
//
// The server is the REAL dashboard (node lib/dashboard/server.js) over a
// COPY of the C2 fixture tree (the _copy_project shape — the committed
// tree stays pristine; the simulated writes go to the copy). Two
// servers run in parallel: the main one (full PATH — CI's runner has gh)
// and the gh-absent one (a PATH without gh — the diagnostic leg needs
// the failure, and the runner's /usr/local/bin/gh must not leak in).
//
// Legs (a SUPERSET of the 0.21 receipt's leg set — the receipts keep
// the before/after figures as evidence, these are the gates):
//   L1 transport-live   on an IDLE company the live indicator reaches
//                       'live' at page load (0.21 T2: the response head
//                       + ': connected' arrive at connect; the heartbeat
//                       keeps the pipe warm).
//   L2 spawn-push       #/activity/spawn/<id> on a SIMULATED live
//                       spawn — the viewer's event count climbs when a
//                       new line lands in the stream file (0.22 T2: the
//                       event rides the SSE push; the reader never
//                       navigates or reloads).
//   L3 reload-stays     reload from #/github stays on #/github (0.21 T4:
//                       hash routing + rehydration — a refresh keeps the
//                       user where they are).
//   L4 chat-pill        a message arrives while the reader is scrolled
//                       up in a DEEP-LINKED channel — the 'N new — jump
//                       to the latest' pill appears (0.22 T3; the
//                       deep-link entry pins the 0.22 T5 view-identity
//                       fix — a refreshed chat tab is a deep link).
//   L5 gh-absent-diag   a server whose PATH has no gh renders the
//                       diagnostic block (the failing check named + the
//                       fix), not two empty tables (0.21 T3).
//   L6 log-deep-link    a DEEP-LINKED #/logs/<file> viewer receives a
//                       live append (0.22 T5 view-identity fix, the
//                       logs face — the refresh path is a deep link).
//
// Usage:
//   node tests/cdp_legs.js --chrome <path-to-chrome> \
//     --base http://127.0.0.1:PORT   (the main server, full PATH)
//     --ghbase http://127.0.0.1:PORT (the gh-absent server)
//     --company <dir>                (a COPY of the C2 fixture tree)
//
// Exit 0 = every leg passed; 1 = any leg failed (the failure says what
// was waited for, how long, and what the page showed instead).

const fs = require('fs');
const path = require('path');
const net = require('net');
const { spawn } = require('child_process');

// ── args ────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const arg = (name) => {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : null;
};
const CHROME = arg('--chrome');
const BASE = arg('--base');
const GHBASE = arg('--ghbase');
const COMPANY = arg('--company');
if (!CHROME || !BASE || !GHBASE || !COMPANY) {
  console.error('bad arguments — need --chrome, --base, --ghbase, --company');
  console.error('see the header of tests/cdp_legs.js');
  process.exit(2);
}
// Fresh id per run: the server's per-spawn push cursor is PROCESS state
// (T2, 0.22 — a restart resets it, the client's wire cursor is truth).
// Reusing a fixed id against a long-lived server (a dev-box rerun) makes
// the cursor stale after the leg's truncating rewrite (lines <= cursor:
// no push), so the id must be new every run.
const SID = 'cdp-leg-spawn-' + Math.floor(Date.now() / 1000);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── Chrome + CDP (node builtins only) ──────────────────────────────────
async function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.listen(0, '127.0.0.1', () => {
      const p = s.address().port;
      s.close(() => resolve(p));
    });
    s.on('error', reject);
  });
}

async function launchChrome() {
  const port = await freePort();
  // /tmp, not os.tmpdir(): Chrome's SingletonSocket lives in the profile
  // dir and the path is UNIX-socket limited (~108 chars) — a deep TMPDIR
  // (this dev box's is <repo>/unsloth-tmp) makes the launch FATAL with
  // 'Socket path too long'. /tmp is the short, POSIX-guaranteed prefix.
  const profile = fs.mkdtempSync('/tmp/cdp-legs-');
  const chromeErr = [];
  const child = spawn(CHROME, [
    '--headless',
    '--no-sandbox',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    `--remote-debugging-port=${port}`,
    '--remote-allow-origins=*',
    `--user-data-dir=${profile}`,
    '--window-size=1280,900',
    '--lang=en-US',
    'about:blank',
  ], {
    stdio: ['ignore', 'ignore', 'pipe'],
    // Chrome's process-singleton socket derives from TMPDIR even when
    // --user-data-dir is set, and the path is UNIX-socket limited
    // (~108 chars): a deep TMPDIR (this dev box's is <repo>/unsloth-tmp)
    // makes the launch FATAL with 'Socket path too long'. /tmp is the
    // short, POSIX-guaranteed prefix (the CI runner's TMPDIR is short
    // already — the override is a no-op there).
    env: { ...process.env, TMPDIR: '/tmp' },
  });
  child.stderr.on('data', (d) => {
    chromeErr.push(d);
    if (chromeErr.length > 50) chromeErr.shift();
  });
  const dtUrl = `http://127.0.0.1:${port}`;
  const t0 = Date.now();
  let info = null;
  while (!info && Date.now() - t0 < 60000) {
    try {
      const r = await fetch(dtUrl + '/json/version');
      if (r.ok) info = await r.json();
    } catch {}
    if (!info) await sleep(250);
  }
  if (!info) {
    child.kill('SIGKILL');
    throw new Error('Chrome DevTools endpoint never came up — '
      + Buffer.concat(chromeErr).toString('utf8').slice(-2000));
  }
  return { child, profile, dtUrl };
}

async function newTab(dtUrl, url) {
  // Chrome >= 111 takes PUT for /json/new (GET is the cross-origin
  // shape); fall back to GET for older builds.
  let r = await fetch(dtUrl + '/json/new?' + encodeURIComponent(url),
                      { method: 'PUT' });
  if (!r.ok) r = await fetch(dtUrl + '/json/new?' + encodeURIComponent(url));
  if (!r.ok) throw new Error('failed to open a DevTools tab (HTTP ' + r.status + ')');
  return r.json();
}

class CDP {
  constructor() {
    this.ws = null; this.seq = 0;
    this.pending = new Map(); this.listeners = new Map();
  }
  static async connect(wsUrl) {
    const c = new CDP();
    await new Promise((resolve, reject) => {
      c.ws = new WebSocket(wsUrl);
      c.ws.addEventListener('open', () => resolve());
      c.ws.addEventListener('error', () =>
        reject(new Error('CDP websocket failed to open: ' + wsUrl)));
    });
    c.ws.addEventListener('message', (m) => c._onMessage(String(m.data)));
    await c.send('Page.enable');
    await c.send('Runtime.enable');
    return c;
  }
  _onMessage(text) {
    let msg;
    try { msg = JSON.parse(text); } catch { return; }
    if (msg.id !== undefined && this.pending.has(msg.id)) {
      const p = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.error) p.reject(new Error(msg.error.message));
      else p.resolve(msg.result);
    } else if (msg.method) {
      const ls = this.listeners.get(msg.method) || [];
      for (const l of ls) { try { l(msg.params || {}); } catch {} }
    }
  }
  send(method, params) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('CDP timeout on ' + method));
      }, 30000);
      this.pending.set(id, { resolve, reject, timer });
      this.ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
  }
  on(method, fn) {
    const ls = this.listeners.get(method) || [];
    ls.push(fn); this.listeners.set(method, ls);
  }
  async eval(expression) {
    const r = await this.send('Runtime.evaluate',
                              { expression, returnByValue: true });
    if (r.exceptionDetails) {
      const d = r.exceptionDetails;
      throw new Error('page eval threw: '
        + (d.exception && d.exception.description || d.text || 'unknown')
          .slice(0, 300));
    }
    return r.result ? r.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch {} }
}

// ── leg machinery ───────────────────────────────────────────────────────
const results = [];
let ACTIVE_CDP = null;  // main() sets it — the leg() failure diagnostic needs it

async function waitFor(label, probe, timeout = 25000, interval = 250) {
  const t0 = Date.now();
  let last = null;
  while (Date.now() - t0 < timeout) {
    try { last = await probe(); } catch (e) { last = 'probe error: ' + e.message; }
    if (last) return last;
    await sleep(interval);
  }
  throw new Error(`${label}: timeout after ${((Date.now() - t0) / 1000).toFixed(1)}s — last probe: ${JSON.stringify(last)}`);
}

async function leg(name, fn) {
  const t0 = Date.now();
  try {
    const detail = await fn();
    results.push({ name, ok: true, ms: Date.now() - t0, detail: detail || '' });
    console.log(`LEG ${name.padEnd(16)} PASS (${((Date.now() - t0) / 1000).toFixed(1)}s)${detail ? '  ' + detail : ''}`);
  } catch (e) {
    results.push({ name, ok: false, ms: Date.now() - t0, detail: e.message });
    let diag = '';
    if (ACTIVE_CDP) {
      try {
        const d = await ACTIVE_CDP.eval(
          "(function(){var c=document.getElementById('content');return JSON.stringify({href:location.href,view:(typeof activeView!=='undefined')?activeView:'?',head:c?c.textContent.slice(0,140):null})})()");
        diag = '  | page: ' + String(d).slice(0, 240);
      } catch (_) {}
    }
    console.log(`LEG ${name.padEnd(16)} FAIL (${((Date.now() - t0) / 1000).toFixed(1)}s)  ${e.message}${diag}`);
  }
}

// ── main ────────────────────────────────────────────────────────────────
// ── watcher canary ─────────────────────────────────────────────────────
// Proves the main server is watching THIS tree before any leg may
// measure a file event. Two traps produce the otherwise-inexplicable
// "all file-event legs time out" shape: (a) a server launched with a
// --dir different from --company; (b) the watched tree REPLACED after
// the server started (rm -rf + cp -r, a dev-box fixture refresh) —
// fs.watch binds to the directory's INODE, so the recreated dir is
// invisible to the old watch. The canary writes a throwaway stream file
// and listens on the SSE endpoint for the watcher's event; a stale or
// mismatched watch never answers, and the run dies with a named cause
// instead of three 25-second leg timeouts.
async function canaryWatch(company) {
  const streamsDir = path.join(company, 'logs', 'streams');
  fs.mkdirSync(streamsDir, { recursive: true });
  const canaryFile = path.join(streamsDir, 'cdp-canary-' + Date.now() + '.jsonl');
  const seen = [];
  const ctrl = new AbortController();
  const reader = (await fetch(BASE + '/api/events',
                              { signal: ctrl.signal })).body.getReader();
  let buf = '';
  const drain = (async () => {
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += Buffer.from(value).toString();
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          const m = chunk.match(/^event: (.+)$/m);
          if (m) seen.push(m[1]);
        }
      }
    } catch {}
  })();
  fs.writeFileSync(canaryFile,
    JSON.stringify({ type: 'system', subtype: 'init', spawn_id: 'cdp-canary' }) + '\n');
  const t0 = Date.now();
  while (!seen.length && Date.now() - t0 < 10000) await sleep(200);
  ctrl.abort();
  try { fs.unlinkSync(canaryFile); } catch {}
  drain.catch(() => {});
  if (!seen.length) {
    throw new Error('the watcher canary got no SSE event in 10 s — the '
      + 'server is not watching this tree: it was launched with a --dir '
      + 'different from --company, or the tree was REPLACED (rm -rf + cp) '
      + 'after the server started (fs.watch binds to the inode). Restart '
      + 'the server against the copy that will be tested.');
  }
  return seen.slice(0, 3).join(' + ');
}

async function main() {
  const chrome = await launchChrome();
  let cdp = null;
  try {
    // Both servers must be up before the first leg touches them.
    for (const [label, base] of [['main', BASE], ['gh-absent', GHBASE]]) {
      await waitFor(`server ${label} answers /api/status`, async () => {
        const r = await fetch(base + '/api/status');
        return r.ok ? true : null;
      }, 30000);
    }
    // The canary must pass before any leg may measure a file event.
    {
      const saw = await canaryWatch(COMPANY);
      console.log('CANARY watch alive  saw: ' + saw);
    }
    const tab = await newTab(chrome.dtUrl, BASE + '/?leg=open#/dashboard');
    cdp = await CDP.connect(tab.webSocketDebuggerUrl);
    ACTIVE_CDP = cdp;

    // The chat leg's history, appended BEFORE any page opens: the
    // resulting chat-change (500 ms debounce) must die with zero clients
    // attached — if it fired while the chat page was booting, it would
    // race the page's first render (a concurrent showChannel), and the
    // scrolled-up reader's position is what the leg measures.
    {
      const cf = path.join(COMPANY, 'chat', 'engineering.md');
      let raw = fs.readFileSync(cf, 'utf8');
      const hist = [];
      for (let i = 1; i <= 30; i++) {
        hist.push('---\n\n**[2026-11-04T11:' + String(Math.floor(i / 60)).padStart(2, '0') + ':' + String(i % 60).padStart(2, '0') + ':00Z] ic-engineer-history**\nHistory row ' + i + ' for the CDP chat leg — long enough to be a real transcript line.');
      }
      fs.appendFileSync(cf, (raw.endsWith('\n') ? '' : '\n') + hist.join('\n') + '\n');
    }

    // nav: a unique QUERY per leg forces a FULL load (a hash-only change
    // would be a same-document jump — fine for routing, wrong for the
    // isolation these legs need: fresh page state, fresh SSE connection).
    async function nav(legName, base, hash) {
      const url = base + '/?leg=' + legName + hash;
      await cdp.send('Page.navigate', { url });
      // Wait for the COMMITTED navigation — not document.readyState:
      // on a fresh profile the first navigation to a new origin commits
      // late (the tab still reports about:blank / readyState 'complete'),
      // and polling state on the pre-navigation page reads nothing.
      await waitFor(`page ${legName} to commit`, async () => {
        const h = await cdp.eval('location.href');
        return h === url ? true : null;
      }, 45000);
      await sleep(300); // settle: checkStatus's async chain starts the boot
    }
    const content = async () => cdp.eval(
      "(function(){var el=document.getElementById('content');return el?el.textContent:null})()");

    // L1 — transport: the live indicator reaches 'live' at page load
    // on an IDLE company (the head + ': connected' arrive at connect;
    // the heartbeat keeps the byteless pipe alive).
    await leg('transport-live', async () => {
      await nav('transport', BASE, '#/dashboard');
      const live = await waitFor("the live indicator's 'live' state",
        async () => {
          const t = await cdp.eval(
            "(function(){var el=document.getElementById('live-indicator');return el?el.textContent:null})()");
          return (t && t.indexOf('live') !== -1 && t.indexOf('reconnecting') === -1) ? t : null;
        });
      return `indicator read '${live.trim()}'`;
    });

    // L5 — gh-absent: the diagnostic block names the failing check +
    // the fix (0.20 showed a warning badge over two empty tables).
    await leg('gh-absent-diag', async () => {
      await nav('ghdiag', GHBASE, '#/github');
      const t = await waitFor('the gh-absent diagnostic block', async () => {
        const c = await content();
        return (c && c.indexOf('GitHub sync is not working') !== -1) ? c : null;
      });
      for (const needle of ['gh on PATH', 'FAILED']) {
        if (t.indexOf(needle) === -1) {
          throw new Error('the diagnostic block is missing "' + needle + '" — ' +
            t.slice(0, 300));
        }
      }
      // The fix line depends on the HOST: where the restricted PATH truly
      // has no gh the failing check is 'gh on PATH' (install: cli.github.com);
      // on the CI runner gh lives in /usr/bin (on the restricted PATH) but
      // is NOT authenticated there — the failing check is 'gh authenticated'
      // (gh auth login). Both faces are the diagnostic block: a named
      // failing check + an actionable fix, not two empty tables.
      if (t.indexOf('install: https://cli.github.com') === -1 &&
          t.indexOf('gh auth login') === -1) {
        throw new Error('the diagnostic block names no actionable fix — ' +
          t.slice(0, 300));
      }
      return 'named the failing check + the fix';
    });

    // L3 — routing: reload from #/github stays on #/github.
    await leg('reload-stays', async () => {
      await nav('routing', BASE, '#/github');
      await waitFor('the GitHub page (before reload)', async () => {
        const c = await content();
        return (c && c.indexOf('GitHub') !== -1) ? true : null;
      });
      await cdp.send('Page.reload', {});
      await waitFor('the GitHub page (after reload)', async () => {
        const rs = await cdp.eval('document.readyState');
        if (rs !== 'complete') return null;
        const c = await content();
        return (c && c.indexOf('GitHub') !== -1) ? true : null;
      }, 30000);
      const hash = await cdp.eval('location.hash');
      if (hash !== '#/github') {
        throw new Error('after reload the hash is ' + JSON.stringify(hash)
          + ' — the refresh threw the user off #/github');
      }
      return 'reloaded and stayed on #/github';
    });

    // L2 — push: the spawn viewer's event count climbs when a new line
    // lands in the stream file. The SIMULATED spawn: a tagged active
    // record (no pid — the liveness probe would EPERM on a borrowed
    // pid, so the record omits it and stays 'alive') + a stream file
    // with two events. The append is the push under test.
    const activeDir = path.join(COMPANY, 'logs', 'active');
    const streamsDir = path.join(COMPANY, 'logs', 'streams');
    fs.mkdirSync(activeDir, { recursive: true });
    fs.mkdirSync(streamsDir, { recursive: true });
    const streamFile = path.join(streamsDir, SID + '.jsonl');
    const initLines = [
      JSON.stringify({ type: 'system', subtype: 'init', spawn_id: SID,
        agent: 'ic-engineer-cdp', model: 'gpt-4o' }),
      JSON.stringify({ type: 'assistant', message: { content: [
        { type: 'text', text: 'Picking up CDP-LEG: working the rate limiter fix.' } ] } }),
    ];
    const pushLine = JSON.stringify({ type: 'result', subtype: 'success',
      is_error: false, num_turns: 2,
      result: 'CDP leg: the pushed line landed in the open viewer.' });
    fs.writeFileSync(path.join(activeDir, SID + '.json'), JSON.stringify({
      spawn_id: SID, id: SID, agent: 'ic-engineer-cdp', dept: 'backend',
      role: 'IC', ticket: 'CDP-LEG',
      started_ts: Math.floor(Date.now() / 1000),
      started_at: new Date().toISOString(),
    }));
    fs.writeFileSync(streamFile, initLines.join('\n') + '\n');

    const eventCount = async () => cdp.eval(
      "(function(){var el=document.getElementById('content');if(!el)return null;var m=el.textContent.match(/(\\d+)\\s+events/);return m?Number(m[1]):null})()");

    await leg('spawn-push', async () => {
      await nav('spawn', BASE, '#/activity/spawn/' + SID);
      const before = await waitFor('the spawn viewer at its initial count',
        async () => {
          const n = await eventCount();
          return n === initLines.length ? n : null;
        });
      fs.appendFileSync(streamFile, pushLine + '\n');
      const after = await waitFor('the event count to climb from the push',
        async () => {
          const n = await eventCount();
          return (n !== null && n > before) ? n : null;
        });
      return `viewer climbed ${before} -> ${after} events (no navigation)`;
    });

    // L4 — chat pill: a message arrives while the reader is scrolled up
    // in a DEEP-LINKED channel. The channel's real history (30 rows) was
    // appended at driver start (before ANY page opened — a residual
    // chat-change racing the chat page's boot would race its own render,
    // and the atBottom decision is a 16px threshold the one-line fixture
    // channel has no room to satisfy).
    const chatFile = path.join(COMPANY, 'chat', 'engineering.md');

    await leg('chat-pill', async () => {
      await nav('chat', BASE, '#/chat/engineering');
      await waitFor('the chat transcript to be scrollable', async () => {
        const p = await cdp.eval(
          "(function(){var m=document.getElementById('chat-msgs');return m?[m.scrollHeight-m.clientHeight,null]:[0,null]})()");
        return Array.isArray(p) && p[0] > 16 ? p[0] : null;
      });
      // Settle: the header's message count must be STABLE (two polls)
      // before the leg measures — a concurrent re-render in flight would
      // otherwise reset the reader's position after the scroll below.
      let last = null, stable = 0;
      while (stable < 2) {
        const c = await cdp.eval(
          "(function(){var h=document.querySelector('#chat-view .chat-header');return h?h.textContent:null})()");
        if (c === last) stable++; else { stable = 0; last = c; }
        await sleep(300);
      }
      // Scrolled-up reader: pin scrollTop to 0 (re-pin if a late render
      // replaces the element and resets it to the tail).
      for (let i = 0; i < 5; i++) {
        await cdp.eval("(function(){var m=document.getElementById('chat-msgs');if(m)m.scrollTop=0;return true})()");
        await sleep(300);
        const st = await cdp.eval(
          "(function(){var m=document.getElementById('chat-msgs');return m?m.scrollTop:null})()");
        if (st === 0) break;
      }
      fs.appendFileSync(chatFile,
        '\n---\n\n**[2026-11-04T12:00:00Z] ic-engineer-cdp**\nThe message the leg is waiting for — it arrived while you were reading history.\n');
      const pill = await waitFor('the \'N new — jump to latest\' pill', async () => {
        const t = await cdp.eval(
          "(function(){var p=document.getElementById('chat-new-pill');return p?p.textContent:null})()");
        return (t && t.indexOf('new') !== -1) ? t : null;
      });
      return `pill read '${pill.trim()}' after the scrolled-up append`;
    });

    // L6 — log deep link: the deep-linked log viewer receives a live
    // append (the refresh path is a deep link — a stale viewer would
    // render once and never see the line).
    const logFile = path.join(COMPANY, 'logs', 'llm-usage.log');
    const logLines = fs.readFileSync(logFile, 'utf8').split('\n').filter(Boolean);
    const lastLine = logLines[logLines.length - 1];
    const marker = 'cdp-leg-marker-' + Date.now();

    await leg('log-deep-link', async () => {
      await nav('logs', BASE, '#/logs/llm-usage.log');
      await waitFor('the deep-linked log viewer to render its content', async () => {
        const c = await cdp.eval(
          "(function(){var el=document.getElementById('log-content');return el?el.textContent:null})()");
        return (c && c.indexOf(lastLine) !== -1) ? true : null;
      });
      fs.appendFileSync(logFile, marker + '\n');
      await waitFor('the appended line to reach the live viewer', async () => {
        const c = await cdp.eval(
          "(function(){var el=document.getElementById('log-content');return el?el.textContent:null})()");
        return (c && c.indexOf(marker) !== -1) ? true : null;
      });
      return 'the live append landed in the deep-linked viewer';
    });
  } finally {
    if (cdp) cdp.close();
    try { chrome.child.kill('SIGKILL'); } catch {}
    try { fs.rmSync(chrome.profile, { recursive: true, force: true }); } catch {}
  }

  const failed = results.filter((r) => !r.ok);
  console.log('──────────────────────────────────────────────────');
  console.log((failed.length
    ? 'CDP LEGS: ' + failed.length + '/' + results.length + ' FAILED'
    : 'CDP LEGS: all ' + results.length + ' passed') + '  (chrome: ' + CHROME + ')');
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => {
  console.error('CDP legs crashed: ' + (e && e.stack || e));
  process.exit(1);
});
