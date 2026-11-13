// SSE file-watcher: watches the live .company tree and pushes
// change events to every connected browser tab (G8: split out
// of server.js).
const fs = require('fs');
const path = require('path');

const sseClients = new Set();
let fileWatchers = [];

// T2 (0.22): the stream event rides the SSE channel — push, not
// nudge-plus-refetch. The per-spawn push cursor is the lines ALREADY
// PUSHED for each stream file (the delta is read from here, so nothing
// is ever resent); the global cursor is the per-spawn line index the
// server-side merge has pushed. Both are PROCESS state: a server
// restart resets them, and the client's wire catch-up (the demoted
// cursor fetch on open / SSE reconnect) is the source of truth — the
// client dedupes on (from, next) per spawn and on (spawn_id, line)
// globally, so a stale push is ignored, never duplicated.
const streamPushCursor = {};  // spawn_id -> lines already pushed
let globalPushCursor = {};    // spawn_id -> lines the merge has pushed

function readStreamLines(file) {
  let lines = [];
  try { lines = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean); } catch {}
  return lines;
}

function parseStreamLine(line) {
  try { return JSON.parse(line); }
  catch { return { type: 'parse_error', raw: String(line).slice(0, 500) }; }
}

function broadcastSSE(event, data) {
  const msg = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const res of sseClients) {
    try { res.write(msg); } catch { sseClients.delete(res); }
  }
}

// T2 (0.22): the per-spawn push — `spawn-stream-event` with the DELTA
// since this process last pushed for the file (streamPushCursor), so
// a second append carries only the new lines (no resend). `from` +
// `next` let the client slice away any overlap after a server restart
// (the cursor is process state; the client's wire cursor is truth).
function pushStreamDelta(companyDir, spawnId) {
  try {
    const file = path.join(companyDir, 'logs', 'streams', spawnId + '.jsonl');
    const lines = readStreamLines(file);
    const from = streamPushCursor[spawnId] || 0;
    if (lines.length <= from) return;
    const events = [];
    for (let i = from; i < lines.length; i++) events.push(parseStreamLine(lines[i]));
    // The cursor advances only AFTER the broadcast succeeds — a throw
    // mid-broadcast retries the same window next push, never skips.
    broadcastSSE('spawn-stream-event', {
      spawn_id: spawnId,
      from: from,
      events: events,
      next: lines.length,
      total: lines.length,
      time: new Date().toISOString(),
    });
    streamPushCursor[spawnId] = lines.length;
  } catch {}
  // Runs from a setTimeout: a throw here is an uncaught exception that
  // kills the server. The wire cursor endpoints are the truth; a missed
  // push is caught by the client's reconnect catch-up.
}

// T2 (0.22): the global push — `spawn-stream-global`. The time-ordered
// merge across spawn files moves SERVER-SIDE (the same shape as
// /api/spawns/global-stream: meta from logs/active/, wrapped events,
// sort by started_ts then line): the client's global-stream poll is
// replaced as the LIVE path and demoted to open / reconnect catch-up.
// active_count tracks the registry create / remove (read at push time).
function pushGlobalDelta(companyDir) {
  try { _pushGlobalDelta(companyDir); } catch {}
}

