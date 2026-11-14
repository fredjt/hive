#!/usr/bin/env node
'use strict';
// tests/cdp_stream_fetch_count.js — the T2 (0.22) request-count leg.
//
// The before/after table's request-count row, as a measurement (R3):
// open the per-spawn viewer on a simulated live spawn, let the open
// fetch settle, then append ONE line at a time (700 ms apart — outside
// the server's 50 ms nudge debounce, so each append is its own nudge)
// and count the /api/spawns/<id>/stream HTTP requests.
//
//   0.21 (the nudge-plus-refetch era): the viewer FETCHES on every
//         nudge — the measurement is one open fetch + one fetch per
//         appended line (the data takes the second hop).
//   0.22 (the push era): the open fetch stays (history seed), the
//         appended lines arrive as spawn-stream-event PUSHES on the
//         already-open /api/events channel — the measurement is one
//         open fetch + ZERO fetches while the lines land.
//
// Same zero-dependency posture as cdp_legs.js: node builtins only
// (fetch for the DevTools HTTP surface, the built-in WebSocket for
// CDP, child_process for the browser, fs for the simulated spawn).
// Node >= 22 for the built-in WebSocket.
//
// Usage:
//   node tests/cdp_stream_fetch_count.js --chrome <path> \
//     --base http://127.0.0.1:PORT --company <a COPY of the fixture
//     tree the server was launched on> --lines N
//
// Prints one JSON object: {base, lines, open_fetches,
// push_phase_fetches, total_stream_fetches, final_events}.
// Exit 0 = the run completed (the numbers are the evidence); 1 = a
// leg precondition failed (the server, the spawn, the viewer).

const fs = require('fs');
const path = require('path');
const net = require('net');
const { spawn } = require('child_process');

