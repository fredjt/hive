// SSE file-watcher: watches the live .company tree and pushes
// change events to every connected browser tab (G8: split out
// of server.js).
const fs = require('fs');
const path = require('path');

const sseClients = new Set();
let fileWatchers = [];

function broadcastSSE(event, data) {
  const msg = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const res of sseClients) {
    try { res.write(msg); } catch { sseClients.delete(res); }
  }
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
  watchDir(path.join(logDir, 'streams'),           'spawn-stream-change', 'streams');
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