function _pushGlobalDelta(companyDir) {
  const activeDir = path.join(companyDir, 'logs', 'active');
  const registryMeta = {};
  try {
    for (const f of fs.readdirSync(activeDir)) {
      if (!f.endsWith('.json')) continue;
      try {
        const rec = JSON.parse(fs.readFileSync(path.join(activeDir, f), 'utf8'));
        // The same fallback chain as /api/spawns/active — 0.20 orphan
        // records (id only) stay addressable.
        const sid = rec.spawn_id || rec.id || path.basename(f, '.json');
        if (sid) {
          registryMeta[sid] = { agent: rec.agent, dept: rec.dept,
            role: rec.role, ticket: rec.ticket,
            started_ts: rec.started_ts || 0 };
        }
      } catch {}
    }
  } catch {}
  // Copy, don't alias: the cursor loop below adds completed-spawn
  // fallbacks to `meta`, and active_count must count the REGISTRY only.
  const meta = Object.assign({}, registryMeta);
  // Spawns that COMPLETED mid-flight stay pushable: the endpoint keeps
  // their tail alive via the CLIENT's cursor loop; the push has its own
  // cursor loop to do the same (a record removed before its final lines
  // were pushed would otherwise drop that tail until a reconnect).
  for (const sid of Object.keys(globalPushCursor)) {
    if (!meta[sid]) meta[sid] = { agent: '?', dept: '?', role: '?',
      ticket: null, started_ts: 0 };
  }
  const all = [];
  const newCursor = {};
  for (const sid of Object.keys(meta)) {
    const lines = readStreamLines(
      path.join(companyDir, 'logs', 'streams', sid + '.jsonl'));
    const start = globalPushCursor[sid] || 0;
    for (let i = start; i < lines.length; i++) {
      all.push({
        spawn_id: sid,
        line: i,
        agent: meta[sid].agent,
        dept: meta[sid].dept,
        role: meta[sid].role,
        ticket: meta[sid].ticket,
        event: parseStreamLine(lines[i]),
      });
    }
    newCursor[sid] = lines.length;
  }
  if (!all.length) return;
  // Sort by spawn started_ts then line index — the endpoint's roughly-
  // chronological interleave (per-event timestamps are not universal).
  all.sort((a, b) => {
    const ta = (meta[a.spawn_id] && meta[a.spawn_id].started_ts) || 0;
    const tb = (meta[b.spawn_id] && meta[b.spawn_id].started_ts) || 0;
    if (ta !== tb) return ta - tb;
    return a.line - b.line;
  });
  let total = 0;
  for (const sid of Object.keys(newCursor)) total += newCursor[sid];
  // The cursors swap in only AFTER the broadcast succeeds — a throw
  // mid-broadcast retries the same window next push, never skips.
  broadcastSSE('spawn-stream-global', {
    events: all,
    next: Buffer.from(JSON.stringify(newCursor)).toString('base64'),
    total: total,
    active_count: Object.keys(registryMeta).length,
    time: new Date().toISOString(),
  });
  globalPushCursor = newCursor;
}