const args = process.argv.slice(2);
const arg = (name) => {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : null;
};
const CHROME = arg('--chrome');
const BASE = arg('--base');
const COMPANY = arg('--company');
const LINES = Math.max(1, parseInt(arg('--lines') || '5', 10));
if (!CHROME || !BASE || !COMPANY) {
  console.error('bad arguments — need --chrome, --base, --company [--lines N]');
  process.exit(2);
}
const SID = 'fetch-count-' + Math.floor(Date.now() / 1000);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
  // /tmp, not os.tmpdir(): the UNIX-socket path limit (~108 chars) —
  // a deep TMPDIR makes the launch FATAL (cdp_legs.js, same note).
  const profile = fs.mkdtempSync('/tmp/fetch-count-');
  const chromeErr = [];
  const child = spawn(CHROME, [
    '--headless', '--no-sandbox', '--disable-gpu',
    '--disable-dev-shm-usage',
    `--remote-debugging-port=${port}`,
    '--remote-allow-origins=*',
    `--user-data-dir=${profile}`,
    '--window-size=1280,900', '--lang=en-US', 'about:blank',
  ], {
    stdio: ['ignore', 'ignore', 'pipe'],
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
  let r = await fetch(dtUrl + '/json/new?' + encodeURIComponent(url),
                      { method: 'PUT' });
  if (!r.ok) r = await fetch(dtUrl + '/json/new?' + encodeURIComponent(url));
  if (!r.ok) throw new Error('failed to open a DevTools tab (HTTP ' + r.status + ')');
  return r.json();
}

class CDP {
  constructor() { this.ws = null; this.seq = 0; this.pending = new Map(); }
  static async connect(wsUrl) {
    const c = new CDP();
    await new Promise((resolve, reject) => {
      c.ws = new WebSocket(wsUrl);
      c.ws.addEventListener('open', () => resolve());
      c.ws.addEventListener('error', () =>
        reject(new Error('CDP websocket failed to open: ' + wsUrl)));
    });
    c.ws.addEventListener('message', (m) => {
      let msg; try { msg = JSON.parse(String(m.data)); } catch { return; }
      if (msg.id !== undefined && c.pending.has(msg.id)) {
        const p = c.pending.get(msg.id); c.pending.delete(msg.id);
        clearTimeout(p.timer);
        if (msg.error) p.reject(new Error(msg.error.message));
        else p.resolve(msg.result);
      }
    });
    await c.send('Page.enable');
    await c.send('Runtime.enable');
    return c;
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

async function waitFor(label, probe, timeout = 45000, interval = 250) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    let v = null;
    try { v = await probe(); } catch {}
    if (v) return v;
    await sleep(interval);
  }
  throw new Error(label + ': timeout after '
    + ((Date.now() - t0) / 1000).toFixed(1) + 's');
}

async function main() {
  const chrome = await launchChrome();
  let cdp = null;
  try {
    // The simulated spawn: a tagged active record (no pid — the
    // liveness probe would EPERM on a borrowed pid, so the record omits
    // it) + a stream file with two events (cdp_legs.js, same shape).
    const activeDir = path.join(COMPANY, 'logs', 'active');
    const streamsDir = path.join(COMPANY, 'logs', 'streams');
    fs.mkdirSync(activeDir, { recursive: true });
    fs.mkdirSync(streamsDir, { recursive: true });
    const streamFile = path.join(streamsDir, SID + '.jsonl');
    const initLines = [
      JSON.stringify({ type: 'system', subtype: 'init', spawn_id: SID,
        agent: 'ic-engineer-fetchcount', model: 'gpt-4o' }),
      JSON.stringify({ type: 'assistant', message: { content: [
        { type: 'text', text: 'Fetch-count leg: the seed line.' } ] } }),
    ];
    fs.writeFileSync(path.join(activeDir, SID + '.json'), JSON.stringify({
      spawn_id: SID, id: SID, agent: 'ic-engineer-fetchcount',
      dept: 'backend', role: 'IC', ticket: 'FETCH-COUNT',
      started_ts: Math.floor(Date.now() / 1000),
      started_at: new Date().toISOString(),
    }));
    fs.writeFileSync(streamFile, initLines.join('\n') + '\n');

    const tab = await newTab(chrome.dtUrl, BASE + '/#/activity/spawn/' + SID);
    cdp = CDP; // resolved after connect
    const conn = await CDP.connect(tab.webSocketDebuggerUrl);
    cdp = conn;
    // Every /api/spawns/<id>/stream request, as it is SENT.
    const streamFetches = [];
    conn.ws.addEventListener('message', (m) => {
      let msg; try { msg = JSON.parse(String(m.data)); } catch { return; }
      if (msg.method === 'Network.requestWillBeSent') {
        const u = (msg.params && msg.params.request
                  && msg.params.request.url) || '';
        if (u.indexOf('/api/spawns/') !== -1 && u.indexOf('/stream') !== -1) {
          streamFetches.push(new Date().toISOString());
        }
      }
    });
    await conn.send('Network.enable');

    const eventCount = async () => conn.eval(
      "(function(){var el=document.getElementById('content');if(!el)return null;" +
      "var m=el.textContent.match(/(\\d+)\\s+events/);return m?Number(m[1]):null})()");

    // Settle on the open fetch: the viewer shows its two seed events
    // (the first navigation on a fresh profile commits late — the
    // waitFor below rides out that tax).
    await waitFor('the spawn viewer at its initial count', async () => {
      const n = await eventCount();
      return n === initLines.length ? n : null;
    });
    await sleep(1000); // let any in-flight open fetch land in the count
    const openFetches = streamFetches.length;

    // The push phase: one line per nudge window, counted while it lands.
    for (let i = 1; i <= LINES; i++) {
      fs.appendFileSync(streamFile, JSON.stringify({
        type: 'assistant', message: { content: [{ type: 'text',
          text: 'Fetch-count leg: appended line ' + i + ' of ' + LINES + '.' }] },
      }) + '\n');
      await sleep(700);
    }
    await waitFor('the event count to climb to ' + (initLines.length + LINES), async () => {
      const n = await eventCount();
      return (n !== null && n >= initLines.length + LINES) ? n : null;
    });
    await sleep(1000); // the last era-appropriate fetch may trail the render

    const total = streamFetches.length;
    console.log(JSON.stringify({
      base: BASE,
      lines: LINES,
      open_fetches: openFetches,
      push_phase_fetches: total - openFetches,
      total_stream_fetches: total,
      final_events: initLines.length + LINES,
    }));
  } finally {
    if (cdp) cdp.close();
    try { chrome.child.kill('SIGKILL'); } catch {}
    try { fs.rmSync(chrome.profile, { recursive: true, force: true }); } catch {}
  }
}

main().catch((e) => {
  console.error('fetch-count leg failed: ' + (e && e.message || e));
  process.exit(1);
});