function setupWatchers(companyDir) {
  // Clean up old watchers
  for (const w of fileWatchers) try { w.close(); } catch {}
  fileWatchers = [];
  if (!companyDir) return;

  // Pre-create the canonical subdirs we want to watch. fs.watch can only
  // attach to existing paths, and many of these (sprints/, logs/streams/,
  // them empty here lets watchers attach now so the very first daemon
  // write fires an SSE event without needing a re-scan.
  for (const sub of ['sprints/history', 'logs/streams', 'logs/active', 'reports']) {
    const p = path.join(companyDir, sub);
    try { fs.mkdirSync(p, { recursive: true }); } catch {}
  }

  // Per-event debounce windows. Most events collapse rapid bursts at 500 ms
  // (ticket moves, sprint state, roster edits — all human-paced writes where
  // sub-second redundancy is pure overhead). Stream and cycle log files are
  // different: they're append-only, written by the agent harness / sprint-lifecycle at
  // many-events-per-second, and the client's cursor-based fetch with an
  // in-flight guard naturally serializes refreshes. A 500 ms debounce on
  // these channels swallows everything between the first and last event of
  // each burst (e.g., a tool_use + tool_result pair landing in the same
  // ~100 ms as the assistant message that triggered them) — the user only
  // sees the leading edge and assumes "only assistant events fire a refresh."
  // Drop their debounce to 50 ms to keep coalescing micro-bursts without
  // hiding intra-burst events.
  // Ticket status dirs, local to this module (NOT imported from
  // api/handlers.js, which requires THIS module for sseClients /
  // broadcastSSE — a top-level import back would bind the partially
  // initialised circular exports and read STATUSES as undefined, the
  // ReferenceError that made setupWatchers throw for any company with a
  // department ticket board).
  const TICKET_STATUSES = ['backlog', 'sprint-current', 'in-progress',
                           'in-review', 'done'];
  const DEBOUNCE_FAST = { 'spawn-stream-change': true, 'cycle-change': true, 'log-change': true };
  const debounce = {};
  function emit(event, key, payload) {
    const k = event + ':' + key;
    if (debounce[k]) return;
    debounce[k] = true;
    const ttl = DEBOUNCE_FAST[event] ? 50 : 500;
    setTimeout(() => { delete debounce[k]; }, ttl);
    broadcastSSE(event, { ...payload, time: new Date().toISOString() });
  }
  function watchDir(dir, event, keyPrefix, extraFields) {
    if (!fs.existsSync(dir)) return;
    try {
      const w = fs.watch(dir, (ev, fn) => emit(event, keyPrefix + '/' + (fn || ''),
        { event: ev, file: fn, ...(extraFields || {}) }));
      fileWatchers.push(w);
    } catch {}
  }
  // watchFile watches a single file by name within its parent directory.
  // Using the parent-dir watcher (rather than fs.watch(file)) lets us catch
  // both creation and modification — important because processes.json,
  // sprints/current.json, etc. don't exist until daemons start, and we
  // don't want to re-run setupWatchers on every connect.
  function watchFile(file, event, keyPrefix, extraFields) {
    const dir = path.dirname(file);
    const basename = path.basename(file);
    if (!fs.existsSync(dir)) return;
    try {
      const w = fs.watch(dir, (ev, fn) => {
        if (fn !== basename) return;
        emit(event, keyPrefix, { event: ev, file: basename, ...(extraFields || {}) });
      });
      fileWatchers.push(w);
    } catch {}
  }

  // ── Tickets (per-dept, per-status) ────────────────────────────────────
  const deptDir = path.join(companyDir, 'departments');
  if (fs.existsSync(deptDir)) {
    for (const dept of fs.readdirSync(deptDir)) {
      const ticketBase = path.join(deptDir, dept, 'tickets');
      if (fs.existsSync(ticketBase)) {
        for (const status of [...TICKET_STATUSES, 'archive']) {
          watchDir(path.join(ticketBase, status), 'ticket-change', dept + '/' + status,
                   { department: dept });
        }
      }
      // Per-dept reports drive the report-change SSE event.
      watchDir(path.join(deptDir, dept, 'reports'), 'report-change', 'dept:' + dept,
               { department: dept, scope: 'department' });
    }
  }

  // ── Chat ──────────────────────────────────────────────────────────────
  watchDir(path.join(companyDir, 'chat'), 'chat-change', 'chat');

  // ── Logs ──────────────────────────────────────────────────────────────
  // Generic log-change for the dir; cycle-change for the three specific
  // logs the activity panel cares about; spawn-stream-change for per-spawn
  // stream files under logs/streams/.
  const logDir = path.join(companyDir, 'logs');
  watchDir(logDir, 'log-change', 'logs');
  watchFile(path.join(logDir, 'cycles.jsonl'),     'cycle-change', 'cycles');
  watchFile(path.join(logDir, 'spawns.log'),       'cycle-change', 'spawns');
  watchFile(path.join(logDir, 'sprint-lifecycle.log'), 'cycle-change', 'sprint-lifecycle');
  // ── Stream files: the legacy nudge AND the push (T2, 0.22) ────────
  // The nudge (spawn-stream-change, immediate + 50 ms suppression) is
  // UNCHANGED — the activity table's Events column / live total still
  // ride the spawns/active poll on it. The push is a TRAILING 50 ms
  // debounce per file: a micro-burst of writes coalesces into ONE delta
  // read + ONE broadcast, not one push per line; the delta is per-file
  // (streamPushCursor) so no line is ever resent.
  {
    const streamsDir = path.join(logDir, 'streams');
    const pushTimers = {};
    function scheduleStreamPush(spawnId, dir) {
      if (pushTimers[spawnId]) return;
      pushTimers[spawnId] = setTimeout(() => {
        delete pushTimers[spawnId];
        pushStreamDelta(dir, spawnId);
        pushGlobalDelta(dir);
      }, 50);
      if (pushTimers[spawnId].unref) pushTimers[spawnId].unref();
    }
    if (fs.existsSync(streamsDir)) {
      try {
        const w = fs.watch(streamsDir, (ev, fn) => {
          if (!fn) return;
          try {
            emit('spawn-stream-change', 'streams/' + fn, { event: ev, file: fn });
            if (fn.endsWith('.jsonl'))
              scheduleStreamPush(fn.slice(0, -'.jsonl'.length), companyDir);
          } catch {}
          // A watcher-callback throw is an UNCAUGHT exception that kills
          // the whole server process — the nudge and the push stay live
          // no matter what a single read goes wrong on.
        });
        fileWatchers.push(w);
      } catch {}
    }
  }
  // T2 (0.21): the spawn registry (logs/active/<id>.json) — a spawn
  // STARTING or STOPPING is an event of its own: 0.20 only updated the
  // activity list on the next unrelated event, and nothing refreshed the
  // processes or roster views from a spawn start/stop at all.
  watchDir(path.join(logDir, 'active'),            'spawn-registry-change', 'active');

  // ── Processes (active agent spawns + daemon registry) ─────────────────
  watchFile(path.join(companyDir, 'processes.json'), 'process-change', 'processes');

  // ── Sprint state ──────────────────────────────────────────────────────
  watchFile(path.join(companyDir, 'sprints', 'current.json'), 'sprint-change', 'current');
  watchDir(path.join(companyDir, 'sprints', 'history'),       'sprint-change', 'history');


  // ── Reports (top-level + marketing/output) ────────────────────────────
  watchDir(path.join(companyDir, 'reports'), 'report-change', 'reports',
           { scope: 'global' });
  watchDir(path.join(path.dirname(companyDir), 'marketing', 'output'), 'report-change',
           'marketing/output', { scope: 'marketing' });

  // ── Roster + org chart ────────────────────────────────────────────────
  watchFile(path.join(companyDir, 'roster.json'),    'roster-change', 'roster');
  watchFile(path.join(companyDir, 'org-chart.json'), 'roster-change', 'org-chart');

  // ── T3 (0.22): the Git view sees what the repo is doing ──────────────
  // The panel (showGit) renders the repo's ref state and the worktrees
  // (dirty / changedFiles per worktree). 0.21 refreshed it only from
  // ticket-change — and even that wiring was dead: the git view never
  // registered a refresher, so refreshActive('git') no-oped and an agent
  // commit left the worktree panel stale until a navigation. The panel
  // reads three places, all watched here:
  //   .git/HEAD          — branch moves (checkout / switch)
  //   .git/refs/heads/   — commit / branch updates (a commit rewrites the
  //                        checked-out branch's ref file)
  //   .worktrees/<TID>/  — file writes in a ticket worktree (the $PROJECT_
  //                        ROOT/.worktrees/$TID convention — no new layout).
  // All three fire the ONE git-change event under the SHARED emit key
  // 'git', so a burst (a commit + the index churn around it) coalesces
  // into one refresh at the 500 ms slow-class window — git state moves
  // slower than stream events (the fast class).
  {
    const root = path.dirname(companyDir);
    const gitDir = path.join(root, '.git');
    watchFile(path.join(gitDir, 'HEAD'), 'git-change', 'git');
    watchDir(path.join(gitDir, 'refs', 'heads'), 'git-change', 'git');
    const wtRoot = path.join(root, '.worktrees');
    if (fs.existsSync(wtRoot)) {
      const watchedWt = {};
      function watchWtSub(name) {
        if (!name || watchedWt[name]) return;
        const sub = path.join(wtRoot, name);
        try {
          if (!fs.statSync(sub).isDirectory()) return;
        } catch { return; }
        try {
          const w = fs.watch(sub, (ev, fn) => emit('git-change', 'git',
            { event: ev, file: 'worktree/' + name + '/' + (fn || ''),
              scope: 'worktree' }));
          fileWatchers.push(w);
          watchedWt[name] = w;
        } catch {}
      }
      for (const name of fs.readdirSync(wtRoot)) watchWtSub(name);
      try {
        // The parent watcher: a NEW worktree directory (tickets pick one
        // up as they go in-progress) attaches itself on creation, and the
        // creation is itself a git change for the panel.
        const w = fs.watch(wtRoot, (ev, fn) => {
          if (fn) watchWtSub(fn);
          emit('git-change', 'git', { event: ev, file: fn || '',
            scope: 'worktree' });
        });
        fileWatchers.push(w);
      } catch {}
    }
  }
}

// T2 (0.21): keepalive heartbeat — a byteless idle pipe is a dead pipe (proxies
// and browsers give up silently; EventSource fires onerror, not onopen).
const HB_SECS = Math.max(1, parseInt(process.env.HIVE_DASHBOARD_SSE_HB_SECS, 10) || 15);
let heartbeatTimer = null;
function startHeartbeat() {
  if (heartbeatTimer) return;
  heartbeatTimer = setInterval(() => {
    const line = ': hb ' + new Date().toISOString() + '\n\n';
    for (const res of sseClients) {
      try { res.write(line); } catch { sseClients.delete(res); }
    }
  }, HB_SECS * 1000);
  if (heartbeatTimer.unref) heartbeatTimer.unref();
}
module.exports = { sseClients, broadcastSSE, setupWatchers, startHeartbeat };
