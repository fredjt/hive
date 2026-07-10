#!/usr/bin/env node
const http = require('http');
const fs = require('fs');
const path = require('path');
const { execSync, spawn } = require('child_process');

const args = process.argv.slice(2);
let companyDir = args.find((a, i) => args[i - 1] === '--dir') || null;
const port = parseInt(args.find((a, i) => args[i - 1] === '--port') || '3456');
const STATUSES = ['backlog', 'sprint-current', 'in-progress', 'in-review', 'done'];

// Server start time (Unix seconds) — used by the live indicator to show
// how long the Hive process has been running since the dashboard loaded.
const SERVER_STARTED_AT = Math.floor(Date.now() / 1000);

// ── HIVE MCP CLIENT ─────────────────────────────────────────────────────────
// All write operations route through the hive MCP server so the dashboard,
// the CLI, and Claude agents all share the same gating + audit logic.
// Reads continue to hit the filesystem directly (fast, low-overhead).
//
// MCP's Streamable HTTP transport is session-based: the first call must be
// "initialize", which returns an mcp-session-id header that subsequent calls
// must include. We initialize lazily and cache the session for the dashboard's
// lifetime; on any 4xx we re-initialize and retry once.
const MCP_URL = process.env.HIVE_MCP_URL || 'http://127.0.0.1:7891/mcp';
let mcpRpcId = 1;
let mcpSessionId = null;

// Parse a possibly-SSE response body into the JSON-RPC object.
async function _parseMcpResponse(response) {
  const text = await response.text();
  // streamable-http may send either application/json or text/event-stream
  if (text.startsWith('event:') || text.includes('\ndata: ')) {
    const dataLine = text.split('\n').find(l => l.startsWith('data: '));
    if (dataLine) return JSON.parse(dataLine.slice(6));
  }
  try { return JSON.parse(text); } catch { return null; }
}

async function _mcpInitialize() {
  const initPayload = {
    jsonrpc: '2.0', id: mcpRpcId++, method: 'initialize',
    params: {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'hive-dashboard', version: '0.1.0' },
    },
  };
  const r = await fetch(MCP_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json',
               'Accept': 'application/json, text/event-stream' },
    body: JSON.stringify(initPayload),
  });
  if (!r.ok) throw new Error('MCP init failed: HTTP ' + r.status);
  mcpSessionId = r.headers.get('mcp-session-id');
  await _parseMcpResponse(r);   // consume body
  // Send notifications/initialized
  await fetch(MCP_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json, text/event-stream',
      ...(mcpSessionId ? { 'mcp-session-id': mcpSessionId } : {}),
    },
    body: JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }),
  });
}

async function mcpCall(toolName, args = {}) {
  if (!mcpSessionId) {
    try { await _mcpInitialize(); }
    catch (e) { return { ok: false, error: 'MCP unreachable: ' + e.message }; }
  }
  const payload = {
    jsonrpc: '2.0', id: mcpRpcId++, method: 'tools/call',
    params: { name: toolName, arguments: args },
  };
  let response;
  try {
    response = await fetch(MCP_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
        ...(mcpSessionId ? { 'mcp-session-id': mcpSessionId } : {}),
      },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    return { ok: false, error: 'MCP unreachable: ' + e.message };
  }
  if (response.status === 404 || response.status === 400) {
    // Session expired or invalid - re-init once
    mcpSessionId = null;
    try { await _mcpInitialize(); } catch (e) { return { ok: false, error: e.message }; }
    return mcpCall(toolName, args);
  }
  if (!response.ok) {
    return { ok: false, error: 'MCP HTTP ' + response.status + ': ' + (await response.text()) };
  }
  const data = await _parseMcpResponse(response);
  if (!data) return { ok: false, error: 'unparseable MCP response' };
  if (data.error) return { ok: false, error: data.error };
  const content = (data.result || {}).content || [];
  if (content[0] && content[0].type === 'text') {
    try { return JSON.parse(content[0].text); }
    catch { return { ok: true, stdout: content[0].text }; }
  }
  return data.result || {};
}
// ────────────────────────────────────────────────────────────────────────────

// SSE: live updates
const sseClients = new Set();
let fileWatchers = [];

function broadcastSSE(event, data) {
  const msg = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const res of sseClients) {
    try { res.write(msg); } catch { sseClients.delete(res); }
  }
}

function setupWatchers() {
  // Clean up old watchers
  for (const w of fileWatchers) try { w.close(); } catch {}
  fileWatchers = [];
  if (!companyDir) return;

  // Pre-create the canonical subdirs we want to watch. fs.watch can only
  // attach to existing paths, and many of these (sprints/, logs/streams/,
  // meetings/, reports/) only get populated once daemons start. Creating
  // them empty here lets watchers attach now so the very first daemon
  // write fires an SSE event without needing a re-scan.
  for (const sub of ['sprints/history', 'logs/streams', 'meetings', 'reports']) {
    const p = path.join(companyDir, sub);
    try { fs.mkdirSync(p, { recursive: true }); } catch {}
  }

  // Per-event debounce windows. Most events collapse rapid bursts at 500 ms
  // (ticket moves, sprint state, roster edits — all human-paced writes where
  // sub-second redundancy is pure overhead). Stream and cycle log files are
  // different: they're append-only, written by claude / sprint-lifecycle at
  // many-events-per-second, and the client's cursor-based fetch with an
  // in-flight guard naturally serializes refreshes. A 500 ms debounce on
  // these channels swallows everything between the first and last event of
  // each burst (e.g., a tool_use + tool_result pair landing in the same
  // ~100 ms as the assistant message that triggered them) — the user only
  // sees the leading edge and assumes "only assistant events fire a refresh."
  // Drop their debounce to 50 ms to keep coalescing micro-bursts without
  // hiding intra-burst events.
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
        for (const status of [...STATUSES, 'archive']) {
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

  // ── Processes (active agent spawns + daemon registry) ─────────────────
  watchFile(path.join(companyDir, 'processes.json'), 'process-change', 'processes');

  // ── Sprint state ──────────────────────────────────────────────────────
  watchFile(path.join(companyDir, 'sprints', 'current.json'), 'sprint-change', 'current');
  watchDir(path.join(companyDir, 'sprints', 'history'),       'sprint-change', 'history');

  // ── Meetings (top dir + the schedule + nested subdirs) ────────────────
  const meetingDir = path.join(companyDir, 'meetings');
  if (fs.existsSync(meetingDir)) {
    watchDir(meetingDir, 'meeting-change', 'meetings');
    try {
      for (const sub of fs.readdirSync(meetingDir)) {
        const p = path.join(meetingDir, sub);
        if (fs.statSync(p).isDirectory()) {
          watchDir(p, 'meeting-change', 'meetings/' + sub);
        }
      }
    } catch {}
  }

  // ── Reports (top-level + marketing/output) ────────────────────────────
  watchDir(path.join(companyDir, 'reports'), 'report-change', 'reports',
           { scope: 'global' });
  watchDir(path.join(path.dirname(companyDir), 'marketing', 'output'), 'report-change',
           'marketing/output', { scope: 'marketing' });

  // ── Roster + org chart ────────────────────────────────────────────────
  watchFile(path.join(companyDir, 'roster.json'),    'roster-change', 'roster');
  watchFile(path.join(companyDir, 'org-chart.json'), 'roster-change', 'org-chart');
}

function jsonReply(res, data, st = 200) {
  res.writeHead(st, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
  res.end(JSON.stringify(data));
}

function readJson(fp) {
  try { return JSON.parse(fs.readFileSync(fp, 'utf8')); } catch { return null; }
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { reject(e); } });
  });
}

function companyPath() { return companyDir; }

function projectRoot() {
  if (!companyDir) return null;
  return path.dirname(companyDir);
}

function getDepartments() {
  const dir = companyPath();
  if (!dir) return [];
  const deptDir = path.join(dir, 'departments');
  if (!fs.existsSync(deptDir)) return [];
  // Only return departments that exist in org-chart.json (if available)
  const orgChart = readJson(path.join(dir, 'org-chart.json'));
  const validDepts = orgChart ? new Set(Object.keys(orgChart.departments || {})) : null;
  return fs.readdirSync(deptDir).filter(d => {
    const p = path.join(deptDir, d);
    if (!fs.statSync(p).isDirectory()) return false;
    // If we have an org chart, only show valid departments
    if (validDepts && !validDepts.has(d)) return false;
    return true;
  });
}

function deptHasTicketBoard(dept) {
  const dir = path.join(companyPath(), 'departments', dept, 'tickets');
  return fs.existsSync(dir);
}

function ensureTicketBoard(dept) {
  if (!dept || !String(dept).trim()) {
    // Refuse to seed a counter for an empty dept name. A blank dept here
    // produced counter.json with prefix:"" and tickets formatted as
    // "-0001", "-0002" with no department prefix. (Legal hit this in
    // 0.4.x.) Caller almost always has a real dept; if not, this is a bug
    // worth surfacing rather than silently scaffolding a broken board.
    throw new Error('ensureTicketBoard: dept is empty/blank — refusing to scaffold');
  }
  const base = path.join(companyPath(), 'departments', dept, 'tickets');
  for (const s of [...STATUSES, 'archive']) fs.mkdirSync(path.join(base, s), { recursive: true });
  fs.mkdirSync(path.join(base, 'sprints', 'history'), { recursive: true });
  const counterPath = path.join(base, 'counter.json');
  if (!fs.existsSync(counterPath)) {
    // Strip non-letters and uppercase: "backend-api" → "BACKENDAPI",
    // "rd" → "RD", "legal" → "LEGAL". Cap at 12 chars to keep IDs short.
    let prefix = String(dept).replace(/[^A-Za-z]/g, '').toUpperCase().slice(0, 12);
    if (!prefix) prefix = 'TICK';   // last-resort fallback for weird dept names
    fs.writeFileSync(counterPath, JSON.stringify({ next_id: 1, prefix }, null, 2));
  }
}

function getTicketsForDept(dept) {
  const tickets = [];
  for (const st of STATUSES) {
    const dir = path.join(companyPath(), 'departments', dept, 'tickets', st);
    if (!fs.existsSync(dir)) continue;
    for (const file of fs.readdirSync(dir).filter(f => f.endsWith('.json'))) {
      const ticket = readJson(path.join(dir, file));
      if (ticket) { ticket._status_dir = st; tickets.push(ticket); }
    }
  }
  return tickets;
}

function findTicketFile(dept, id) {
  for (const st of STATUSES) {
    const dir = path.join(companyPath(), 'departments', dept, 'tickets', st);
    if (!fs.existsSync(dir)) continue;
    for (const file of fs.readdirSync(dir).filter(f => f.endsWith('.json'))) {
      const ticket = readJson(path.join(dir, file));
      if (ticket && ticket.id === id) return { ticket, filePath: path.join(dir, file), status: st, fileName: file };
    }
  }
  return null;
}

function parseChatMessages(raw) {
  const messages = [];
  const blocks = raw.split(/\n---\n/).filter(b => b.trim());
  for (const block of blocks) {
    const match = block.match(/\*\*\[([^\]]*)\]\s*([^*]*)\*\*\s*([\s\S]*)/);
    if (match) {
      messages.push({ timestamp: match[1].trim(), author: match[2].trim(), body: match[3].trim() });
    } else {
      const lines = block.trim().split('\n').filter(l => !l.startsWith('#'));
      if (lines.length) messages.push({ timestamp: '', author: '', body: lines.join('\n').trim() });
    }
  }
  return messages;
}

function getCompanyInfo() {
  if (!companyPath()) return null;
  const manifest = readJson(path.join(companyPath(), 'manifest.json'));
  const orgChart = readJson(path.join(companyPath(), 'org-chart.json'));
  return { manifest, orgChart, path: companyPath() };
}

function safeReadFile(fp) {
  try { return fs.readFileSync(fp, 'utf8'); } catch { return null; }
}

function safeListDir(dir) {
  try { if (fs.existsSync(dir)) return fs.readdirSync(dir); } catch {}
  return [];
}

function isPidAlive(pid) {
  try { process.kill(pid, 0); return true; } catch { return false; }
}

async function handleApi(req, res) {
  const url = new URL(req.url, `http://localhost:${port}`);
  const parts = url.pathname.replace('/api/', '').split('/').filter(Boolean);

  // POST /api/connect - set company dir from UI
  if (req.method === 'POST' && parts[0] === 'connect') {
    const data = await readBody(req);
    const dir = data.path;
    if (!dir) return jsonReply(res, { error: 'path required' }, 400);
    const resolved = path.resolve(dir);
    const companySubdir = path.join(resolved, '.company');
    if (fs.existsSync(companySubdir)) {
      companyDir = companySubdir;
    } else if (fs.existsSync(resolved) && path.basename(resolved) === '.company') {
      companyDir = resolved;
    } else {
      return jsonReply(res, { error: `No .company directory found at ${resolved}` }, 404);
    }
    // Auto-create ticket boards for departments that don't have them
    for (const dept of getDepartments()) {
      if (!deptHasTicketBoard(dept)) ensureTicketBoard(dept);
    }
    setupWatchers();
    return jsonReply(res, { connected: true, path: companyDir, departments: getDepartments(),
      started_at: SERVER_STARTED_AT });
  }

  // GET /api/events - SSE endpoint
  if (req.method === 'GET' && parts[0] === 'events') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'Access-Control-Allow-Origin': '*'
    });
    sseClients.add(res);
    req.on('close', () => sseClients.delete(res));
    return;
  }

  // GET /api/status
  if (req.method === 'GET' && parts[0] === 'status') {
    if (!companyPath()) return jsonReply(res, { connected: false });
    return jsonReply(res, { connected: true, path: companyPath(), info: getCompanyInfo(),
      started_at: SERVER_STARTED_AT });
  }

  // Everything below requires a connected company dir
  if (!companyPath()) return jsonReply(res, { error: 'Not connected. POST /api/connect with {"path": "/your/project"}' }, 400);

  // GET /api/departments
  if (req.method === 'GET' && parts[0] === 'departments' && parts.length === 1) {
    const depts = getDepartments().map(d => {
      if (!deptHasTicketBoard(d)) ensureTicketBoard(d);
      const tickets = getTicketsForDept(d);
      const counts = {};
      for (const s of STATUSES) counts[s] = tickets.filter(t => t._status_dir === s).length;
      const counter = readJson(path.join(companyPath(), 'departments', d, 'tickets', 'counter.json'));
      return { name: d, prefix: counter?.prefix || d.toUpperCase().slice(0, 6), counts, total: tickets.length };
    });
    return jsonReply(res, depts);
  }

  // GET /api/departments/:dept/tickets
  if (req.method === 'GET' && parts[0] === 'departments' && parts[2] === 'tickets' && parts.length === 3) {
    return jsonReply(res, getTicketsForDept(parts[1]));
  }

  // GET /api/departments/:dept/tickets/:id
  if (req.method === 'GET' && parts[0] === 'departments' && parts[2] === 'tickets' && parts.length === 4) {
    const result = findTicketFile(parts[1], decodeURIComponent(parts[3]));
    if (!result) return jsonReply(res, { error: 'Ticket not found' }, 404);
    return jsonReply(res, result.ticket);
  }

  // POST /api/departments/:dept/tickets — routed through MCP ticket_create
  if (req.method === 'POST' && parts[0] === 'departments' && parts[2] === 'tickets' && parts.length === 3) {
    const data = await readBody(req);
    const dept = parts[1];
    const result = await mcpCall('ticket_create', {
      dept,
      type: data.type || 'feature',
      priority: (data.priority || 'medium').toUpperCase(),
      title: data.title || '',
      description: data.description || '',
      created_by: 'dashboard-user',
      source: 'user-ui',
      company_dir: companyPath(),
    });
    if (!result.ok) return jsonReply(res, { error: result.error || result.stderr || 'create failed' }, 500);
    return jsonReply(res, result, 201);
  }

  // PATCH /api/departments/:dept/tickets/:id — routed through MCP ticket_move/ticket_comment
  if (req.method === 'PATCH' && parts[0] === 'departments' && parts[2] === 'tickets' && parts.length === 4) {
    const updates = await readBody(req);
    const dept = parts[1];
    const ticketId = decodeURIComponent(parts[3]);

    // Comment first if requested (separate MCP call)
    if (updates.comment) {
      const cr = await mcpCall('ticket_comment', {
        dept, ticket_id: ticketId,
        author: updates.author || 'dashboard-user',
        comment: updates.comment,
        company_dir: companyPath(),
      });
      if (!cr.ok) return jsonReply(res, { error: cr.error || cr.stderr || 'comment failed' }, 500);
    }

    // Status change?
    if (updates.status) {
      const mr = await mcpCall('ticket_move', {
        dept, ticket_id: ticketId,
        new_status: updates.status,
        comment: updates.move_comment || `moved via dashboard`,
        author: updates.author || 'dashboard-user',
        reason: updates.reason || (updates.status === 'done' ? 'implemented' : undefined),
        force: !!updates.force,
        company_dir: companyPath(),
      });
      if (!mr.ok) return jsonReply(res, { error: mr.error || mr.stderr || 'move failed' }, 400);
      return jsonReply(res, mr);
    }

    // No status change, no comment - read back the ticket
    const result = findTicketFile(dept, ticketId);
    if (!result) return jsonReply(res, { error: 'Ticket not found' }, 404);
    return jsonReply(res, result.ticket);
  }

  // POST /api/departments/:dept/tickets/:id/approve — verification IC approval
  if (req.method === 'POST' && parts[0] === 'departments' && parts[2] === 'tickets' && parts[4] === 'approve' && parts.length === 5) {
    const body = await readBody(req);
    const result = await mcpCall('ticket_approve', {
      dept: parts[1],
      ticket_id: decodeURIComponent(parts[3]),
      verifier: body.verifier || 'dashboard-user',
      rationale: body.rationale || '',
      company_dir: companyPath(),
    });
    if (!result.ok) return jsonReply(res, { error: result.error || result.stderr || 'approve failed' }, 400);
    return jsonReply(res, result);
  }

  // POST /api/company/start — start all daemons.
  // Shells out to company-start.sh DIRECTLY (not via MCP). The MCP
  // company_start tool was removed because spawned agents kept invoking
  // it mid-cycle, restart-looping the whole company. Lifecycle is
  // human-only; the dashboard is a human surface, so it's allowed.
  if (req.method === 'POST' && parts[0] === 'company' && parts[1] === 'start') {
    if (!companyPath()) return jsonReply(res, { error: 'Not connected' }, 400);
    const projectRoot = path.dirname(companyPath());
    const installDir = process.env.HIVE_INSTALL_DIR || path.resolve(__dirname, '..', '..');
    // Per-project scaffold lives at <project>/.hive/. Fall back to install
    // dir for pre-scaffold projects so old layouts still work until they
    // run `hive scaffold extract`.
    const projectScaffold = path.join(projectRoot, '.hive');
    const hiveRoot = fs.existsSync(projectScaffold) ? projectScaffold : installDir;
    const logPath = path.join(companyPath(), 'logs', 'company-start.log');
    try { fs.mkdirSync(path.dirname(logPath), { recursive: true }); } catch {}
    const logFd = fs.openSync(logPath, 'a');
    try {
      const child = spawn('bash',
        [path.join(hiveRoot, 'lib', 'tools', 'company-start.sh'), projectRoot],
        { env: { ...process.env,
                 HIVE_ROOT: hiveRoot,
                 SKILL_DIR: hiveRoot,
                 HIVE_INSTALL_DIR: installDir },
          detached: true, stdio: ['ignore', logFd, logFd] });
      child.unref();
      return jsonReply(res, { ok: true, log: logPath, note: 'company-start.sh spawned in background' });
    } catch (e) {
      return jsonReply(res, { ok: false, error: String(e) }, 500);
    } finally {
      try { fs.closeSync(logFd); } catch {}
    }
  }
  // POST /api/company/stop — same: direct shell-out, not via MCP.
  if (req.method === 'POST' && parts[0] === 'company' && parts[1] === 'stop') {
    if (!companyPath()) return jsonReply(res, { error: 'Not connected' }, 400);
    const projectRoot = path.dirname(companyPath());
    const installDir = process.env.HIVE_INSTALL_DIR || path.resolve(__dirname, '..', '..');
    const projectScaffold = path.join(projectRoot, '.hive');
    const hiveRoot = fs.existsSync(projectScaffold) ? projectScaffold : installDir;
    try {
      execSync(`bash ${JSON.stringify(path.join(hiveRoot, 'lib', 'tools', 'company-stop.sh'))} ${JSON.stringify(projectRoot)}`,
        { env: { ...process.env,
                 HIVE_ROOT: hiveRoot,
                 SKILL_DIR: hiveRoot,
                 HIVE_INSTALL_DIR: installDir },
          timeout: 60000, stdio: ['ignore', 'pipe', 'pipe'] });
      return jsonReply(res, { ok: true });
    } catch (e) {
      return jsonReply(res, { ok: false, error: String(e), stderr: e.stderr ? e.stderr.toString() : '' }, 500);
    }
  }
  // POST /api/gates/:dept/run
  if (req.method === 'POST' && parts[0] === 'gates' && parts[2] === 'run' && parts.length === 3) {
    if (!companyPath()) return jsonReply(res, { error: 'Not connected' }, 400);
    const result = await mcpCall('gates_run', {
      dept: parts[1],
      project_root: path.dirname(companyPath()),
      company_dir: companyPath(),
    });
    return jsonReply(res, result, result.ok ? 200 : 500);
  }
  // GET /api/gates/:dept/:ticket_id
  if (req.method === 'GET' && parts[0] === 'gates' && parts.length === 3) {
    if (!companyPath()) return jsonReply(res, { error: 'Not connected' }, 400);
    const result = await mcpCall('gates_check', {
      dept: parts[1], ticket_id: decodeURIComponent(parts[2]), company_dir: companyPath(),
    });
    return jsonReply(res, result);
  }

  // GET /api/overview
  if (req.method === 'GET' && parts[0] === 'overview') {
    const depts = getDepartments();
    const overview = { departments: depts.length, tickets: {}, by_department: {} };
    for (const s of STATUSES) overview.tickets[s] = 0;
    overview.tickets.total = 0;
    for (const d of depts) {
      if (!deptHasTicketBoard(d)) ensureTicketBoard(d);
      const tickets = getTicketsForDept(d);
      const counts = {};
      for (const s of STATUSES) { counts[s] = tickets.filter(t => t._status_dir === s).length; overview.tickets[s] += counts[s]; }
      counts.total = tickets.length; overview.tickets.total += tickets.length;
      overview.by_department[d] = counts;
    }
    return jsonReply(res, overview);
  }

  // GET /api/chat/:channel
  if (req.method === 'GET' && parts[0] === 'chat' && parts.length === 2) {
    const chatFile = path.join(companyPath(), 'chat', parts[1] + '.md');
    if (!fs.existsSync(chatFile)) return jsonReply(res, { channel: parts[1], content: '', messages: [] });
    const raw = fs.readFileSync(chatFile, 'utf8');
    const messages = parseChatMessages(raw);
    return jsonReply(res, { channel: parts[1], content: raw, messages });
  }

  // GET /api/chat
  if (req.method === 'GET' && parts[0] === 'chat' && parts.length === 1) {
    const chatDir = path.join(companyPath(), 'chat');
    if (!fs.existsSync(chatDir)) return jsonReply(res, []);
    const channels = fs.readdirSync(chatDir).filter(f => f.endsWith('.md')).map(f => {
      const name = f.replace('.md', '');
      const content = fs.readFileSync(path.join(chatDir, f), 'utf8');
      const lines = content.split('\n').filter(l => l.trim()).length;
      const msgs = parseChatMessages(content);
      return { name, messageCount: msgs.length, lastMessage: msgs.length ? msgs[msgs.length - 1] : null };
    });
    return jsonReply(res, channels);
  }

  // GET /api/meetings
  if (req.method === 'GET' && parts[0] === 'meetings' && parts.length === 1) {
    const histDir = path.join(companyPath(), 'meetings', 'history');
    const schedule = readJson(path.join(companyPath(), 'meetings', 'schedule.json'));
    const meetings = [];
    if (fs.existsSync(histDir)) {
      for (const file of fs.readdirSync(histDir).filter(f => f.endsWith('.md')).sort().reverse()) {
        const content = fs.readFileSync(path.join(histDir, file), 'utf8');
        const title = content.split('\n').find(l => l.startsWith('#'))?.replace(/^#+\s*/, '') || file;
        const stat = fs.statSync(path.join(histDir, file));
        meetings.push({ file, title, date: file.replace(/\.md$/, ''), size: content.length, modified: stat.mtime.toISOString(), created: stat.birthtime.toISOString() });
      }
    }
    return jsonReply(res, { meetings, schedule });
  }

  // GET /api/meetings/:file
  if (req.method === 'GET' && parts[0] === 'meetings' && parts.length === 2) {
    const file = decodeURIComponent(parts[1]);
    const filePath = path.join(companyPath(), 'meetings', 'history', file.endsWith('.md') ? file : file + '.md');
    if (!fs.existsSync(filePath)) return jsonReply(res, { error: 'Meeting not found' }, 404);
    const content = fs.readFileSync(filePath, 'utf8');
    const title = content.split('\n').find(l => l.startsWith('#'))?.replace(/^#+\s*/, '') || file;
    const stat = fs.statSync(filePath);
    return jsonReply(res, { file, title, content, modified: stat.mtime.toISOString() });
  }

  // ===== NEW API ENDPOINTS =====

  // 1. GET /api/org-chart (merged with roster)
  if (req.method === 'GET' && parts[0] === 'org-chart' && parts.length === 1) {
    const orgChart = readJson(path.join(companyPath(), 'org-chart.json'));
    if (!orgChart) return jsonReply(res, { error: 'org-chart.json not found' }, 404);
    // Merge roster agents into org chart by department
    const roster = readJson(path.join(companyPath(), 'roster.json'));
    const agentList = roster ? (Array.isArray(roster.agents) ? roster.agents : (roster.agents ? Object.values(roster.agents) : [])) : [];
    orgChart._roster = {};
    for (const agent of agentList) {
      const dept = agent.department || 'unknown';
      if (!orgChart._roster[dept]) orgChart._roster[dept] = [];
      orgChart._roster[dept].push(agent);
    }
    return jsonReply(res, orgChart);
  }

  // 2. GET /api/roster
  if (req.method === 'GET' && parts[0] === 'roster' && parts.length === 1) {
    const roster = readJson(path.join(companyPath(), 'roster.json'));
    if (!roster) return jsonReply(res, { error: 'roster.json not found' }, 404);
    return jsonReply(res, roster);
  }

  // 2b. GET /api/agent-cycles
  if (req.method === 'GET' && parts[0] === 'agent-cycles' && parts.length === 1) {
    const cycles = readJson(path.join(companyPath(), 'logs', 'agent-cycles.json'));
    return jsonReply(res, cycles || {});
  }

  // 2d. GET /api/spawns/active — list of currently in-flight claude spawns.
  // Two sources merged:
  //   (a) Tagged: .company/logs/active/<spawn_id>.json, written by claude-spawn.sh.
  //       Has full identity (agent/dept/role/ticket) and a stream-file pointer.
  //   (b) Untagged: any `claude` process currently alive in /proc whose pid
  //       isn't already represented by a tagged record. Catches sub-agents
  //       spawned via Claude Code's built-in Agent tool or any other path
  //       that doesn't go through claude-spawn.sh.
  if (req.method === 'GET' && parts[0] === 'spawns' && parts[1] === 'active' && parts.length === 2) {
    const dir = path.join(companyPath(), 'logs', 'active');
    const out = [];
    const tagged_pids = new Set();
    try {
      for (const f of fs.readdirSync(dir)) {
        if (!f.endsWith('.json')) continue;
        try {
          const rec = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
          let alive = true;
          if (rec.pid) {
            try { process.kill(rec.pid, 0); } catch { alive = false; }
          }
          rec.alive = alive;
          rec.elapsed_s = Math.max(0, Math.floor(Date.now()/1000) - (rec.started_ts || 0));
          rec.source = 'tagged';
          if (rec.stream_file) {
            try {
              const stat = fs.statSync(rec.stream_file);
              rec.stream_bytes = stat.size;
              const data = fs.readFileSync(rec.stream_file, 'utf8');
              const lines = data.length ? data.split('\n').filter(Boolean) : [];
              rec.stream_lines = lines.length;
            } catch { rec.stream_bytes = 0; rec.stream_lines = 0; }
          }
          if (rec.pid) tagged_pids.add(rec.pid);
          // Also track child claude pids so we don't double-count
          out.push(rec);
        } catch {}
      }
    } catch {}

    // Walk /proc for claude binaries and add untagged ones
    try {
      const procEntries = fs.readdirSync('/proc').filter(d => /^\d+$/.test(d));
      for (const pidStr of procEntries) {
        const pid = parseInt(pidStr, 10);
        let comm = '';
        try { comm = fs.readFileSync('/proc/'+pid+'/comm', 'utf8').trim(); } catch { continue; }
        if (comm !== 'claude') continue;
        // Read its parent so we can reason about ownership
        let ppid = 0;
        try {
          const stat = fs.readFileSync('/proc/'+pid+'/stat', 'utf8');
          // Format: pid (comm) state ppid ...
          const m = stat.match(/\) [A-Z] (\d+)/);
          if (m) ppid = parseInt(m[1], 10);
        } catch {}
        // Skip if this pid (or its ancestor) is already accounted for by a tagged record
        let cur = pid, depth = 0, claimed = false;
        while (cur && depth < 12) {
          if (tagged_pids.has(cur)) { claimed = true; break; }
          try {
            const stat = fs.readFileSync('/proc/'+cur+'/stat', 'utf8');
            const m = stat.match(/\) [A-Z] (\d+)/);
            cur = m ? parseInt(m[1], 10) : 0;
          } catch { cur = 0; }
          depth++;
        }
        if (claimed) continue;
        // Read start time + cmdline for context
        let startTs = 0, elapsed_s = 0;
        try {
          const s = fs.statSync('/proc/'+pid);
          startTs = Math.floor(s.ctimeMs / 1000);
          elapsed_s = Math.max(0, Math.floor(Date.now()/1000) - startTs);
        } catch {}
        let cmdline = '';
        try { cmdline = fs.readFileSync('/proc/'+pid+'/cmdline', 'utf8').replace(/\0/g, ' ').trim().slice(0, 200); } catch {}
        out.push({
          spawn_id: 'proc-' + pid,
          agent: '(untagged)',
          dept: '?',
          role: '(claude proc)',
          ticket: null,
          pid: pid,
          ppid: ppid,
          alive: true,
          source: 'proc-scan',
          started_ts: startTs,
          started_at: new Date(startTs * 1000).toISOString(),
          elapsed_s: elapsed_s,
          cmdline: cmdline,
          stream_lines: 0,
        });
      }
    } catch {}

    out.sort((a, b) => (b.started_ts || 0) - (a.started_ts || 0));
    return jsonReply(res, { active: out });
  }

  // 2e. GET /api/spawns/cycles?limit=N — recent completed cycle history
  if (req.method === 'GET' && parts[0] === 'spawns' && parts[1] === 'cycles' && parts.length === 2) {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '100', 10) || 100, 500);
    const cyclesLog = path.join(companyPath(), 'logs', 'cycles.jsonl');
    const out = [];
    try {
      const lines = fs.readFileSync(cyclesLog, 'utf8').split('\n').filter(Boolean);
      for (const line of lines.slice(-limit)) {
        try { out.push(JSON.parse(line)); } catch {}
      }
    } catch {}
    return jsonReply(res, { cycles: out.reverse() });
  }

  // 2f. GET /api/spawns/:id/stream?from=N — return new stream-json events
  // from the given line offset. Client polls with the new "next" cursor each
  // time. Each event is an object emitted by `claude --output-format stream-json`.
  if (req.method === 'GET' && parts[0] === 'spawns' && parts[2] === 'stream' && parts.length === 3) {
    const id = parts[1];
    if (!/^[0-9A-Za-z\-]+$/.test(id)) return jsonReply(res, { error: 'invalid spawn id' }, 400);
    const url = new URL(req.url, `http://${req.headers.host}`);
    const from = Math.max(0, parseInt(url.searchParams.get('from') || '0', 10) || 0);
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '500', 10) || 500, 2000);
    const file = path.join(companyPath(), 'logs', 'streams', id + '.jsonl');
    const events = [];
    let totalLines = 0;
    try {
      const lines = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean);
      totalLines = lines.length;
      for (const line of lines.slice(from, from + limit)) {
        try {
          events.push(JSON.parse(line));
        } catch { events.push({ type: 'parse_error', raw: line.slice(0, 500) }); }
      }
    } catch (e) {
      return jsonReply(res, { events: [], next: from, total: 0, error: 'stream-not-found' });
    }
    return jsonReply(res, { events, next: from + events.length, total: totalLines });
  }

  // 2g. GET /api/spawns/global-stream?cursor=<base64-json>&limit=N
  // Returns a merged, time-ordered slice of events from every active spawn
  // beyond its per-spawn cursor. Cursor is a JSON object {spawn_id: next_line}
  // base64-encoded. Response includes the new cursor for the next poll.
  // Each emitted event is wrapped with its source spawn metadata so the
  // client can color/label by agent.
  if (req.method === 'GET' && parts[0] === 'spawns' && parts[1] === 'global-stream' && parts.length === 2) {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '300', 10) || 300, 2000);
    const cursorRaw = url.searchParams.get('cursor') || '';
    let cursor = {};
    if (cursorRaw) {
      try { cursor = JSON.parse(Buffer.from(cursorRaw, 'base64').toString('utf8')) || {}; }
      catch { cursor = {}; }
    }

    // Discover active spawns (read once from logs/active/)
    const activeDir = path.join(companyPath(), 'logs', 'active');
    const meta = {};   // spawn_id -> {agent, dept, role, ticket, started_ts}
    try {
      for (const f of fs.readdirSync(activeDir)) {
        if (!f.endsWith('.json')) continue;
        try {
          const rec = JSON.parse(fs.readFileSync(path.join(activeDir, f), 'utf8'));
          if (rec.spawn_id) {
            meta[rec.spawn_id] = {
              agent: rec.agent, dept: rec.dept, role: rec.role,
              ticket: rec.ticket, started_ts: rec.started_ts || 0,
            };
          }
        } catch {}
      }
    } catch {}

    // Also include spawns whose cursor was provided (in case they completed
    // mid-poll — we still want to surface their tail).
    for (const sid of Object.keys(cursor)) {
      if (!meta[sid]) meta[sid] = { agent: '?', dept: '?', role: '?', ticket: null, started_ts: 0 };
    }

    // Pull new events from each spawn
    const all = [];
    const newCursor = {};
    for (const sid of Object.keys(meta)) {
      const file = path.join(companyPath(), 'logs', 'streams', sid + '.jsonl');
      let lines = [];
      try { lines = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean); }
      catch { lines = []; }
      const start = cursor[sid] || 0;
      for (let i = start; i < lines.length; i++) {
        let ev;
        try { ev = JSON.parse(lines[i]); } catch { ev = { type: 'parse_error', raw: lines[i].slice(0, 500) }; }
        all.push({
          spawn_id: sid,
          line: i,
          agent: meta[sid].agent,
          dept: meta[sid].dept,
          role: meta[sid].role,
          ticket: meta[sid].ticket,
          event: ev,
        });
      }
      newCursor[sid] = lines.length;
    }

    // Sort by spawn started_ts then by line index — gives a roughly
    // chronological interleaving without relying on per-event timestamps
    // (which not every event has).
    all.sort((a, b) => {
      const ta = (meta[a.spawn_id] && meta[a.spawn_id].started_ts) || 0;
      const tb = (meta[b.spawn_id] && meta[b.spawn_id].started_ts) || 0;
      if (ta !== tb) return ta - tb;
      return a.line - b.line;
    });

    const sliced = all.slice(-limit); // keep tail
    const cursorOut = Buffer.from(JSON.stringify(newCursor)).toString('base64');
    return jsonReply(res, {
      events: sliced,
      cursor: cursorOut,
      total: all.length,
      active_count: Object.keys(meta).length,
    });
  }

  // 2h. GET /api/rounds/current — read .company/rounds/current-round.json.
  // Decorates the response with the active sprint stage (so the dashboard
  // can show "tdd-green", "verify", etc.) and, if the current round is an
  // empty shell (round-promote built it but sprint-lifecycle hasn't
  // enqueued anything yet), surfaces the most recent archived round under
  // `previous_round` so the panel doesn't appear to wipe between rounds.
  if (req.method === 'GET' && parts[0] === 'rounds' && parts[1] === 'current' && parts.length === 2) {
    const f = path.join(companyPath(), 'rounds', 'current-round.json');
    let current;
    try { current = JSON.parse(fs.readFileSync(f, 'utf8')); }
    catch { current = { error: 'no current round', round_id: null, entries: [], cursor: 0 }; }

    // Attach sprint stage info (best-effort).
    try {
      const sf = path.join(companyPath(), 'sprints', 'current.json');
      const sd = JSON.parse(fs.readFileSync(sf, 'utf8'));
      current.sprint_id = sd.sprint_id;
      current.stage = sd.stage;
      current.stage_started_at = sd.stage_started_at;
      current.loop_iteration = sd.loop_iteration;
    } catch {}

    // If the current round has no entries, attach the most recent archived
    // round. The dashboard renders that one until the new round fills up.
    const noEntries = !current.entries || current.entries.length === 0;
    if (noEntries) {
      try {
        const histDir = path.join(companyPath(), 'rounds', 'history');
        const files = fs.readdirSync(histDir).filter(x => x.endsWith('.json')).sort();
        if (files.length) {
          const last = files[files.length - 1];
          current.previous_round = JSON.parse(fs.readFileSync(path.join(histDir, last), 'utf8'));
        }
      } catch {}
    }

    return jsonReply(res, current);
  }

  // 2i. GET /api/rounds/next — read .company/rounds/next-plan.json
  if (req.method === 'GET' && parts[0] === 'rounds' && parts[1] === 'next' && parts.length === 2) {
    const f = path.join(companyPath(), 'rounds', 'next-plan.json');
    try {
      return jsonReply(res, JSON.parse(fs.readFileSync(f, 'utf8')));
    } catch {
      return jsonReply(res, { error: 'no pending plan', entries: [] });
    }
  }

  // 2j. GET /api/rounds/history?limit=N — summarized historical rounds
  if (req.method === 'GET' && parts[0] === 'rounds' && parts[1] === 'history' && parts.length === 2) {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '20', 10) || 20, 200);
    const histDir = path.join(companyPath(), 'rounds', 'history');
    const out = [];
    try {
      const files = fs.readdirSync(histDir).filter(f => f.endsWith('.json')).sort();
      for (const f of files.slice(-limit).reverse()) {
        try {
          const d = JSON.parse(fs.readFileSync(path.join(histDir, f), 'utf8'));
          const entries = d.entries || [];
          const counts = {};
          for (const e of entries) counts[e.status || '?'] = (counts[e.status || '?'] || 0) + 1;
          let dur = null;
          if (d.started_at && d.ended_at) {
            try { dur = Math.floor((new Date(d.ended_at) - new Date(d.started_at)) / 1000); } catch {}
          }
          out.push({
            round_id: d.round_id,
            source: d.source,
            started_at: d.started_at,
            ended_at: d.ended_at,
            duration_s: dur,
            entry_count: entries.length,
            status_counts: counts,
          });
        } catch {}
      }
    } catch {}
    return jsonReply(res, { rounds: out });
  }

  // 2k. GET /api/rounds/:round_id — full historical round JSON
  if (req.method === 'GET' && parts[0] === 'rounds' && parts.length === 2 &&
      parts[1] !== 'current' && parts[1] !== 'next' && parts[1] !== 'history') {
    const rid = parts[1];
    if (!/^[A-Za-z0-9\-_T]+$/.test(rid)) return jsonReply(res, { error: 'invalid round id' }, 400);
    const f = path.join(companyPath(), 'rounds', 'history', rid + '.json');
    try {
      return jsonReply(res, JSON.parse(fs.readFileSync(f, 'utf8')));
    } catch {
      return jsonReply(res, { error: 'round not found' }, 404);
    }
  }

  // 2l. GET /api/verification/<TID>/stream?from=N&limit=M — read a
  // verification log file (.company/logs/verification-<TID>.log), parse each
  // line as JSON, return events with cursor-based pagination.  This powers
  // the "Watch Verification" live output panel.
  if (req.method === 'GET' && parts[0] === 'verification' && parts[2] === 'stream' && parts.length === 3) {
    const tid = parts[1];
    if (!/^[A-Za-z0-9\-_]+$/.test(tid)) return jsonReply(res, { error: 'invalid ticket id' }, 400);
    const url = new URL(req.url, `http://${req.headers.host}`);
    const from = Math.max(0, parseInt(url.searchParams.get('from') || '0', 10) || 0);
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '500', 10) || 500, 2000);
    const file = path.join(companyPath(), 'logs', 'verification-' + tid + '.log');
    const lines = [];
    let totalLines = 0;
    try {
      const raw = fs.readFileSync(file, 'utf8');
      const all = raw.split('\n').filter(Boolean);
      totalLines = all.length;
      for (const line of all.slice(from, from + limit)) {
        try {
          lines.push({ type: 'raw', text: line });
        } catch {
          lines.push({ type: 'raw', text: line.slice(0, 500) });
        }
      }
    } catch (e) {
      return jsonReply(res, { events: [], next: from, total: 0, error: 'log-not-found' });
    }
    return jsonReply(res, { events: lines, next: from + lines.length, total: totalLines });
  }

  // 2m. GET /api/gh/mirror — read .company/integrations/github-mirror.json,
  // refreshing PR state from GitHub on demand. The mirror sidecar is only
  // written by gh-pr-open.sh and gh-pr-merge.sh, so anything that happens
  // to a PR outside those paths (merged via the GH web UI, closed manually,
  // merged with `gh pr merge` direct) leaves it stuck at "open" in the
  // sidecar forever. Refresh from `gh pr list` + `gh issue list` here so
  // the dashboard always shows current truth, throttled to once per 60s.
  if (req.method === 'GET' && parts[0] === 'gh' && parts[1] === 'mirror' && parts.length === 2) {
    const mirrorFile = path.join(companyPath(), 'integrations', 'github-mirror.json');
    let mirror;
    try { mirror = JSON.parse(fs.readFileSync(mirrorFile, 'utf8')); }
    catch { mirror = { issues: {}, prs: {} }; }

    // Cache: skip refresh if last sync < 60s ago (avoid hammering gh on every refresh)
    const SYNC_TTL_MS = 60 * 1000;
    const lastSync = Number(mirror._last_gh_sync_ms || 0);
    if (Date.now() - lastSync > SYNC_TTL_MS) {
      try {
        // PRs first — open + recently-closed in one call
        const prRaw = require('child_process').execSync(
          'gh pr list --state all --limit 100 --json number,state,mergedAt,closedAt,headRefName,title',
          { cwd: companyPath().replace(/\.company$/, ''), encoding: 'utf8', timeout: 8000, stdio: ['ignore','pipe','ignore'] }
        );
        const prList = JSON.parse(prRaw);
        mirror.prs = mirror.prs || {};
        for (const pr of prList) {
          const k = String(pr.number);
          const existing = mirror.prs[k] || {};
          mirror.prs[k] = {
            ...existing,
            number:    pr.number,
            branch:    existing.branch || pr.headRefName,
            title:     pr.title || existing.title,
            state:     (pr.state || '').toLowerCase(),         // OPEN | CLOSED | MERGED
            merged_at: pr.mergedAt || existing.merged_at || null,
            closed_at: pr.closedAt || existing.closed_at || null,
            opened_at: existing.opened_at || null,
          };
        }
        // Issues — only those we already mirror (we don't want every random
        // GH issue, just refreshed state of the ones the poller created tickets for).
        const issueRaw = require('child_process').execSync(
          'gh issue list --state all --limit 200 --json number,state',
          { cwd: companyPath().replace(/\.company$/, ''), encoding: 'utf8', timeout: 8000, stdio: ['ignore','pipe','ignore'] }
        );
        const issueList = JSON.parse(issueRaw);
        const seenIssues = {};
        for (const it of issueList) seenIssues[String(it.number)] = (it.state || '').toLowerCase();
        mirror.issues = mirror.issues || {};
        for (const k of Object.keys(mirror.issues)) {
          if (seenIssues[k]) {
            mirror.issues[k].last_seen_state = seenIssues[k];
            mirror.issues[k].last_synced_at = new Date().toISOString();
          }
        }
        mirror._last_gh_sync_ms = Date.now();
        try {
          fs.mkdirSync(path.dirname(mirrorFile), { recursive: true });
          fs.writeFileSync(mirrorFile, JSON.stringify(mirror, null, 2));
        } catch {}
      } catch (e) {
        // gh not authed / no remote / network down — fall back to whatever we
        // have on disk. Add a marker so the UI can warn.
        mirror._sync_error = String((e && e.message) || e).slice(0, 200);
      }
    }
    return jsonReply(res, mirror);
  }

  // 2l-sprint. GET /api/sprints/current — sprint-staged lifecycle status.
  // Single source of truth for current sprint id, stage, loop iteration,
  // release retry count, ticket counts, in-flight release block.
  if (req.method === 'GET' && parts[0] === 'sprints' && parts[1] === 'current' && parts.length === 2) {
    const f = path.join(companyPath(), 'sprints', 'current.json');
    try {
      const d = JSON.parse(fs.readFileSync(f, 'utf8'));
      // Compute live ticket counts so the dashboard doesn't trust the
      // tickets_in_sprint snapshot (which is set once at sprint-fill).
      const counts = { sprint_current: 0, in_progress: 0, in_review: 0, done: 0 };
      for (const dept of getDepartments()) {
        for (const k of ['sprint-current', 'in-progress', 'in-review', 'done']) {
          const dir = path.join(companyPath(), 'departments', dept, 'tickets', k);
          if (!fs.existsSync(dir)) continue;
          const n = fs.readdirSync(dir).filter(x => x.endsWith('.json')).length;
          counts[k.replace('-', '_')] += n;
        }
      }
      d.live_ticket_counts = counts;
      return jsonReply(res, d);
    } catch {
      return jsonReply(res, { error: 'no sprint in progress (sprints/current.json missing)' });
    }
  }

  // 2l-sprint. GET /api/sprints/history?limit=N
  if (req.method === 'GET' && parts[0] === 'sprints' && parts[1] === 'history' && parts.length === 2) {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '20', 10) || 20, 200);
    const dir = path.join(companyPath(), 'sprints', 'history');
    const out = [];
    try {
      const files = fs.readdirSync(dir)
        .filter(n => n.endsWith('.json'))
        .map(n => ({ n, m: fs.statSync(path.join(dir, n)).mtimeMs }))
        .sort((a, b) => b.m - a.m).slice(0, limit);
      for (const { n } of files) {
        try {
          const d = JSON.parse(fs.readFileSync(path.join(dir, n), 'utf8'));
          out.push({
            sprint_id: d.sprint_id,
            started_at: d.started_at,
            ended_at: d.ended_at,
            release_version: (d.release || {}).version,
            release_status: (d.release || {}).publish_status,
          });
        } catch {}
      }
    } catch {}
    return jsonReply(res, { sprints: out });
  }

  // 2m. GET /api/releases/current — sprint-staged lifecycle: returns sprint
  // state's release block. Falls back to the most recent published sprint.
  if (req.method === 'GET' && parts[0] === 'releases' && parts[1] === 'current' && parts.length === 2) {
    const cur = path.join(companyPath(), 'sprints', 'current.json');
    try {
      const d = JSON.parse(fs.readFileSync(cur, 'utf8'));
      return jsonReply(res, { sprint_id: d.sprint_id, stage: d.stage,
                              release_retry_count: d.release_retry_count,
                              release: d.release || {} });
    } catch {}
    // Fall back to most-recent historical sprint with a release.version
    try {
      const dir = path.join(companyPath(), 'sprints', 'history');
      const files = fs.readdirSync(dir)
        .filter(n => n.endsWith('.json'))
        .map(n => ({ n, m: fs.statSync(path.join(dir, n)).mtimeMs }))
        .sort((a, b) => b.m - a.m);
      for (const { n } of files) {
        const d = JSON.parse(fs.readFileSync(path.join(dir, n), 'utf8'));
        if ((d.release || {}).version) {
          return jsonReply(res, { sprint_id: d.sprint_id, ended_at: d.ended_at,
                                  release: d.release });
        }
      }
    } catch {}
    return jsonReply(res, { error: 'no releases yet' });
  }

  // 2n. GET /api/releases/history?limit=N — historical releases. Truth comes
  // from TWO sources, merged on version tag:
  //   (a) sprint-staged archive: .company/sprints/history/*.json with a
  //       release.version set. Carries publish_status, tarball path, sprint id.
  //   (b) `gh release list` — the actual published GitHub releases. Catches
  //       releases the sprint archive missed (loop-cap fired before publish
  //       wrote back, sprint cancelled, manual release, etc.).
  // Sources merged so the panel never lies about which versions actually shipped.
  if (req.method === 'GET' && parts[0] === 'releases' && parts[1] === 'history' && parts.length === 2) {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '20', 10) || 20, 200);
    const byVersion = new Map();
    const norm = (v) => String(v || '').replace(/^v/i, '');

    // (a) Sprint archive
    try {
      const dir = path.join(companyPath(), 'sprints', 'history');
      const files = fs.readdirSync(dir)
        .filter(n => n.endsWith('.json'))
        .map(n => ({ n, m: fs.statSync(path.join(dir, n)).mtimeMs }))
        .sort((a, b) => b.m - a.m).slice(0, limit * 2);
      for (const { n } of files) {
        try {
          const d = JSON.parse(fs.readFileSync(path.join(dir, n), 'utf8'));
          const rel = d.release || {};
          if (!rel.version) continue;
          const k = norm(rel.version);
          byVersion.set(k, {
            sprint_id: d.sprint_id,
            version:   rel.version,
            status:    rel.publish_status || 'unknown',
            tarball:   rel.tarball || null,
            ended:     d.ended_at,
            source:    'sprint-archive',
          });
        } catch {}
      }
    } catch {}

    // (b) GitHub release list — augment / overwrite with truth from GH
    try {
      const ghRaw = require('child_process').execSync(
        `gh release list --limit ${limit * 2} --json tagName,name,publishedAt,isDraft,isPrerelease`,
        { cwd: companyPath().replace(/\.company$/, ''), encoding: 'utf8', timeout: 8000, stdio: ['ignore','pipe','ignore'] }
      );
      const ghList = JSON.parse(ghRaw);
      for (const r of ghList) {
        const k = norm(r.tagName || r.name);
        const existing = byVersion.get(k) || {};
        byVersion.set(k, {
          ...existing,
          version:  existing.version || r.tagName || r.name,
          status:   r.isDraft ? 'draft' : (r.isPrerelease ? 'prerelease' : 'published'),
          ended:    existing.ended || r.publishedAt,
          gh_published_at: r.publishedAt,
          source:   existing.source ? existing.source + '+gh' : 'gh',
        });
      }
    } catch (e) {
      // gh not authed / no remote — silent; sprint-archive entries still ship.
    }

    const out = Array.from(byVersion.values())
      .sort((a, b) => String(b.gh_published_at || b.ended || '').localeCompare(String(a.gh_published_at || a.ended || '')))
      .slice(0, limit);
    return jsonReply(res, { releases: out });
  }

  // 2o. GET /api/releases/:version — full release record
  if (req.method === 'GET' && parts[0] === 'releases' && parts.length === 2 &&
      parts[1] !== 'current' && parts[1] !== 'history') {
    const v = parts[1];
    if (!/^v?[0-9A-Za-z\.\-_]+$/.test(v)) return jsonReply(res, { error: 'invalid version' }, 400);
    const file = path.join(companyPath(), 'releases', (v.startsWith('v') ? v : 'v' + v) + '.json');
    try { return jsonReply(res, JSON.parse(fs.readFileSync(file, 'utf8'))); }
    catch { return jsonReply(res, { error: 'release not found' }, 404); }
  }

  // 2c. GET /api/ticket-types - all unique types used across all tickets
  if (req.method === 'GET' && parts[0] === 'ticket-types' && parts.length === 1) {
    const types = new Set(['feature', 'bug', 'improvement', 'chore', 'infrastructure', 'research']);
    for (const dept of getDepartments()) {
      for (const status of STATUSES) {
        const dir = path.join(companyPath(), 'departments', dept, 'tickets', status);
        if (!fs.existsSync(dir)) continue;
        for (const file of fs.readdirSync(dir).filter(f => f.endsWith('.json'))) {
          try {
            const t = readJson(path.join(dir, file));
            if (t && t.type) types.add(t.type);
          } catch {}
        }
      }
    }
    return jsonReply(res, Array.from(types).sort());
  }

  // 3. GET /api/sprint
  if (req.method === 'GET' && parts[0] === 'sprint' && parts.length === 1) {
    const depts = getDepartments();
    const sprintData = { departments: {}, totals: { backlog: 0, 'sprint-current': 0, 'in-progress': 0, 'in-review': 0, done: 0, total: 0 } };
    for (const dept of depts) {
      const sprintFile = path.join(companyPath(), 'departments', dept, 'tickets', 'sprints', 'current.json');
      const sprintInfo = readJson(sprintFile);
      const tickets = getTicketsForDept(dept);
      const counts = {};
      for (const s of STATUSES) {
        counts[s] = tickets.filter(t => t._status_dir === s).length;
        sprintData.totals[s] += counts[s];
      }
      counts.total = tickets.length;
      sprintData.totals.total += tickets.length;
      sprintData.departments[dept] = { counts, sprint: sprintInfo };
    }
    return jsonReply(res, sprintData);
  }

  // 4. GET /api/reports — aggregate every report-style document the company
  // produces into one feed. Replaces the old per-dept Research panel and the
  // standalone Marketing panel; everyone's reports show up here now.
  //
  // Sources:
  //   .company/research/ongoing/*.md           (R&D findings)
  //   .company/marketing/reports/*.md          (marketing reports)
  //   .company/marketing/output/*.md           (marketing deliverables)
  //   .company/departments/<dept>/reports/**.md (per-dept reports if any)
  //   .company/departments/<dept>/*.md         (e.g. hr/productivity-report.md)
  //   .company/meetings/history/*.md           (meeting notes)
  //
  // Each entry carries structured metadata (dept, type, date, author) parsed
  // from the front matter when present, otherwise inferred from the path +
  // file mtime.
  if (req.method === 'GET' && parts[0] === 'reports' && parts.length === 1) {
    const reports = [];
    const seen = new Set();
    const parseFront = (text) => {
      // Lightweight front-matter parser: looks for top-of-file lines like
      //   author: foo
      //   date: 2026-05-06
      //   dept: backend-api
      // (with or without a leading "---" fence). Doesn't try to be full YAML.
      const meta = {};
      const lines = String(text || '').split(/\r?\n/);
      let start = 0;
      if (lines[0] && lines[0].trim() === '---') {
        for (let j = 1; j < Math.min(lines.length, 25); j++) {
          if (lines[j].trim() === '---') { start = j + 1; break; }
          const m = /^([A-Za-z_][\w-]*)\s*:\s*(.+?)\s*$/.exec(lines[j]);
          if (m) meta[m[1].toLowerCase()] = m[2];
        }
      } else {
        for (let j = 0; j < Math.min(lines.length, 8); j++) {
          const m = /^([A-Za-z_][\w-]*)\s*:\s*(.+?)\s*$/.exec(lines[j]);
          if (m) meta[m[1].toLowerCase()] = m[2];
          else break;
        }
      }
      // Title from first H1 if present
      for (const l of lines) {
        const m = /^#\s+(.+?)\s*$/.exec(l);
        if (m) { meta.__title = m[1]; break; }
      }
      return meta;
    };
    const titleFromFile = (file) => file.replace(/\.[^.]+$/, '').replace(/[-_]/g, ' ');
    const addReport = (fp, dept, type) => {
      try {
        if (seen.has(fp)) return;
        const stat = fs.statSync(fp);
        if (!stat.isFile()) return;
        if (!/\.(md|markdown|txt)$/i.test(fp)) return;
        seen.add(fp);
        const content = fs.readFileSync(fp, 'utf8');
        const front = parseFront(content);
        reports.push({
          file: path.basename(fp),
          path: path.relative(companyPath(), fp),
          dept: front.dept || front.department || dept || '',
          type: front.type || type || 'report',
          author: front.author || front.by || front['written-by'] || '',
          date: front.date || stat.mtime.toISOString(),
          mtime: stat.mtime.toISOString(),
          title: front.__title || front.title || titleFromFile(path.basename(fp)),
          size: stat.size,
          content,
        });
      } catch {}
    };
    const scanDir = (dir, dept, type, opts) => {
      opts = opts || {};
      try {
        for (const f of fs.readdirSync(dir)) {
          const fp = path.join(dir, f);
          let st;
          try { st = fs.statSync(fp); } catch { continue; }
          if (st.isFile()) addReport(fp, dept, type);
          else if (st.isDirectory() && opts.recurse) scanDir(fp, dept, type, opts);
        }
      } catch {}
    };

    scanDir(path.join(companyPath(), 'research', 'ongoing'), '', 'research');
    scanDir(path.join(companyPath(), 'marketing', 'reports'), 'marketing', 'marketing-report');
    scanDir(path.join(companyPath(), 'marketing', 'output'), 'marketing', 'marketing-output');
    scanDir(path.join(companyPath(), 'meetings', 'history'), '', 'meeting');
    scanDir(path.join(companyPath(), 'meetings', 'sprint-reviews'), '', 'sprint-review');

    // Per-dept: <dept>/reports/** plus any *.md sitting at the dept root
    // (e.g. hr/productivity-report.md, finance/budget-summary.md).
    try {
      const deptsDir = path.join(companyPath(), 'departments');
      for (const dept of fs.readdirSync(deptsDir)) {
        const droot = path.join(deptsDir, dept);
        let dst;
        try { dst = fs.statSync(droot); } catch { continue; }
        if (!dst.isDirectory()) continue;
        scanDir(path.join(droot, 'reports'), dept, 'dept-report', { recurse: true });
        // top-level *.md only (skip tickets/, components/, etc.)
        try {
          for (const f of fs.readdirSync(droot)) {
            if (!/\.(md|markdown|txt)$/i.test(f)) continue;
            addReport(path.join(droot, f), dept, 'dept-report');
          }
        } catch {}
      }
    } catch {}

    reports.sort((a, b) => (b.date || '').localeCompare(a.date || ''));
    const recommendations = safeReadFile(path.join(companyPath(), 'research', 'recommendations.md'));
    return jsonReply(res, { reports, recommendations });
  }
  // Legacy alias — old clients that still hit /api/research get the
  // (narrower) research-only feed in the original shape.
  if (req.method === 'GET' && parts[0] === 'research' && parts.length === 1) {
    const ongoingDir = path.join(companyPath(), 'research', 'ongoing');
    const findings = [];
    for (const file of safeListDir(ongoingDir)) {
      try {
        const fp = path.join(ongoingDir, file);
        const stat = fs.statSync(fp);
        if (stat.isFile()) findings.push({ file, content: fs.readFileSync(fp, 'utf8'), date: stat.mtime.toISOString() });
      } catch {}
    }
    findings.sort((a, b) => b.date.localeCompare(a.date));
    return jsonReply(res, { findings, recommendations: safeReadFile(path.join(companyPath(), 'research', 'recommendations.md')) });
  }

  // 5. GET /api/logs and GET /api/logs/:name
  if (req.method === 'GET' && parts[0] === 'logs') {
    const logsDir = path.join(companyPath(), 'logs');
    if (parts.length === 1) {
      const files = safeListDir(logsDir);
      const logFiles = [];
      for (const file of files) {
        try {
          const fp = path.join(logsDir, file);
          const stat = fs.statSync(fp);
          if (stat.isFile()) {
            logFiles.push({ file, size: stat.size, modified: stat.mtime.toISOString() });
          }
        } catch {}
      }
      logFiles.sort((a, b) => b.modified.localeCompare(a.modified));
      return jsonReply(res, logFiles);
    }
    if (parts.length === 2) {
      const name = decodeURIComponent(parts[1]);
      if (name.includes('..') || name.includes('/') || name.includes('\\')) return jsonReply(res, { error: 'Invalid log file name' }, 400);
      const fp = path.join(logsDir, name);
      if (!fs.existsSync(fp)) return jsonReply(res, { error: 'Log file not found' }, 404);
      const content = fs.readFileSync(fp, 'utf8');
      const lines = content.split('\n');
      const tail = lines.slice(-200).join('\n');
      return jsonReply(res, { file: name, content: tail, totalLines: lines.length });
    }
  }

  // 6. GET /api/departments/:dept/plan and /api/departments/:dept/contracts
  if (req.method === 'GET' && parts[0] === 'departments' && parts.length >= 3) {
    const dept = parts[1];
    // GET /api/departments/:dept/plan
    //
    // For static admin departments that maintain real artifact directories at
    // <project_root>/<dept>/, we serve those instead of the in-.company plan.md.
    // For dynamic engineering departments, we serve the .company/.../plan.md
    // (which the dept's manager writes during the planning phase to coordinate ICs).
    if (parts[2] === 'plan' && parts.length === 3) {
      const projectRoot = path.dirname(companyPath());
      // Mapping: which docs to surface for static admin depts.
      // Each entry is a list of candidate files; first one that exists wins.
      const ARTIFACT_MAP = {
        marketing: ['marketing/strategy.md', 'marketing/gtm-plan.md'],
        legal:     ['legal/risk-register.md', 'legal/privacy.md'],
        finance:   ['finance/budget.md', 'finance/runway.json'],
        rd:        ['research/recommendations.md'],
      };
      const docs = [];
      // Always look for .company/.../plan.md first
      const planPath = path.join(companyPath(), 'departments', dept, 'plan.md');
      const planContent = safeReadFile(planPath);
      if (planContent !== null) {
        docs.push({ path: planPath, source: 'company', content: planContent });
      }
      // For static admin depts with real artifact dirs, also surface those
      const candidates = ARTIFACT_MAP[dept] || [];
      for (const rel of candidates) {
        const fp = path.join(projectRoot, rel);
        const c = safeReadFile(fp);
        if (c !== null) {
          docs.push({ path: fp, source: 'project', relpath: rel, content: c });
        }
      }
      if (docs.length === 0) {
        return jsonReply(res, {
          department: dept,
          docs: [],
          message: 'No plan or strategy doc found for this department. Engineering depts: VP/manager writes ' +
                   companyPath() + '/departments/' + dept + '/plan.md. Static admin depts (marketing/legal/finance) ' +
                   'write strategy artifacts at <project>/' + dept + '/.',
        }, 404);
      }
      // Backward compat: if a single doc was found and it came from .company,
      // also include the legacy `content` key (some old clients used it).
      const out = { department: dept, docs };
      if (docs.length === 1) out.content = docs[0].content;
      return jsonReply(res, out);
    }
    // GET /api/departments/:dept/contracts
    if (parts[2] === 'contracts' && parts.length === 3) {
      const contractsDir = path.join(companyPath(), 'departments', dept, 'contracts');
      const files = safeListDir(contractsDir);
      const contracts = [];
      for (const file of files) {
        try {
          const fp = path.join(contractsDir, file);
          const stat = fs.statSync(fp);
          if (stat.isFile()) {
            contracts.push({ file, size: stat.size, modified: stat.mtime.toISOString() });
          }
        } catch {}
      }
      return jsonReply(res, { department: dept, contracts });
    }
    // GET /api/departments/:dept/contracts/:name
    if (parts[2] === 'contracts' && parts.length === 4) {
      const name = decodeURIComponent(parts[3]);
      const fp = path.join(companyPath(), 'departments', dept, 'contracts', name);
      const content = safeReadFile(fp);
      if (content === null) return jsonReply(res, { error: 'Contract not found' }, 404);
      return jsonReply(res, { department: dept, file: name, content });
    }
  }

  // 7. GET /api/processes
  if (req.method === 'GET' && parts[0] === 'processes' && parts.length === 1) {
    const processes = readJson(path.join(companyPath(), 'processes.json'));
    if (!processes) return jsonReply(res, { error: 'processes.json not found', processes: [] });
    const result = [];
    const procs = Array.isArray(processes) ? processes : (processes.processes || processes.agents || []);
    for (const proc of procs) {
      const pid = proc.pid || proc.PID;
      let alive = false;
      if (pid) {
        alive = isPidAlive(parseInt(pid));
      }
      result.push({ ...proc, alive });
    }
    return jsonReply(res, { processes: result });
  }

  // 8. GET /api/inbox and GET /api/outbox
  if (req.method === 'GET' && parts[0] === 'inbox' && parts.length === 1) {
    const userRequests = safeReadFile(path.join(companyPath(), 'inbox', 'user-requests.md'));
    const ticketReqDir = path.join(companyPath(), 'inbox', 'ticket-requests');
    const ticketFiles = safeListDir(ticketReqDir);
    const ticketRequests = [];
    for (const file of ticketFiles) {
      try {
        const fp = path.join(ticketReqDir, file);
        const stat = fs.statSync(fp);
        if (stat.isFile()) {
          const content = fs.readFileSync(fp, 'utf8');
          ticketRequests.push({ file, content, modified: stat.mtime.toISOString() });
        }
      } catch {}
    }
    return jsonReply(res, { userRequests, ticketRequests });
  }
  if (req.method === 'GET' && parts[0] === 'outbox' && parts.length === 1) {
    const notifications = safeReadFile(path.join(companyPath(), 'outbox', 'notifications.md'));
    const pendingApprovals = safeReadFile(path.join(companyPath(), 'outbox', 'pending-approvals.md'));
    const userQuestions = safeReadFile(path.join(companyPath(), 'outbox', 'user-questions.md'));
    const ceoEscalations = safeReadFile(path.join(companyPath(), 'outbox', 'ceo-escalations.md'));
    const userPrefs = readJson(path.join(companyPath(), 'user-preferences.json'));
    const userAnswers = safeReadFile(path.join(companyPath(), 'inbox', 'user-answers.md'));
    return jsonReply(res, { notifications, pendingApprovals, userQuestions, ceoEscalations, userPreferences: userPrefs, userAnswers });
  }

  // POST /api/outbox-clear - archive old outbox items
  if (req.method === 'POST' && parts[0] === 'outbox-clear' && parts.length === 1) {
    const outbox = path.join(companyPath(), 'outbox');
    const archive = path.join(outbox, 'archive');
    fs.mkdirSync(archive, { recursive: true });
    let archived = 0;
    for (const file of fs.readdirSync(outbox)) {
      if (!file.endsWith('.md')) continue;
      if (file === 'pending-approvals.md' || file === 'user-questions.md') {
        fs.writeFileSync(path.join(outbox, file), '');
        continue;
      }
      const src = path.join(outbox, file);
      if (fs.statSync(src).isFile()) {
        fs.renameSync(src, path.join(archive, file));
        archived++;
      }
    }
    return jsonReply(res, { ok: true, archived });
  }

  // POST /api/user-answer - user responds to a CEO escalation
  if (req.method === 'POST' && parts[0] === 'user-answer' && parts.length === 1) {
    const data = await readBody(req);
    const answer = data.answer;
    const question = data.question || '';
    if (!answer) return jsonReply(res, { error: 'answer required' }, 400);
    const timestamp = new Date().toISOString();
    const answersFile = path.join(companyPath(), 'inbox', 'user-answers.md');
    fs.mkdirSync(path.dirname(answersFile), { recursive: true });
    const entry = '\n---\n**[' + timestamp + '] USER**\nRe: ' + question + '\nAnswer: ' + answer + '\n';
    fs.appendFileSync(answersFile, entry);
    return jsonReply(res, { ok: true, timestamp });
  }

  // POST /api/user-request - user files a request/feedback
  // Routes through MCP inbox_write so the small-model auto-classification runs
  // when type/dept are 'auto'.
  if (req.method === 'POST' && parts[0] === 'user-request' && parts.length === 1) {
    const data = await readBody(req);
    const message = data.message;
    if (!message) return jsonReply(res, { error: 'message required' }, 400);
    const result = await mcpCall('inbox_write', {
      type: data.type || 'auto',
      dept: data.dept || 'auto',
      content: message,
      company_dir: companyPath(),
    });
    if (!result.ok) return jsonReply(res, { error: result.error || result.stderr || 'inbox write failed' }, 500);
    return jsonReply(res, result);
  }

  // 9. GET /api/marketing and GET /api/marketing/:type/:file
  if (req.method === 'GET' && parts[0] === 'marketing') {
    if (parts.length === 1) {
      const reportsDir = path.join(companyPath(), 'marketing', 'reports');
      const outputDir = path.join(companyPath(), 'marketing', 'output');
      const reports = [];
      const output = [];
      for (const file of safeListDir(reportsDir)) {
        try {
          const fp = path.join(reportsDir, file);
          const stat = fs.statSync(fp);
          if (stat.isFile()) reports.push({ file, size: stat.size, modified: stat.mtime.toISOString() });
        } catch {}
      }
      for (const file of safeListDir(outputDir)) {
        try {
          const fp = path.join(outputDir, file);
          const stat = fs.statSync(fp);
          if (stat.isFile()) output.push({ file, size: stat.size, modified: stat.mtime.toISOString() });
        } catch {}
      }
      return jsonReply(res, { reports, output });
    }
    if (parts.length === 3) {
      const type = parts[1]; // "reports" or "output"
      const file = decodeURIComponent(parts[2]);
      if (type !== 'reports' && type !== 'output') return jsonReply(res, { error: 'Invalid type' }, 400);
      const fp = path.join(companyPath(), 'marketing', type, file);
      const content = safeReadFile(fp);
      if (content === null) return jsonReply(res, { error: 'File not found' }, 404);
      return jsonReply(res, { type, file, content });
    }
  }

  // 10. GET /api/git
  if (req.method === 'GET' && parts[0] === 'git' && parts.length === 1) {
    const root = projectRoot();
    if (!root) return jsonReply(res, { error: 'No project root' }, 400);
    // Suppress stderr on every git call below — fallbacks are expected to
    // miss often (master vs main, branch with no merge-base, etc.) and the
    // dashboard log shouldn't fill with `fatal: ambiguous argument` lines.
    const gitOpts = { cwd: root, encoding: 'utf8', timeout: 5000, stdio: ['ignore', 'pipe', 'ignore'] };
    try {
      const logOutput = execSync('git log --oneline -15 --format="%h|%s|%ci"', gitOpts);
      const log = logOutput.trim().split('\n').filter(Boolean).map(line => {
        const p = line.split('|');
        return { hash: p[0] || '', message: p[1] || '', date: p[2] || '' };
      });
      const branchOutput = execSync('git branch', gitOpts);
      const branches = [];
      let current_branch = '';
      for (const line of branchOutput.trim().split('\n')) {
        const name = line.replace(/^\*?\s+/, '').trim();
        if (line.startsWith('*')) current_branch = name;
        if (name) branches.push(name);
      }
      // Worktrees
      let worktrees = [];
      try {
        const wtOutput = execSync('git worktree list --porcelain', gitOpts);
        let wt = {};
        for (const line of wtOutput.split('\n')) {
          if (line.startsWith('worktree ')) { if (wt.path) worktrees.push(wt); wt = { path: line.slice(9) }; }
          else if (line.startsWith('HEAD ')) wt.head = line.slice(5);
          else if (line.startsWith('branch ')) wt.branch = line.slice(7).replace('refs/heads/', '');
          else if (line === 'bare') wt.bare = true;
          else if (line === 'detached') wt.detached = true;
        }
        if (wt.path) worktrees.push(wt);
        // Add ticket info from branch name. Match any feature/<ID> (no longer
        // hardcoded to TICK-<DEPT>-<NUM> — ticket prefixes are per-dept and
        // arbitrary, e.g. AUTH-0001, BACKEN-0042, FRONTE-0007).
        for (const w of worktrees) {
          const m = (w.branch || '').match(/^feature\/(.+)$/);
          if (m) w.ticket = m[1];
          w.isMain = w.path === root || w.branch === 'main' || w.branch === 'master';
          // Diff base: prefer main, then master, then origin/HEAD's target.
          // Without this, a project on master gets 0 changedFiles + 0 ahead
          // for every branch (silent failure).
          let base = '';
          for (const candidate of ['main', 'master']) {
            try {
              execSync('git rev-parse --verify ' + candidate, gitOpts);
              base = candidate; break;
            } catch {}
          }
          if (!base) {
            try {
              const r = execSync("git symbolic-ref --short refs/remotes/origin/HEAD", gitOpts).trim();
              base = r.replace(/^origin\//, '');
            } catch {}
          }
          // Count files actually changed on this branch vs base, plus
          // uncommitted changes in the worktree.
          if (!w.isMain && w.branch && base) {
            let committedFiles = new Set();
            let uncommittedFiles = new Set();
            let aheadCommits = 0;
            try {
              const diff = execSync('git diff --name-only ' + base + '...' + w.branch, gitOpts);
              for (const line of diff.split('\n')) {
                const f = line.trim();
                if (f) committedFiles.add(f);
              }
            } catch {}
            try {
              const ahead = execSync('git rev-list --count ' + base + '..' + w.branch, gitOpts);
              aheadCommits = parseInt(ahead.trim(), 10) || 0;
            } catch {}
            try {
              const status = execSync('git status --porcelain',
                { ...gitOpts, cwd: w.path });
              for (const line of status.split('\n')) {
                if (!line.trim()) continue;
                const f = line.slice(3).split(' -> ').pop().trim();
                if (f) uncommittedFiles.add(f);
              }
            } catch {}
            const allFiles = new Set([...committedFiles, ...uncommittedFiles]);
            w.changedFiles = allFiles.size;
            w.committedFiles = committedFiles.size;
            w.uncommittedFiles = uncommittedFiles.size;
            w.aheadCommits = aheadCommits;
            w.dirty = uncommittedFiles.size > 0;
            w.base = base;
          } else {
            w.dirty = false;
            w.changedFiles = 0;
            w.committedFiles = 0;
            w.uncommittedFiles = 0;
            w.aheadCommits = 0;
          }
        }
      } catch {}

      return jsonReply(res, { log, branches, current_branch, worktrees });
    } catch (e) {
      return jsonReply(res, { error: 'Git command failed: ' + e.message, log: [], branches: [], current_branch: '', worktrees: [] });
    }
  }

  // 11. GET /api/brief
  if (req.method === 'GET' && parts[0] === 'brief' && parts.length === 1) {
    const brief = safeReadFile(path.join(companyPath(), 'brief.md'));
    const manifest = readJson(path.join(companyPath(), 'manifest.json'));
    return jsonReply(res, { brief, manifest });
  }

  // ===== WORKTREE BROWSING ENDPOINTS =====

  // Helper: find worktree path by name (branch name or directory name)
  function findWorktreePath(name) {
    const root = projectRoot();
    if (!root) return null;
    try {
      const wtOutput = execSync('git worktree list --porcelain', { cwd: root, encoding: 'utf8', timeout: 5000 });
      let wt = {};
      const worktrees = [];
      for (const line of wtOutput.split('\n')) {
        if (line.startsWith('worktree ')) { if (wt.path) worktrees.push(wt); wt = { path: line.slice(9) }; }
        else if (line.startsWith('branch ')) wt.branch = line.slice(7).replace('refs/heads/', '');
        else if (line === 'detached') wt.detached = true;
      }
      if (wt.path) worktrees.push(wt);
      // Match by branch name or last path segment
      for (const w of worktrees) {
        const dirName = path.basename(w.path);
        if (w.branch === name || dirName === name) return w.path;
      }
    } catch {}
    // Fallback: check .worktrees directory
    const wtDir = path.join(root, '.worktrees', name);
    if (fs.existsSync(wtDir)) return wtDir;
    // Also check .claude/worktrees
    const claudeWtDir = path.join(root, '.claude', 'worktrees', name);
    if (fs.existsSync(claudeWtDir)) return claudeWtDir;
    return null;
  }

  // Helper: parse unified diff output
  function parseDiffOutput(diffText) {
    const files = [];
    if (!diffText || !diffText.trim()) return files;
    const diffSections = diffText.split(/^diff --git /m).filter(Boolean);
    for (const section of diffSections) {
      const lines = section.split('\n');
      const headerMatch = lines[0].match(/a\/(.+?)\s+b\/(.+)/);
      const filePath = headerMatch ? headerMatch[2] : lines[0];
      const file = { path: filePath, additions: 0, deletions: 0, hunks: [] };
      let currentHunk = null;
      let oldLine = 0, newLine = 0;
      for (let i = 1; i < lines.length; i++) {
        const line = lines[i];
        if (line.startsWith('@@')) {
          const hunkMatch = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/);
          if (hunkMatch) {
            oldLine = parseInt(hunkMatch[1]);
            newLine = parseInt(hunkMatch[2]);
            currentHunk = { header: line, lines: [] };
            file.hunks.push(currentHunk);
          }
          continue;
        }
        if (!currentHunk) continue;
        if (line.startsWith('+') && !line.startsWith('+++')) {
          currentHunk.lines.push({ type: 'add', content: line.slice(1), oldNum: '', newNum: newLine });
          file.additions++;
          newLine++;
        } else if (line.startsWith('-') && !line.startsWith('---')) {
          currentHunk.lines.push({ type: 'del', content: line.slice(1), oldNum: oldLine, newNum: '' });
          file.deletions++;
          oldLine++;
        } else if (line.startsWith(' ') || line === '') {
          currentHunk.lines.push({ type: 'context', content: line.slice(1), oldNum: oldLine, newNum: newLine });
          oldLine++;
          newLine++;
        }
      }
      files.push(file);
    }
    return files;
  }

  const SKIP_DIRS = new Set(['.git', 'node_modules', '.company', '.worktrees', '.claude']);

  // GET /api/git/worktrees
  if (req.method === 'GET' && parts[0] === 'git' && parts[1] === 'worktrees' && parts.length === 2) {
    const root = projectRoot();
    if (!root) return jsonReply(res, { error: 'No project root' }, 400);
    try {
      const wtOutput = execSync('git worktree list --porcelain', { cwd: root, encoding: 'utf8', timeout: 5000 });
      let wt = {};
      const worktrees = [];
      for (const line of wtOutput.split('\n')) {
        if (line.startsWith('worktree ')) { if (wt.path) worktrees.push(wt); wt = { path: line.slice(9) }; }
        else if (line.startsWith('HEAD ')) wt.head = line.slice(5);
        else if (line.startsWith('branch ')) wt.branch = line.slice(7).replace('refs/heads/', '');
        else if (line === 'bare') wt.bare = true;
        else if (line === 'detached') wt.detached = true;
      }
      if (wt.path) worktrees.push(wt);
      for (const w of worktrees) {
        const m = (w.branch || '').match(/feature\/(TICK-[A-Z]+-\d+)/);
        if (m) w.ticket = m[1];
        w.isMain = w.path === root || w.branch === 'main' || w.branch === 'master';
        w.name = w.branch || path.basename(w.path);
        try {
          const status = execSync('git status --porcelain', { cwd: w.path, encoding: 'utf8', timeout: 3000 });
          w.dirty = status.trim().length > 0;
          w.changedFiles = status.trim().split('\n').filter(Boolean).length;
        } catch { w.dirty = false; w.changedFiles = 0; }
      }
      return jsonReply(res, { worktrees });
    } catch (e) {
      return jsonReply(res, { error: 'Git command failed: ' + e.message, worktrees: [] });
    }
  }

  // GET /api/git/worktrees/:name/files?path=
  if (req.method === 'GET' && parts[0] === 'git' && parts[1] === 'worktrees' && parts.length >= 4 && parts[3] === 'files') {
    const wtName = decodeURIComponent(parts[2]);
    const wtPath = findWorktreePath(wtName);
    if (!wtPath) return jsonReply(res, { error: 'Worktree not found: ' + wtName }, 404);
    const relPath = url.searchParams.get('path') || '';
    const targetPath = relPath ? path.join(wtPath, relPath) : wtPath;
    // Security: ensure we stay inside worktree
    const resolved = path.resolve(targetPath);
    if (!resolved.startsWith(path.resolve(wtPath))) return jsonReply(res, { error: 'Path outside worktree' }, 400);
    if (!fs.existsSync(resolved)) return jsonReply(res, { error: 'Path not found' }, 404);
    const stat = fs.statSync(resolved);
    if (stat.isDirectory()) {
      const entries = [];
      for (const name of fs.readdirSync(resolved)) {
        if (SKIP_DIRS.has(name)) continue;
        try {
          const entryPath = path.join(resolved, name);
          const entryStat = fs.statSync(entryPath);
          const entryRelPath = relPath ? relPath + '/' + name : name;
          entries.push({
            name: name,
            type: entryStat.isDirectory() ? 'dir' : 'file',
            size: entryStat.isDirectory() ? 0 : entryStat.size,
            path: entryRelPath
          });
        } catch {}
      }
      // Sort: dirs first, then alphabetical
      entries.sort(function(a, b) {
        if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      return jsonReply(res, { type: 'directory', entries: entries, currentPath: relPath });
    } else {
      // Read file content (limit to ~1MB)
      if (stat.size > 1024 * 1024) {
        return jsonReply(res, { type: 'file', path: relPath, content: '(File too large to display: ' + (stat.size / 1024 / 1024).toFixed(1) + 'MB)', truncated: true });
      }
      try {
        const content = fs.readFileSync(resolved, 'utf8');
        return jsonReply(res, { type: 'file', path: relPath, content: content, size: stat.size });
      } catch {
        return jsonReply(res, { type: 'file', path: relPath, content: '(Binary file)', binary: true });
      }
    }
  }

  // GET /api/git/worktrees/:name/diff
  if (req.method === 'GET' && parts[0] === 'git' && parts[1] === 'worktrees' && parts.length === 4 && parts[3] === 'diff') {
    const wtName = decodeURIComponent(parts[2]);
    const wtPath = findWorktreePath(wtName);
    if (!wtPath) return jsonReply(res, { error: 'Worktree not found: ' + wtName }, 404);
    try {
      // Try diff against main, fallback to master, fallback to HEAD~1.
      // stdio=pipe + stderr ignored so the expected misses on master/HEAD
      // don't spam company-board.log with `fatal: ambiguous argument` lines.
      let diffText = '';
      const diffOpts = { cwd: wtPath, encoding: 'utf8', timeout: 15000, maxBuffer: 10 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'] };
      try {
        diffText = execSync('git diff main...HEAD', diffOpts);
      } catch {
        try {
          diffText = execSync('git diff master...HEAD', diffOpts);
        } catch {
          try {
            diffText = execSync('git diff HEAD', diffOpts);
          } catch { diffText = ''; }
        }
      }
      // Also include uncommitted changes
      let uncommitted = '';
      try {
        uncommitted = execSync('git diff', { cwd: wtPath, encoding: 'utf8', timeout: 15000, maxBuffer: 10 * 1024 * 1024 });
      } catch {}
      let staged = '';
      try {
        staged = execSync('git diff --cached', { cwd: wtPath, encoding: 'utf8', timeout: 15000, maxBuffer: 10 * 1024 * 1024 });
      } catch {}
      const allDiff = [diffText, uncommitted, staged].filter(Boolean).join('\n');
      const files = parseDiffOutput(allDiff);
      return jsonReply(res, { files: files });
    } catch (e) {
      return jsonReply(res, { error: 'Diff failed: ' + e.message, files: [] });
    }
  }

  // GET /api/git/worktrees/:name/diff-file?path=
  if (req.method === 'GET' && parts[0] === 'git' && parts[1] === 'worktrees' && parts.length === 4 && parts[3] === 'diff-file') {
    const wtName = decodeURIComponent(parts[2]);
    const wtPath = findWorktreePath(wtName);
    if (!wtPath) return jsonReply(res, { error: 'Worktree not found: ' + wtName }, 404);
    const filePath = url.searchParams.get('path') || '';
    if (!filePath) return jsonReply(res, { error: 'path parameter required' }, 400);
    try {
      let diffText = '';
      try {
        diffText = execSync('git diff main -- ' + JSON.stringify(filePath), { cwd: wtPath, encoding: 'utf8', timeout: 10000, maxBuffer: 5 * 1024 * 1024 });
      } catch {
        try {
          diffText = execSync('git diff master -- ' + JSON.stringify(filePath), { cwd: wtPath, encoding: 'utf8', timeout: 10000, maxBuffer: 5 * 1024 * 1024 });
        } catch {
          diffText = execSync('git diff HEAD -- ' + JSON.stringify(filePath), { cwd: wtPath, encoding: 'utf8', timeout: 10000, maxBuffer: 5 * 1024 * 1024 });
        }
      }
      const files = parseDiffOutput(diffText);
      return jsonReply(res, { files: files });
    } catch (e) {
      return jsonReply(res, { error: 'Diff failed: ' + e.message, files: [] });
    }
  }

  // GET /api/git/commit/:hash - show a specific commit's diff
  if (req.method === 'GET' && parts[0] === 'git' && parts[1] === 'commit' && parts.length === 3) {
    const root = projectRoot();
    if (!root) return jsonReply(res, { error: 'No project root' }, 400);
    const hash = parts[2];
    try {
      const info = execSync('git log -1 --format="%H|%s|%an|%ci" ' + hash, { cwd: root, encoding: 'utf8', timeout: 5000 }).trim();
      const p = info.split('|');
      const commit = { hash: p[0], message: p[1], author: p[2], date: p[3] };
      let diffText = '';
      try {
        diffText = execSync('git diff ' + hash + '~1 ' + hash, { cwd: root, encoding: 'utf8', timeout: 10000, maxBuffer: 5 * 1024 * 1024 });
      } catch { /* first commit */ }
      const files = parseDiffOutput(diffText);
      return jsonReply(res, { commit, files });
    } catch (e) {
      return jsonReply(res, { error: 'Commit not found: ' + hash });
    }
  }

  // GET /api/git/branch-history/:branch - show all commits on a branch and the merge diff
  if (req.method === 'GET' && parts[0] === 'git' && parts[1] === 'branch-history' && parts.length === 3) {
    const root = projectRoot();
    if (!root) return jsonReply(res, { error: 'No project root' }, 400);
    const branch = decodeURIComponent(parts[2]);
    const ticketId = branch.replace(/^feature\//, '');
    const gitOpts = { cwd: root, encoding: 'utf8', timeout: 5000, stdio: ['ignore', 'pipe', 'ignore'] };
    try {
      // Resolve the diff base (main / master / origin/HEAD). Without this,
      // `git log <hardcoded>..branch` silently returns empty for every branch
      // on a non-main project, and the dashboard shows "no commits" for
      // perfectly-fine branches.
      let base = '';
      for (const candidate of ['main', 'master']) {
        try { execSync('git rev-parse --verify ' + candidate, gitOpts); base = candidate; break; } catch {}
      }
      if (!base) {
        try {
          const r = execSync("git symbolic-ref --short refs/remotes/origin/HEAD", gitOpts).trim();
          base = r.replace(/^origin\//, '');
        } catch {}
      }

      // Verify the branch ref actually exists. If not, return an empty but
      // explicit response so the UI can render "branch not found" instead
      // of mysteriously empty history.
      let branchExists = true;
      try { execSync('git rev-parse --verify ' + branch, gitOpts); }
      catch { branchExists = false; }

      // Real branch log: every commit reachable from <branch> that's NOT
      // reachable from <base>. This is what "branch history" actually means
      // — replaces the old `--grep="<ticketId>"` heuristic that only worked
      // when every commit message mentioned the ticket id.
      let commits = [];
      if (branchExists) {
        let cmd;
        if (base) {
          // Normal case: branch diverges from base.
          cmd = 'git log ' + base + '..' + branch + ' --format="%H|%s|%an|%ci"';
        } else {
          // No base resolvable — fall back to last 30 commits on the branch.
          cmd = 'git log ' + branch + ' -30 --format="%H|%s|%an|%ci"';
        }
        try {
          const logOut = execSync(cmd, gitOpts);
          commits = logOut.trim().split('\n').filter(Boolean).map(l => {
            const p2 = l.split('|');
            return { hash: p2[0], message: p2[1], author: p2[2], date: p2[3] };
          });
        } catch {}
      }

      // Find the merge commit (if the branch was already merged).
      // GitHub PR merges produce "Merge pull request #N from owner/<branch>";
      // local merges produce "Merge branch '<branch>'" or "Merge feature/<X>".
      // Try both patterns.
      let mergeCommit = null;
      let mergeDiff = [];
      const mergePatterns = [
        "Merge pull request.*" + branch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + "$",
        "Merge branch '?" + branch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + "'?",
        "Merge " + branch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'),
      ];
      for (const pat of mergePatterns) {
        try {
          const out = execSync('git log --all --format="%H|%s|%ci" --grep=' + JSON.stringify(pat) + ' -E -1', gitOpts).trim();
          if (out) {
            const mp = out.split('|');
            mergeCommit = { hash: mp[0], message: mp[1], date: mp[2] };
            try {
              const mDiff = execSync('git diff ' + mp[0] + '~1 ' + mp[0], { ...gitOpts, timeout: 10000, maxBuffer: 5 * 1024 * 1024 });
              mergeDiff = parseDiffOutput(mDiff);
            } catch {}
            break;
          }
        } catch {}
      }

      return jsonReply(res, {
        branch, ticketId, base,
        branchExists,
        commits,
        mergeCommit,
        mergeDiff,
      });
    } catch (e) {
      return jsonReply(res, { error: 'Branch history failed: ' + e.message, branch, commits: [], mergeDiff: [] });
    }
  }

  jsonReply(res, { error: 'Not found' }, 404);
}

const HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Company Board</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'SF Mono','Fira Code',Consolas,monospace;background:#0d1117;color:#c9d1d9;min-height:100vh}
a{color:#58a6ff;text-decoration:none}
.header{background:#161b22;border-bottom:1px solid #30363d;padding:10px 24px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.header h1{font-size:18px;color:#f0f6fc}
.header .nav{display:flex;gap:4px;flex-wrap:wrap}
.header .nav button{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:5px 11px;border-radius:6px;cursor:pointer;font-family:inherit;font-size:12px}
.header .nav button:hover,.header .nav button.active{background:#30363d;border-color:#58a6ff;color:#f0f6fc}
.company-path{font-size:11px;color:#8b949e;margin-left:auto;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer}
.company-path:hover{color:#58a6ff;text-decoration:underline}
.container{padding:24px;max-width:1600px;margin:0 auto}

/* Dropdown */
.dropdown-wrap{position:relative;display:inline-block}
.dropdown{display:none;position:absolute;top:100%;right:0;margin-top:4px;background:#161b22;border:1px solid #30363d;border-radius:8px;min-width:180px;z-index:50;box-shadow:0 8px 24px rgba(0,0,0,.4);padding:4px 0}
.dropdown.open{display:block}
.dropdown button{display:block;width:100%;background:none;border:none;color:#c9d1d9;padding:8px 14px;text-align:left;cursor:pointer;font-family:inherit;font-size:12px}
.dropdown button:hover{background:#21262d;color:#f0f6fc}

/* Connect screen */
.connect-screen{display:flex;justify-content:center;align-items:center;min-height:calc(100vh - 60px)}
.connect-box{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:32px;width:100%;max-width:500px;text-align:center}
.connect-box h2{font-size:20px;color:#f0f6fc;margin-bottom:8px}
.connect-box p{font-size:13px;color:#8b949e;margin-bottom:20px}
.connect-box input{width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:10px 14px;border-radius:6px;font-family:inherit;font-size:14px;margin-bottom:12px}
.connect-box .error{color:#f85149;font-size:12px;margin-bottom:8px;display:none}
.connect-box .recent{text-align:left;margin-top:16px;border-top:1px solid #30363d;padding-top:16px}
.connect-box .recent h4{font-size:12px;color:#8b949e;margin-bottom:8px}
.connect-box .recent-item{padding:6px 10px;cursor:pointer;border-radius:4px;font-size:13px;color:#58a6ff}
.connect-box .recent-item:hover{background:#21262d}

/* Dashboard */
.dashboard{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
.dept-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;cursor:pointer;transition:border-color .2s}
.dept-card:hover{border-color:#58a6ff}
.dept-card h3{font-size:16px;color:#f0f6fc;margin-bottom:8px}
.dept-card .counts{display:flex;gap:8px;flex-wrap:wrap}
.badge{font-size:11px;padding:2px 8px;border-radius:12px}
.badge-backlog{background:#30363d;color:#8b949e}
.badge-sprint{background:#1f3a5f;color:#58a6ff}
.badge-progress{background:#2a1f3f;color:#bc8cff}
.badge-review{background:#3f2a1f;color:#f0883e}
.badge-done{background:#1f3f2a;color:#3fb950}

/* Board */
.board{display:flex;gap:12px;overflow-x:auto;min-height:calc(100vh - 180px);padding-bottom:16px}
.column{min-width:260px;flex:1;background:#161b22;border:1px solid #30363d;border-radius:8px;display:flex;flex-direction:column;max-height:calc(100vh - 180px)}
.column-header{padding:12px;border-bottom:1px solid #30363d;font-size:13px;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;display:flex;justify-content:space-between}
.column-header .count{background:#30363d;padding:2px 8px;border-radius:12px;font-size:11px}
.column-body{padding:8px;overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:8px}
.ticket-card{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px 12px;cursor:pointer;transition:border-color .2s}
.ticket-card:hover{border-color:#58a6ff}
.ticket-card .ticket-id{font-size:11px;color:#8b949e}
.ticket-card .ticket-title{font-size:13px;color:#f0f6fc;margin:4px 0;line-height:1.3}
.ticket-card .ticket-meta{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.priority-critical{color:#f85149}.priority-high{color:#f0883e}.priority-medium{color:#d29922}.priority-low{color:#8b949e}
.type-badge{font-size:10px;padding:1px 6px;border-radius:4px;background:#21262d;color:#8b949e}

/* Modal */
.modal-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);display:none;justify-content:center;align-items:flex-start;padding:40px;z-index:100;overflow-y:auto}
.modal-overlay.open{display:flex}
.modal{background:#161b22;border:1px solid #30363d;border-radius:12px;width:100%;max-width:700px;padding:24px}
.modal h2{font-size:18px;color:#f0f6fc;margin-bottom:16px}
.modal label{display:block;font-size:12px;color:#8b949e;margin:12px 0 4px}
.modal input,.modal textarea,.modal select{width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:8px 12px;border-radius:6px;font-family:inherit;font-size:13px}
.modal textarea{min-height:100px;resize:vertical}
.modal .actions{display:flex;gap:8px;margin-top:16px;justify-content:flex-end}
.btn{padding:8px 16px;border-radius:6px;border:1px solid #30363d;background:#21262d;color:#c9d1d9;cursor:pointer;font-family:inherit;font-size:13px}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}
.btn-danger{background:#da3633;border-color:#f85149;color:#fff}
.btn:hover{background:#30363d}
.detail-field{margin:8px 0}.detail-field .label{font-size:11px;color:#8b949e}.detail-field .value{font-size:13px;color:#f0f6fc;margin-top:2px}
.comment{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px;margin:6px 0}
.comment .comment-author{font-size:11px;color:#58a6ff}.comment .comment-body{font-size:13px;margin-top:4px;white-space:pre-wrap}
.bounce-count{padding:2px 8px;border-radius:12px;background:rgba(139,148,158,0.12);font-size:12px}
.status-select{display:inline-block;width:auto;padding:4px 8px;font-size:12px;margin-left:8px}

/* Org Chart */
.org-tree{margin-top:8px}
.org-ceo{background:#1f1533;border-color:#bc8cff}
.org-children{margin-left:24px;border-left:2px solid #30363d;padding-left:16px;display:flex;flex-direction:column;gap:10px;margin-top:10px}
.org-node{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;position:relative}
.org-fixed{border-left:3px solid #58a6ff}
.org-dynamic{border-left:3px solid #3fb950}
.org-node-title{font-size:14px;color:#f0f6fc;font-weight:600}
.org-node-sub{font-size:12px;color:#8b949e;margin-top:2px;line-height:1.4}
.org-node-meta{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}
.org-node-badge{font-size:10px;padding:2px 8px;border-radius:4px}
.org-badge-fixed{background:#1f3a5f;color:#58a6ff}
.org-badge-dynamic{background:#1f3f2a;color:#3fb950}
.org-components{display:flex;gap:4px;flex-wrap:wrap;margin-top:8px}
.org-component{font-size:11px;background:#21262d;color:#c9d1d9;padding:2px 8px;border-radius:4px}
.org-deps{font-size:11px;color:#f0883e;margin-top:4px}
.org-teams{margin-top:8px;border-top:1px solid #21262d;padding-top:6px}
.org-team{font-size:12px;color:#c9d1d9;margin:3px 0;padding-left:12px;border-left:2px solid #30363d}
.org-team-name{color:#bc8cff;font-weight:600}
.org-team-detail{color:#8b949e;font-size:11px;margin-left:6px}
.org-agents-list{margin-top:8px;border-top:1px solid #21262d;padding-top:6px}
.org-agent{display:flex;align-items:center;gap:6px;padding:3px 0;font-size:12px}
.org-agent-icon{background:#21262d;color:#8b949e;font-size:9px;font-weight:700;padding:2px 4px;border-radius:3px;min-width:24px;text-align:center}
.org-agent-role{color:#c9d1d9}
.org-agent-gen{color:#f0883e;font-size:10px}
.org-agent-notes{color:#484f58;font-size:11px;margin-left:auto;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.org-no-agents{font-size:11px;color:#484f58;margin-top:6px;font-style:italic}

/* Chat */
.chat-layout{display:grid;grid-template-columns:240px 1fr;gap:16px;min-height:calc(100vh - 120px)}
.channel-list{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow-y:auto}
.channel-item{padding:10px 14px;cursor:pointer;border-bottom:1px solid #21262d;display:flex;justify-content:space-between;align-items:center}
.channel-item:hover,.channel-item.active{background:#21262d}
.channel-item .ch-name{font-size:13px;color:#f0f6fc}
.channel-item .ch-count{font-size:11px;color:#8b949e;background:#30363d;padding:1px 6px;border-radius:8px}
.chat-view{background:#161b22;border:1px solid #30363d;border-radius:8px;display:flex;flex-direction:column;max-height:calc(100vh - 120px)}
.chat-header{padding:12px 16px;border-bottom:1px solid #30363d;font-size:14px;color:#f0f6fc;font-weight:600}
.chat-messages{flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:12px}
.chat-msg{max-width:85%}
.chat-msg .msg-header{font-size:11px;margin-bottom:2px}
.chat-msg .msg-author{color:#58a6ff;font-weight:600}
.chat-msg .msg-time{color:#484f58;margin-left:8px}
.chat-msg .msg-body{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:8px 12px;font-size:13px;line-height:1.5;white-space:pre-wrap}
.chat-empty{color:#484f58;text-align:center;padding:40px;font-size:13px}

/* Meetings */
.meetings-layout{display:grid;grid-template-columns:320px 1fr;gap:16px;min-height:calc(100vh - 120px)}
.meeting-list{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow-y:auto}
.meeting-item{padding:12px 14px;cursor:pointer;border-bottom:1px solid #21262d}
.meeting-item:hover,.meeting-item.active{background:#21262d}
.meeting-item .mt-title{font-size:13px;color:#f0f6fc;line-height:1.3}
.meeting-item .mt-date{font-size:11px;color:#8b949e;margin-top:2px}
.meeting-view{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow-y:auto;max-height:calc(100vh - 120px)}
.meeting-content{padding:20px 24px;font-size:13px;line-height:1.7}
.meeting-content h1,.meeting-content h2,.meeting-content h3{color:#f0f6fc;margin:16px 0 8px}
.meeting-content h1{font-size:18px;border-bottom:1px solid #30363d;padding-bottom:8px}
.meeting-content h2{font-size:15px}
.meeting-content h3{font-size:13px;color:#8b949e}
.meeting-content ul,.meeting-content ol{padding-left:20px;margin:8px 0}
.meeting-content li{margin:4px 0}
.meeting-content code{background:#21262d;padding:1px 4px;border-radius:3px;font-size:12px}
.meeting-content blockquote{border-left:3px solid #30363d;padding-left:12px;color:#8b949e;margin:8px 0}
.meeting-content hr{border:none;border-top:1px solid #30363d;margin:16px 0}
.meeting-empty{color:#484f58;text-align:center;padding:40px;font-size:13px}
.schedule-box{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:12px}
.schedule-box h4{font-size:13px;color:#f0f6fc;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #21262d}
.sch-row{display:flex;align-items:center;padding:8px 0;border-bottom:1px solid #161b22}
.sch-row:last-child{border-bottom:none}
.sch-icon{width:28px;height:28px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:14px;margin-right:10px;flex-shrink:0}
.sch-icon-standup{background:#1f3a5f;color:#58a6ff}
.sch-icon-sprint{background:#1f3f2a;color:#3fb950}
.sch-info{flex:1}
.sch-label{font-size:13px;color:#f0f6fc}
.sch-time{font-size:11px;color:#8b949e;margin-top:1px}
.sch-badge{font-size:10px;padding:2px 8px;border-radius:4px;margin-left:8px}
.sch-notes{font-size:11px;color:#8b949e;margin-top:10px;padding-top:8px;border-top:1px solid #21262d;line-height:1.5}

/* Org Chart tree */
.tree-node{margin-left:20px;padding:6px 0}
.tree-node-header{display:flex;align-items:center;gap:8px;padding:6px 12px;background:#161b22;border:1px solid #30363d;border-radius:6px;margin:2px 0;cursor:pointer}
.tree-node-header:hover{border-color:#58a6ff}
.tree-node-header .node-label{font-size:13px;color:#f0f6fc;font-weight:600}
.tree-node-header .node-type{font-size:10px;padding:1px 6px;border-radius:4px}
.tree-node-header .node-type.type-fixed{background:#1f3a5f;color:#58a6ff}
.tree-node-header .node-type.type-dynamic{background:#1f3f2a;color:#3fb950}
.tree-node-header .node-type.type-department{background:#3f2a1f;color:#f0883e}
.tree-node-header .node-type.type-ceo{background:#2a1f3f;color:#bc8cff}
.tree-node-children{border-left:2px solid #30363d;margin-left:16px;padding-left:8px}
.tree-detail{font-size:11px;color:#8b949e;margin-left:4px}

/* Progress bar */
.progress-bar{background:#21262d;border-radius:6px;height:20px;overflow:hidden;position:relative;margin:4px 0}
.progress-fill{height:100%;border-radius:6px;transition:width .3s;display:flex;align-items:center;justify-content:center;font-size:10px;color:#fff;font-weight:600;min-width:0}
.progress-fill.fill-done{background:#238636}
.progress-fill.fill-progress{background:#1f6feb}
.progress-fill.fill-review{background:#d29922}

/* Filters */
.filter-bar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
.filter-bar label{font-size:11px;color:#8b949e}
.filter-bar input,.filter-bar select{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:4px 8px;border-radius:4px;font-family:inherit;font-size:12px}
.filter-bar input{width:160px}
.filter-bar select{min-width:100px}
.filter-bar .filter-clear{font-size:11px;color:#58a6ff;cursor:pointer;padding:4px 8px}
.filter-bar .filter-clear:hover{text-decoration:underline}
.filter-count{font-size:11px;color:#8b949e;margin-left:auto}

/* Data table */
.data-table{width:100%;border-collapse:collapse;font-size:12px}
.data-table th{text-align:left;padding:8px 12px;border-bottom:2px solid #30363d;color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:.5px;cursor:pointer;user-select:none;white-space:nowrap}
.data-table th:hover{color:#f0f6fc}
.data-table th .sort-arrow{margin-left:4px;font-size:10px;color:#484f58}
.data-table th.sorted .sort-arrow{color:#58a6ff}
.data-table td{padding:8px 12px;border-bottom:1px solid #21262d;color:#c9d1d9}
.data-table tr:hover{background:#161b22}

/* Log viewer */
.log-viewer{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:16px;font-size:11px;line-height:1.6;overflow-x:auto;overflow-y:auto;max-height:calc(100vh - 240px);white-space:pre;color:#8b949e;font-family:'SF Mono','Fira Code',Consolas,monospace}

/* Finding card */
.finding-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:12px 0}
.finding-card .finding-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.finding-card .finding-title{font-size:14px;color:#f0f6fc;font-weight:600}
.finding-card .finding-date{font-size:11px;color:#8b949e}
.finding-card .finding-body{font-size:13px;line-height:1.6}

/* Timeline */
.timeline{position:relative;padding-left:24px}
.timeline::before{content:'';position:absolute;left:8px;top:0;bottom:0;width:2px;background:#30363d}
.timeline-item{position:relative;padding:6px 0 6px 16px;font-size:12px}
.timeline-item::before{content:'';position:absolute;left:-20px;top:12px;width:10px;height:10px;border-radius:50%;background:#30363d;border:2px solid #0d1117}
.timeline-item .commit-hash{color:#58a6ff;font-weight:600;margin-right:8px}
.timeline-item .commit-msg{color:#c9d1d9}
.timeline-item .commit-date{color:#484f58;font-size:11px;margin-left:8px}

/* Status dots */
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.dot-active{background:#3fb950}
.dot-claimed{background:#d29922;box-shadow:0 0 0 1px #d29922 inset;background:repeating-linear-gradient(45deg,#d29922,#d29922 2px,#382b00 2px,#382b00 4px)}
.dot-retired{background:#484f58}
.dot-stale{background:#d29922}
.dot-dead{background:#f85149}
.dot-alive{background:#3fb950}

/* Panel layouts */
.panel-layout{display:grid;grid-template-columns:260px 1fr;gap:16px;min-height:calc(100vh - 120px)}
.panel-left{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow-y:auto;max-height:calc(100vh - 120px)}
.panel-right{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow-y:auto;max-height:calc(100vh - 120px);padding:20px 24px}
.panel-item{padding:10px 14px;cursor:pointer;border-bottom:1px solid #21262d;font-size:13px;color:#c9d1d9;display:flex;justify-content:space-between;align-items:center}
.panel-item:hover,.panel-item.active{background:#21262d;color:#f0f6fc}
.panel-item .item-meta{font-size:11px;color:#8b949e}

/* Tabs */
.tab-bar{display:flex;gap:0;margin-bottom:16px;border-bottom:1px solid #30363d}
.tab-btn{background:none;border:none;border-bottom:2px solid transparent;color:#8b949e;padding:8px 16px;cursor:pointer;font-family:inherit;font-size:13px}
.tab-btn:hover{color:#c9d1d9}
.tab-btn.active{color:#f0f6fc;border-bottom-color:#58a6ff}

/* Summary cards row */
.summary-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.summary-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;flex:1;min-width:120px;text-align:center}
.summary-card .num{font-size:24px;font-weight:700;color:#f0f6fc}
.summary-card .lbl{font-size:11px;color:#8b949e;margin-top:2px}

/* Markdown rendered in panels */
.md-content{font-size:13px;line-height:1.7}
.md-content h1,.md-content h2,.md-content h3{color:#f0f6fc;margin:16px 0 8px}
.md-content h1{font-size:18px;border-bottom:1px solid #30363d;padding-bottom:8px}
.md-content h2{font-size:15px}
.md-content h3{font-size:13px;color:#8b949e}
.md-content ul,.md-content ol{padding-left:20px;margin:8px 0}
.md-content li{margin:4px 0}
.md-content code{background:#21262d;padding:1px 4px;border-radius:3px;font-size:12px}
.md-content blockquote{border-left:3px solid #30363d;padding-left:12px;color:#8b949e;margin:8px 0}
.md-content hr{border:none;border-top:1px solid #30363d;margin:16px 0}

/* Section header */
.section-hdr{font-size:15px;color:#f0f6fc;margin:20px 0 12px;padding-bottom:6px;border-bottom:1px solid #30363d}
.section-hdr:first-child{margin-top:0}

/* Branch badge */
.branch-badge{display:inline-block;background:#1f3a5f;color:#58a6ff;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:600;margin:4px 4px 4px 0}
.branch-badge.current{background:#238636;color:#fff}

/* Brief manifest grid */
.manifest-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin:16px 0}
.manifest-field{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px 12px}
.manifest-field .mf-label{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}
.manifest-field .mf-value{font-size:14px;color:#f0f6fc;margin-top:2px;word-break:break-all}

/* Worktree browser */
.wt-header{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:12px}
.wt-tabs{display:flex;gap:0;margin-bottom:12px;border-bottom:1px solid #30363d}
.wt-tab{padding:8px 16px;cursor:pointer;color:#8b949e;border-bottom:2px solid transparent;background:none;border-top:none;border-left:none;border-right:none;font-family:inherit;font-size:13px}
.wt-tab.active{color:#f0f6fc;border-bottom-color:#58a6ff}
.wt-tab:hover{color:#f0f6fc}

/* File browser */
.file-list{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden}
.file-entry{display:flex;align-items:center;padding:8px 16px;border-bottom:1px solid #21262d;cursor:pointer;gap:8px}
.file-entry:hover{background:#21262d}
.file-entry:last-child{border-bottom:none}
.file-icon{width:16px;color:#8b949e;font-size:14px;text-align:center}
.file-name{color:#58a6ff;font-size:13px}
.file-name.dir{color:#f0f6fc}
.file-size{color:#484f58;font-size:11px;margin-left:auto}
.breadcrumb{font-size:13px;color:#8b949e;margin-bottom:8px}
.breadcrumb a{color:#58a6ff;cursor:pointer;text-decoration:none}
.breadcrumb a:hover{text-decoration:underline}
.breadcrumb span{color:#f0f6fc}

/* File viewer */
.file-viewer{background:#0d1117;border:1px solid #30363d;border-radius:8px;overflow:auto;max-height:70vh}
.file-viewer-header{background:#161b22;padding:8px 16px;border-bottom:1px solid #30363d;font-size:12px;color:#8b949e;display:flex;align-items:center;justify-content:space-between}
.file-line{display:flex;font-size:12px;font-family:'SF Mono','Fira Code',Consolas,monospace;line-height:1.6}
.file-line-num{color:#484f58;text-align:right;padding:0 8px;min-width:50px;user-select:none;background:#0d1117}
.file-line-content{padding:0 8px;white-space:pre;flex:1;overflow-x:auto}

/* Diff viewer */
.diff-file-list{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:12px}
.diff-file-header{display:flex;align-items:center;padding:8px 16px;border-bottom:1px solid #21262d;cursor:pointer;gap:8px}
.diff-file-header:hover{background:#21262d}
.diff-adds{color:#3fb950;font-size:12px}
.diff-dels{color:#f85149;font-size:12px}
.diff-file-path{color:#f0f6fc;font-size:13px;flex:1}
.diff-content{border-top:1px solid #21262d}
.diff-hunk-header{background:#1f2937;color:#58a6ff;padding:4px 16px;font-size:12px;font-family:'SF Mono','Fira Code',Consolas,monospace}
.diff-line{display:flex;font-size:12px;font-family:'SF Mono','Fira Code',Consolas,monospace;line-height:1.6}
.diff-line-num{color:#484f58;text-align:right;padding:0 4px;min-width:40px;user-select:none}
.diff-line-content{padding:0 8px;white-space:pre;flex:1;overflow-x:auto}
.diff-line.add{background:rgba(63,185,80,0.15)}
.diff-line.add .diff-line-content{color:#3fb950}
.diff-line.del{background:rgba(248,81,73,0.15)}
.diff-line.del .diff-line-content{color:#f85149}
.diff-line.context .diff-line-content{color:#8b949e}
.diff-summary{display:flex;gap:16px;align-items:center;margin-bottom:12px;font-size:13px;color:#8b949e}
.diff-summary .total-adds{color:#3fb950}
.diff-summary .total-dels{color:#f85149}
.wt-clickable{cursor:pointer}
.wt-clickable:hover{background:#1c2129}
/* Mobile responsive */
@media(max-width:768px){
  .header{flex-wrap:wrap;padding:8px 12px;gap:8px}
  .header h1{font-size:15px}
  .header .nav{width:100%;overflow-x:visible;flex-wrap:wrap;padding-bottom:4px}
  .header .nav button{font-size:11px;padding:5px 10px;white-space:nowrap;flex-shrink:0}
  .company-path{display:none}
  #live-indicator{font-size:10px}
  .container{padding:10px}
  .dashboard{grid-template-columns:1fr}
  .board{flex-direction:column;min-height:auto}
  .column{min-width:100%;max-height:none}
  .column-body{max-height:300px}
  .filter-bar{flex-direction:column;gap:4px}
  .filter-bar input,.filter-bar select{width:100%}
  .filter-count{margin-left:0}
  .panel-layout,.meetings-layout,.chat-layout{grid-template-columns:1fr;grid-template-rows:auto 1fr}
  .panel-left,.meeting-list,.channel-list{max-height:200px;overflow-y:auto}
  .panel-right,.meeting-view,.chat-view{max-height:calc(100vh - 350px)}
  .modal-overlay{padding:10px}
  .modal{max-width:100%;padding:16px}
  .data-table{font-size:11px;display:block;overflow-x:auto}
  .data-table th,.data-table td{padding:4px 6px}
  .summary-row{flex-wrap:wrap}
  .summary-card{min-width:70px}
  .org-children{margin-left:12px;padding-left:8px}
  .org-node{padding:8px 10px}
  .org-agent{flex-wrap:wrap}
  .org-agent-notes{max-width:200px}
  .wt-tabs{overflow-x:auto;flex-wrap:nowrap}
  .wt-tab{white-space:nowrap;flex-shrink:0;font-size:12px;padding:6px 10px}
  .wt-header{padding:10px}
  .file-viewer{max-height:50vh}
  .diff-line-content{font-size:11px}
  .timeline-item{flex-wrap:wrap}
  .finding-card{padding:10px}
  .schedule-box{padding:10px}
  .sch-row{flex-wrap:wrap}
  .connect-box{padding:20px}
  .dropdown{right:10px;left:auto;position:fixed;top:80px;z-index:100}
}
@media(max-width:480px){
  .header .nav button{font-size:10px;padding:4px 7px}
  .board .column{min-width:100%}
  .data-table th,.data-table td{padding:3px 4px;font-size:10px}
  .summary-card .num{font-size:16px}
  .ticket-card{padding:6px 8px}
  .dept-card{padding:10px}
  .modal{border-radius:8px}
}
</style>
</head>
<body>
<div class="header">
  <h1>🐝 Hive Dashboard</h1>
  <div class="company-control" id="company-control" style="display:none; margin-left: 16px;">
    <button id="btn-start-company" onclick="startCompany()" style="background:#1f6f3a;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;margin-right:6px;">▶ Start Company</button>
    <button id="btn-stop-company" onclick="stopCompany()" style="background:#8a1f1f;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;">■ Stop Company</button>
    <span id="company-status-pill" style="margin-left:10px;font-size:12px;color:#8b949e;">status: unknown</span>
  </div>
  <div class="nav" id="nav" style="display:none">
    <button class="active" onclick="showDashboard()">Tickets</button>
    <button onclick="showChats()">Chat</button>
    <button onclick="showMeetings()">Meetings</button>
    <button onclick="showSprint()">Sprint</button>
    <button onclick="showOrgChart()">Org</button>
    <button onclick="showRoster()">Agents</button>
    <button onclick="showReports()">Reports</button>
    <button onclick="showLogs()">Logs</button>
    <button onclick="showVerification()">Verification</button>
    <button onclick="showActivity()">Activity</button>
    <button onclick="showRounds()">Rounds</button>
    <button onclick="showGitHub()">GitHub</button>
    <button onclick="showReleases()">Releases</button>
    <div class="dropdown-wrap">
      <button onclick="toggleMore(event)">More &#9662;</button>
      <div class="dropdown" id="more-dropdown">
        <button onclick="showGit();closeMore()">Git</button>
        <button onclick="showInbox();closeMore()">Inbox/Outbox</button>
        <button onclick="showBrief();closeMore()">Brief</button>
        <button onclick="showProcesses();closeMore()">Processes</button>
      </div>
    </div>
  </div>
  <span id="live-indicator" style="font-size:11px;color:#3fb950;margin-left:auto;white-space:nowrap"></span>
  <span class="company-path" id="company-path" onclick="showConnect()" title="Click to change project" style="margin-left:12px"></span>
</div>
<div class="container" id="content"></div>
<div class="modal-overlay" id="modal-overlay" onclick="if(event.target===this)closeModal()">
  <div class="modal" id="modal"></div>
</div>
<script>
let departments=[];
let currentDept=null;
var activeView='dashboard';
// panelRefreshers — registered by each showX() via startAutoRefresh, fired by
// SSE event handlers via refreshActive(panelKey). Keyed by activeView value.
var panelRefreshers={};
let connected=false;
var hiveStartedAt=0;       // Unix seconds: when the Hive process started (from /api/status)
var sseConnected=false;     // true while SSE connection is alive

const RECENT_KEY='company-board-recent';
function getRecent(){try{return JSON.parse(localStorage.getItem(RECENT_KEY))||[]}catch{return[]}}
function addRecent(p){const r=getRecent().filter(x=>x!==p);r.unshift(p);localStorage.setItem(RECENT_KEY,JSON.stringify(r.slice(0,5)))}

async function api(path,opts){const r=await fetch('/api/'+path,opts);return r.json()}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function saveFocus(){var el=document.activeElement;if(el&&(el.tagName==='INPUT'||el.tagName==='TEXTAREA'||el.tagName==='SELECT')){return{id:el.id,val:el.value,start:el.selectionStart,end:el.selectionEnd}}return null}
function restoreFocus(f){if(!f||!f.id)return;var el=document.getElementById(f.id);if(el){el.focus();if(f.val!=null)el.value=f.val;try{if(f.start!=null)el.setSelectionRange(f.start,f.end)}catch(e){}}}
function localTime(s){if(!s)return '';try{var d=new Date(s);if(isNaN(d))return esc(s);return d.toLocaleString()}catch(e){return esc(s)}}
function localDate(s){if(!s)return '';try{var d=new Date(s);if(isNaN(d))return esc(s);return d.toLocaleDateString()}catch(e){return esc(s)}}
function localShort(s){if(!s)return '';try{var d=new Date(s);if(isNaN(d))return esc(s);return d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}catch(e){return esc(s)}}
function df(l,v){return '<div class="detail-field"><div class="label">'+l+'</div><div class="value">'+(v||'-')+'</div></div>'}
function bounceColor(n){var p=Math.min(n/5*100,100);return n===0?'#8b949e':n<=2?'#d29922':n<=4?'#f85149':'#da3633'}
function openModal(){document.getElementById('modal-overlay').classList.add('open')}
function closeModal(){document.getElementById('modal-overlay').classList.remove('open')}
function isUserTyping(){var el=document.activeElement;return el&&(el.tagName==='INPUT'||el.tagName==='TEXTAREA'||el.tagName==='SELECT')}
function saveScroll(){
  // Capture window + every [id] element currently scrolled away from origin.
  // Also note whether each scrollable element was glued to the bottom — that
  // signal drives "follow-the-tail" behavior in chat/logs/spawn-stream after
  // the render swaps in new content.
  return {
    x: window.scrollX, y: window.scrollY,
    els: Array.from(document.querySelectorAll('[id]')).filter(function(e){
      return e.scrollTop>0 || e.scrollLeft>0 ||
             (e.scrollHeight > e.clientHeight + 4);  // also track tail-followable empties
    }).map(function(e){
      return {
        id: e.id,
        top: e.scrollTop, left: e.scrollLeft,
        oldScrollHeight: e.scrollHeight,
        atBottom: (e.scrollHeight - e.clientHeight - e.scrollTop) <= 16
      };
    })
  };
}
function restoreScroll(s){
  if(!s) return;
  window.scrollTo(s.x, s.y);
  s.els.forEach(function(e){
    var el = document.getElementById(e.id);
    if(!el) return;
    if(e.atBottom){
      // User was following the tail — snap to the new bottom so freshly-
      // appended content stays in view.
      el.scrollTop = el.scrollHeight;
    } else {
      // Events are rendered newest-first (top→bottom). New events are
      // prepended at the top, pushing older content down.
      //
      // If the user was near the top (scroll position within 16px of 0),
      // keep them at the top so new events stay visible.  If the user is
      // scrolled down, preserve the distance from the bottom so their view
      // of older events stays fixed — the scrollbar moves down with the
      // new content.
      var distFromBottom = e.oldScrollHeight - el.clientHeight - e.top;
      if (e.top <= 16) {
        // User was at the top — stay at the top.
        el.scrollTop = 0;
      } else {
        // User is scrolled down — preserve distance from the bottom.
        el.scrollTop = el.scrollHeight - el.clientHeight - distFromBottom;
      }
      el.scrollLeft = e.left;
    }
  });
}

// Per-panel last-rendered fingerprint. If the data we just fetched is
// byte-identical to what's already on screen, the redraw is a no-op and
// scroll position is guaranteed untouched.
var lastSig = {};

// Event-driven panel refresh. Replaces the old 30s startAutoRefresh polling.
// fetcher() returns a Promise resolving to the panel's data; renderer(data)
// performs the actual DOM update synchronously. Both scroll preservation
// AND fingerprint dedup happen here so call sites stay one line.
async function refresh(panelKey, fetcher, renderer){
  if(isUserTyping()) return;
  var data;
  try { data = await fetcher(); } catch(e) { return; }
  var sig;
  try { sig = JSON.stringify(data); } catch(e) { sig = String(Math.random()); }
  if(lastSig[panelKey] === sig) return;
  lastSig[panelKey] = sig;
  var s = saveScroll();
  try { renderer(data); } finally { restoreScroll(s); }
}

// Invalidate a panel's fingerprint so the next refresh() call always renders.
// Used when the user navigates away and back to the same panel — the DOM
// may have been swapped out, so the cached sig is meaningless.
function invalidatePanel(panelKey){ delete lastSig[panelKey]; }

// Existing call-site compatibility shims. The old 30s polling is gone; what
// each showX() actually does is register its refresh closure here, and SSE
// event handlers fire it via refreshActive(panelKey). isUserTyping + scroll
// preservation happen in the wrapper.
function startAutoRefresh(fn){ panelRefreshers[activeView] = fn; }
function stopAutoRefresh(){
  // Clear the per-panel fingerprint cache so re-entering a panel always
  // does a fresh render (the DOM was wiped during navigation).
  lastSig = {};
}

// Fire the registered refresher for one panel, preserving scroll. Called by
// SSE handlers. Closures registered via startAutoRefresh are typically a
// fetch+render monolith — they can't be deduped via JSON fingerprint here;
// for that, use refresh(panelKey, fetcher, renderer) directly in the showX.
//
// Serialized per panel: if a refresh is in flight for this panelKey and
// another SSE event triggers one, we record a pending bit and let the
// in-flight one re-run once when it finishes. Without serialization, two
// concurrent refreshes race — the SECOND saveScroll() can fire in the gap
// between the first refresh's innerHTML write (where scrollY momentarily
// reads 0 because the panel has just been gutted) and the first
// restoreScroll. That captures y=0 as "the user's scroll position" and the
// second restoreScroll then snaps the panel back to the top on every
// event, which is exactly what the user was seeing on the activity panel.
var refreshActiveInFlight = {};
var refreshActivePending  = {};
async function refreshActive(panelKey){
  if(activeView !== panelKey) return;
  var fn = panelRefreshers[panelKey];
  if(!fn) return;
  if(isUserTyping()) return;
  if(refreshActiveInFlight[panelKey]){
    refreshActivePending[panelKey] = true;
    return;
  }
  refreshActiveInFlight[panelKey] = true;
  try {
    do {
      refreshActivePending[panelKey] = false;
      if(activeView !== panelKey) break;
      // When the activity panel is showing a spawn stream (or global stream),
      // the inner renderers handle their own scroll preservation.  Wrapping
      // them here would capture the pre-render position and overwrite the
      // inner restore, causing the scrollbar to jump back to the top.
      if(panelKey==='activity' && activityStream && activityStream.spawnId){
        var r = fn();
        if(r && typeof r.then === 'function') await r;
      } else if(panelKey==='activity' && globalStream && globalStream.open){
        var r = fn();
        if(r && typeof r.then === 'function') await r;
      } else {
        var s = saveScroll();
        try {
          var r2 = fn();
          if(r2 && typeof r2.then === 'function') await r2;
        } finally {
          restoreScroll(s);
        }
      }
    } while(refreshActivePending[panelKey]);
  } finally {
    refreshActiveInFlight[panelKey] = false;
  }
}

function toggleMore(e){
  e.stopPropagation();
  var dd=document.getElementById('more-dropdown');
  dd.classList.toggle('open');
  function closeHandler(){dd.classList.remove('open');document.removeEventListener('click',closeHandler)}
  if(dd.classList.contains('open')){setTimeout(function(){document.addEventListener('click',closeHandler)},0)}
}
function closeMore(){document.getElementById('more-dropdown').classList.remove('open')}

let evtSource=null;
let lastEvent=null;

function connectSSE(){
  if(evtSource){try{evtSource.close()}catch{}}
  evtSource=new EventSource('/api/events');
  evtSource.onopen=function(){sseConnected=true;renderLiveIndicator()};

  // ── Ticket lifecycle ────────────────────────────────────────────────
  // Dashboard (all-dept tickets overview) and Board (single dept's columns)
  // are the only views that care. Use refresh() so unchanged data short-
  // circuits and scroll position is preserved synchronously.
  evtSource.addEventListener('ticket-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='dashboard'){
      refresh('dashboard',
        function(){ return api('departments'); },
        function(d){ departments=d; renderDashboard(); });
    } else if(activeView==='board' && currentDept && lastEvent.department===currentDept){
      refresh('board:'+currentDept,
        function(){ return api('departments/'+currentDept+'/tickets'); },
        function(t){ boardTickets=t; renderBoardColumns(); });
    }
    // Sprint / verification panels read ticket state too; refresh if visible.
    if(activeView==='sprint')       refreshActive('sprint');
    if(activeView==='verification') refreshActive('verification');
  });

  // ── Chat ────────────────────────────────────────────────────────────
  evtSource.addEventListener('chat-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='chat') refreshActive('chat');
  });

  // ── Generic log directory changes (mostly the cycle logs) ───────────
  // Drives the Logs panel + the Activity cycle table.
  evtSource.addEventListener('log-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='logs')     refreshActive('logs');
    if(activeView==='activity') refreshActive('activity');
  });

  // ── Targeted cycle log writes (cycles.jsonl, spawns.log, sprint-lifecycle) ─
  evtSource.addEventListener('cycle-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='activity') refreshActive('activity');
    if(activeView==='logs')     refreshActive('logs');
    if(activeView==='rounds')   refreshActive('rounds');
  });

  // ── Per-spawn stream file writes (Activity drill-in viewer) ─────────
  evtSource.addEventListener('spawn-stream-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    // Only relevant when the Activity panel has drilled into a spawn stream.
    if(activeView==='activity' &&
       typeof activityStream==='object' && activityStream && activityStream.spawnId){
      refreshActive('activity');
    }
  });

  // ── Process registry (active agent spawns + daemons) ────────────────
  // Drives the activity panel, the roster (live agent state), the
  // processes view, and the company-status pill.
  evtSource.addEventListener('process-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    refreshCompanyStatus();
    if(activeView==='activity')  refreshActive('activity');
    if(activeView==='roster')    refreshActive('roster');
    if(activeView==='processes') refreshActive('processes');
  });

  // ── Sprint state ────────────────────────────────────────────────────
  evtSource.addEventListener('sprint-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='sprint')    refreshActive('sprint');
    if(activeView==='dashboard') refreshActive('dashboard'); // release banner
  });

  // ── Meetings ────────────────────────────────────────────────────────
  evtSource.addEventListener('meeting-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='meetings') refreshActive('meetings');
  });

  // ── Reports ─────────────────────────────────────────────────────────
  evtSource.addEventListener('report-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='reports')   refreshActive('reports');
    if(activeView==='marketing') refreshActive('marketing');
  });

  // ── Roster + org chart ──────────────────────────────────────────────
  evtSource.addEventListener('roster-change',function(e){
    lastEvent=JSON.parse(e.data);
    updateLiveIndicator(lastEvent);
    if(activeView==='org')    refreshActive('org');
    if(activeView==='roster') refreshActive('roster');
  });

  evtSource.onerror=function(){
    sseConnected=false;
    hiveStartedAt=0;  // clear stale uptime; refreshed by next checkStatus()/doConnect()
    renderLiveIndicator();
    setTimeout(connectSSE,5000);
  };
}

var lastEventDept='';

function updateLiveIndicator(evt){
  lastEventDept=(evt && evt.department) ? evt.department : '';
  renderLiveIndicator();
}

function renderLiveIndicator(){
  var el=document.getElementById('live-indicator');
  if(!el)return;
  if(!sseConnected){
    el.innerHTML='<span style="color:#f85149">disconnected</span>';
    return;
  }
  var dept=lastEventDept?' ['+lastEventDept+']':'';
  var ago='';
  if(hiveStartedAt>0){
    var nowSec=Math.floor(Date.now()/1000);
    var diff=Math.max(0,nowSec-hiveStartedAt);
    ago=' · '+fmtElapsed(diff)+' ago';
  }
  el.innerHTML='<span class="dot-active" style="display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:4px;vertical-align:middle"></span>'+
    '<span>live'+dept+ago+'</span>';
}

// 1-second tick: re-render every DOM element marked with data-elapsed-from
// (activity table rows, spawn-stream header) plus the header live indicator.
// Cheap because each tick is just a querySelectorAll + textContent write per
// matching element — no innerHTML rewrites of whole panels, no fetches, no
// scroll disruption. Started once on app load; gates on the connected flag.
function tickAsync(){
  if(typeof connected!=='undefined' && !connected)return;
  renderLiveIndicator();
  var nowSec=Math.floor(Date.now()/1000);
  var els=document.querySelectorAll('[data-elapsed-from]');
  for(var i=0;i<els.length;i++){
    var startTs=parseInt(els[i].getAttribute('data-elapsed-from'),10);
    if(!startTs||isNaN(startTs))continue;
    els[i].textContent=fmtElapsed(Math.max(0,nowSec-startTs));
  }
}
setInterval(tickAsync,1000);

async function checkStatus(){
  var s=await api('status');
  if(s.connected){
    connected=true;
    hiveStartedAt=s.started_at||0;
    document.getElementById('nav').style.display='flex';
    document.getElementById('company-control').style.display='inline-block';
    document.getElementById('company-path').textContent=s.path;
    connectSSE();
    await loadDepartments();
    refreshCompanyStatus();
    // No setInterval — process-change SSE handler keeps the company-status
    // pill fresh whenever processes.json is rewritten.
    showDashboard();
  } else {
    showConnect();
  }
}

function showConnect(){
  stopAutoRefresh();
  connected=false;
  sseConnected=false;
  document.getElementById('nav').style.display='none';
  const cc=document.getElementById('company-control'); if(cc) cc.style.display='none';
  document.getElementById('company-path').textContent='';
  var recent=getRecent();
  document.getElementById('content').innerHTML=
    '<div class="connect-screen"><div class="connect-box">'+
    '<h2>Connect to a Project</h2>'+
    '<p>Enter the path to a directory containing a .company folder</p>'+
    '<div class="error" id="connect-error"></div>'+
    '<input id="connect-path" placeholder="/path/to/your/project" onkeydown="if(event.key===\\'Enter\\')doConnect()">'+
    '<button class="btn btn-primary" style="width:100%" onclick="doConnect()">Connect</button>'+
    (recent.length?'<div class="recent"><h4>Recent Projects</h4>'+recent.map(function(r){
      return '<div class="recent-item" onclick="connectTo(\\''+esc(r)+'\\')">'+esc(r)+'</div>';
    }).join('')+'</div>':'')+
    '</div></div>';
  document.getElementById('connect-path').focus();
}

async function doConnect(){
  var p=document.getElementById('connect-path').value.trim();
  if(!p)return;
  var err=document.getElementById('connect-error');
  err.style.display='none';
  var r=await api('connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:p})});
  if(r.error){err.textContent=r.error;err.style.display='block';return}
  addRecent(p);
  connected=true;
  hiveStartedAt=r.started_at||0;
  document.getElementById('nav').style.display='flex';
  document.getElementById('company-path').textContent=r.path;
  connectSSE();
  await loadDepartments();
  showDashboard();
}

async function connectTo(p){
  document.getElementById('connect-path').value=p;
  doConnect();
}

// ── Hive company control (start / stop / verification) ─────────────────────
async function startCompany(){
  const btn=document.getElementById('btn-start-company');
  btn.disabled=true; btn.textContent='Starting...';
  try {
    const r=await api('company/start',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(r.error){alert('Start failed: '+(r.error.message||r.error));}
    else{document.getElementById('company-status-pill').textContent='status: running';}
  } catch(e){alert('Start failed: '+e.message);}
  finally{btn.disabled=false; btn.textContent='▶ Start Company';}
  refreshCompanyStatus();
}

async function stopCompany(){
  if(!confirm('Stop all company daemons?')) return;
  const btn=document.getElementById('btn-stop-company');
  btn.disabled=true; btn.textContent='Stopping...';
  try {
    const r=await api('company/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    document.getElementById('company-status-pill').textContent='status: stopped';
  } catch(e){alert('Stop failed: '+e.message);}
  finally{btn.disabled=false; btn.textContent='■ Stop Company';}
  refreshCompanyStatus();
}

async function refreshCompanyStatus(){
  try {
    const procs=await api('processes');
    const pill=document.getElementById('company-status-pill');
    if(procs && procs.processes && procs.processes.length){
      const alive=procs.processes.filter(p=>p.alive!==false).length;
      pill.textContent='status: '+alive+'/'+procs.processes.length+' processes alive';
    } else {
      pill.textContent='status: stopped';
    }
  } catch{}
}

async function approveTicket(dept, ticketId){
  const verifier=prompt('Verifier name?');
  if(!verifier) return;
  const rationale=prompt('Approval rationale (>= 30 chars - describe what you actually checked):');
  if(!rationale || rationale.length<30){alert('Rationale must be at least 30 characters'); return;}
  const r=await api('departments/'+dept+'/tickets/'+encodeURIComponent(ticketId)+'/approve',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({verifier, rationale})
  });
  if(r.error){alert('Approve failed: '+(r.error.message||r.error));}
  else{alert('Approved. You can now move this ticket to done.');}
}

async function runGates(dept){
  const r=await api('gates/'+dept+'/run',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  alert('Gates run for '+dept+': '+(r.error ? 'ERROR '+(r.error.message||r.error) : 'see verification view'));
}

function showVerification(){
  activeView='verification';
  setActiveNavByLabel('Verification');
  renderVerification();
  startAutoRefresh(renderVerification);
}

function _esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

async function renderVerification(){
  const main=document.getElementById('main');
  if(main) main.innerHTML='<h2>Verification Queue</h2><div id="verif-content">Loading...</div>';
  // Some templates use #content not #main - fall back
  const content=document.getElementById('content');
  if(content && !main) content.innerHTML='<h2>Verification Queue</h2><div id="verif-content">Loading...</div>';

  // Fetch current round to show active verification entries (for status badges)
  var roundData=null;
  try { roundData = await api('rounds/current'); } catch(e){}
  var activeVerif={};  // ticket -> entry
  if(roundData && roundData.entries){
    for(var i=0;i<roundData.entries.length;i++){
      var e=roundData.entries[i];
      if(e.kind==='verification' && (e.status==='queued'||e.status==='running')){
        activeVerif[e.ticket]=e;
      }
    }
  }

  await loadDepartments();
  const rows=[];
  for (const d of (departments||[])){
    try {
      const tickets=await api('departments/'+encodeURIComponent(d.name)+'/tickets');
      for (const t of (tickets||[])) {
        if(t._status_dir!=='in-review') continue;
        const gp=t.gates_passed?'✓':'—';
        const vb=t.verified_by?('✓ '+_esc(t.verified_by)):'—';
        // Live status badge for this ticket's verification run
        var verifStatus='';
        var verifEntry=activeVerif[t.id];
        if(verifEntry){
          if(verifEntry.status==='running'){
            verifStatus='<span style="color:#d29922;font-size:11px;margin-right:6px" title="Verification '+_esc(verifEntry.status)+'">● running</span>';
          } else {
            verifStatus='<span style="color:#8b949e;font-size:11px;margin-right:6px" title="Verification queued">○ queued</span>';
          }
        }
        rows.push(
          '<tr style="border-top:1px solid #30363d">'+
            '<td style="padding:6px">'+_esc(d.name)+'</td>'+
            '<td><code>'+_esc(t.id)+'</code></td>'+
            '<td>'+_esc(t.title||'')+'</td>'+
            '<td>'+gp+'</td>'+
            '<td>'+vb+'</td>'+
            '<td>'+
              verifStatus+
              '<button class="vbtn-gates" data-dept="'+_esc(d.name)+'" style="margin-right:4px">Run gates</button>'+
              '<button class="vbtn-approve" data-dept="'+_esc(d.name)+'" data-ticket="'+_esc(t.id)+'">Approve</button>'+
              '<button class="vbtn-watch" data-ticket="'+_esc(t.id)+'" style="margin-left:4px">View</button>'+
            '</td>'+
          '</tr>'
        );
      }
    } catch(e){}
  }
  var html='<table style="width:100%;border-collapse:collapse;">'+
    '<thead><tr><th align="left">Dept</th><th align="left">Ticket</th><th align="left">Title</th>'+
    '<th align="left">Gates</th><th align="left">Approved</th><th>Actions</th></tr></thead>'+
    '<tbody>'+(rows.length?rows.join(''):'<tr><td colspan="6" style="padding:12px;color:#8b949e">No tickets in in-review.</td></tr>')+'</tbody></table>';
  var target=document.getElementById('verif-content');
  if(target){
    target.innerHTML=html;
    // Attach handlers via delegation - avoids the nested-quote escaping mess
    target.querySelectorAll('.vbtn-gates').forEach(b=>{
      b.addEventListener('click',()=>runGates(b.dataset.dept));
    });
    target.querySelectorAll('.vbtn-approve').forEach(b=>{
      b.addEventListener('click',()=>approveTicket(b.dataset.dept,b.dataset.ticket));
    });
    target.querySelectorAll('.vbtn-watch').forEach(b=>{
      b.addEventListener('click',()=>openGateOutput(b.dataset.ticket));
    });
  }
}

function setActiveNavByLabel(label){
  for (const b of document.querySelectorAll('#nav button')){
    if(b.textContent.trim()===label) b.classList.add('active'); else b.classList.remove('active');
  }
}
// ───────────────────────────────────────────────────────────────────────────

async function loadDepartments(){departments=await api('departments')}

function showDashboard(){
  activeView='dashboard';
  currentDept=null;currentChannel=null;currentMeeting=null;
  setActiveNav(0);
  renderDashboard();
  startAutoRefresh(function(){loadDepartments().then(renderDashboard)});
}

function renderDashboard(){
  var c=document.getElementById('content');
  if(!departments.length){c.innerHTML='<p style="color:#8b949e;padding:40px;text-align:center">No departments found. Run the company skill to create a project first.</p>';return}
  c.innerHTML='<div style="margin-bottom:16px;display:flex;align-items:center"><span style="font-size:16px;color:#f0f6fc">All Departments</span><button class="btn btn-primary" style="margin-left:auto" onclick="showCreate()">+ New Ticket</button></div>'+'<div class="dashboard">'+departments.map(function(d){
    var active=(d.counts['in-progress']||0)+(d.counts['in-review']||0);
    return '<div class="dept-card" onclick="showBoard(\\''+d.name+'\\')">'+
      '<h3>'+esc(d.prefix)+' &middot; '+esc(d.name)+'</h3>'+
      '<div class="counts">'+
        '<span class="badge badge-backlog">backlog '+d.counts.backlog+'</span>'+
        '<span class="badge badge-sprint">sprint '+d.counts['sprint-current']+'</span>'+
        '<span class="badge badge-progress">active '+active+'</span>'+
        '<span class="badge badge-done">done '+d.counts.done+'</span>'+
      '</div></div>';
  }).join('')+'</div>';
}

var boardTickets=[];
var boardDept='';
var boardFilter={search:'',type:'',priority:''};

async function showBoard(dept){
  activeView='board';
  currentDept=dept;
  boardDept=dept;
  boardFilter={search:'',type:'',priority:''};
  setActiveNav(-1);
  boardTickets=await api('departments/'+dept+'/tickets');
  renderBoard();
  startAutoRefresh(async function(){boardTickets=await api('departments/'+boardDept+'/tickets');renderBoardColumns()});
}

function boardFilterChange(field,val){boardFilter[field]=val;renderBoardColumns()}

function renderBoard(){
  var dept=boardDept;
  var allTypes={},allPris={};
  for(var i=0;i<boardTickets.length;i++){allTypes[boardTickets[i].type||'?']=1;allPris[boardTickets[i].priority||'?']=1}

  var c=document.getElementById('content');
  var html='<div style="margin-bottom:12px"><button class="btn" onclick="showDashboard()">&larr; All Departments</button>'+
    ' <span style="font-size:16px;color:#f0f6fc;margin-left:12px">'+esc(dept)+'</span>'+
    ' <button class="btn" style="margin-left:8px" onclick="showCreateFor(\\''+dept+'\\')">+ Ticket</button></div>';

  html+='<div class="filter-bar">';
  html+='<input id="board-search" placeholder="Search tickets..." value="'+esc(boardFilter.search)+'" oninput="boardFilterChange(\\'search\\',this.value)">';
  html+='<select onchange="boardFilterChange(\\'type\\',this.value)"><option value="">All Types</option>';
  Object.keys(allTypes).sort().forEach(function(t){html+='<option'+(boardFilter.type===t?' selected':'')+'>'+esc(t)+'</option>'});
  html+='</select>';
  html+='<select onchange="boardFilterChange(\\'priority\\',this.value)"><option value="">All Priorities</option>';
  ['critical','high','medium','low'].forEach(function(p){if(allPris[p])html+='<option'+(boardFilter.priority===p?' selected':'')+'>'+esc(p)+'</option>'});
  html+='</select>';
  if(boardFilter.search||boardFilter.type||boardFilter.priority){
    html+='<span class="filter-clear" onclick="boardFilter={search:\\'\\',type:\\'\\',priority:\\'\\'};renderBoardColumns()">Clear</span>';
  }
  html+='<span class="filter-count" id="board-filter-count"></span>';
  html+='</div>';
  html+='<div id="board-columns"></div>';
  c.innerHTML=html;
  renderBoardColumns();
}

function renderBoardColumns(){
  var dept=boardDept;
  var filtered=boardTickets.filter(function(t){
    if(boardFilter.search){
      var s=boardFilter.search.toLowerCase();
      var hay=((t.id||'')+(t.title||'')+(t.description||'')).toLowerCase();
      if(hay.indexOf(s)<0)return false;
    }
    if(boardFilter.type&&t.type!==boardFilter.type)return false;
    if(boardFilter.priority&&t.priority!==boardFilter.priority)return false;
    return true;
  });
  var statuses=['backlog','sprint-current','in-progress','in-review','done'];
  var labels={backlog:'Backlog','sprint-current':'Sprint','in-progress':'In Progress','in-review':'In Review',done:'Done'};
  var el=document.getElementById('board-columns');
  if(!el)return;
  el.innerHTML='<div class="board">'+statuses.map(function(s){
    var col=filtered.filter(function(t){return t._status_dir===s});
    return '<div class="column"><div class="column-header">'+labels[s]+' <span class="count">'+col.length+'</span></div>'+
      '<div class="column-body">'+col.map(function(t){
        // For tickets in done, surface WHY (verified | rejected | merge-failed | etc.)
        // so the operator can distinguish "shipped" from "won't-do" at a glance.
        var doneReason='';
        if(s==='done'){
          var r=(t.resolution&&t.resolution.status)||t.done_reason||'';
          if(r){
            var color=(r==='rejected')?'#f85149':(r==='implemented'||r==='verified')?'#3fb950':'#d29922';
            doneReason='<span style="font-size:10px;color:'+color+';background:#0d1117;border:1px solid '+color+';padding:1px 5px;border-radius:3px">'+esc(r)+'</span>';
          }
        }
        return '<div class="ticket-card" onclick="showTicket(\\''+dept+'\\',\\''+esc(t.id)+'\\')">'+
          '<div class="ticket-id">'+esc(t.id)+'</div>'+
          '<div class="ticket-title">'+esc(t.title)+'</div>'+
          '<div class="ticket-meta">'+
            '<span class="type-badge">'+esc(t.type)+'</span>'+
            '<span class="priority-'+esc(t.priority)+'">'+esc(t.priority)+'</span>'+
            doneReason+
            (t.assignee&&t.assignee.name?'<span style="font-size:11px;color:#8b949e">'+esc(t.assignee.name)+'</span>':'')+
          '</div></div>';
      }).join('')+'</div></div>';
  }).join('')+'</div>';
  var fc=document.getElementById('board-filter-count');
  if(fc)fc.textContent=filtered.length+' of '+boardTickets.length+' tickets';
}

async function showTicket(dept,id){
  var t=await api('departments/'+dept+'/tickets/'+encodeURIComponent(id));
  var m=document.getElementById('modal');
  var statusOpts=['backlog','sprint-current','in-progress','in-review','done'].map(function(s){
    return '<option'+(s===t.status?' selected':'')+'>'+s+'</option>';}).join('');
  // Status display includes the resolution reason for done tickets so the
  // operator can tell "verified / implemented" from "rejected" without
  // opening the resolution block below.
  var statusDisplay=t.status;
  if(t.status==='done'){
    var rr=(t.resolution&&t.resolution.status)||t.done_reason||'';
    if(rr){
      var rcolor=(rr==='rejected')?'#f85149':(rr==='implemented'||rr==='verified')?'#3fb950':'#d29922';
      statusDisplay='done <span style="color:'+rcolor+';font-weight:600">('+esc(rr)+')</span>';
    }
  }
  m.innerHTML='<h2>'+esc(t.id)+': '+esc(t.title)+'</h2>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">'+
      df('Type',t.type)+df('Priority','<span class="priority-'+t.priority+'">'+t.priority+'</span>')+
      df('Status',statusDisplay+' <select class="status-select" onchange="moveTicket(\\''+dept+'\\',\\''+esc(t.id)+'\\',this.value)">'+statusOpts+'</select>')+
      df('Department',t.target_department)+
      df('Created',localTime(t.created_at))+df('Assignee',t.assignee&&t.assignee.name?t.assignee.name:'Unassigned')+
      df('Sprint',t.sprint||'None')+df('Estimate',t.estimate||'None')+
      df('Filed by',t.filed_by_department||'?')+df('Source',t.source||'?')+
    '</div>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">'+
      df('Bounces','<span class="bounce-count" style="color:'+bounceColor(t.bounce_count||0)+';font-weight:600">'+(t.bounce_count||0)+' / 5</span>')+
    '</div>'+
    (t.description?'<div class="detail-field"><div class="label">Description</div><div class="value md-content">'+renderMarkdown(t.description)+'</div></div>':'')+
    (t.acceptance_criteria&&t.acceptance_criteria.length?'<div class="detail-field"><div class="label">Acceptance Criteria</div><div class="value">'+t.acceptance_criteria.map(function(a){return '<div>- '+esc(a)+'</div>'}).join('')+'</div></div>':'')+
    (t.dependencies&&t.dependencies.length?'<div class="detail-field"><div class="label">Dependencies</div><div class="value">'+t.dependencies.map(function(d){return esc(d)}).join(', ')+'</div></div>':'')+
    (t.resolution&&(t.resolution.git_branch||t.resolution.commit||t.resolution.resolved_by)?'<div class="detail-field"><div class="label">Resolution</div><div class="value">'+(t.resolution.git_branch?'Branch: <a href="#" onclick="closeModal();showBranchHistory(\\''+esc(t.resolution.git_branch)+'\\');return false" style="color:#58a6ff;cursor:pointer">'+esc(t.resolution.git_branch)+'</a> ':'')+(t.resolution.commit?'Commit: <a href="#" onclick="closeModal();showCommitDiff(\\''+esc(t.resolution.commit)+'\\');return false" style="color:#58a6ff;cursor:pointer;font-family:monospace">'+esc(t.resolution.commit)+'</a> ':'')+(t.resolution.resolved_by?' by '+esc(t.resolution.resolved_by):'')+(t.resolution.resolved_at?' at '+localTime(t.resolution.resolved_at):'')+'</div></div>':'')+
    '<div class="detail-field"><div class="label">Comments ('+((t.comments||[]).length)+')</div>'+
      (t.comments||[]).map(function(c){return '<div class="comment"><div class="comment-author">'+esc(c.author||'system')+' <span style="color:#8b949e">'+localTime(c.timestamp||c.time)+'</span></div><div class="comment-body md-content">'+renderMarkdown(c.body||c.text||'')+'</div></div>'}).join('')+
    '</div>'+
    '<div style="margin-top:12px"><label>Add Comment</label><textarea id="new-comment" rows="2"></textarea></div>'+
    '<div class="actions">'+
      '<button class="btn" onclick="closeModal()">Close</button>'+
      '<button class="btn btn-primary" onclick="addComment(\\''+dept+'\\',\\''+esc(t.id)+'\\')">Comment</button>'+
    '</div>';
  openModal();
}

async function moveTicket(dept,id,newStatus){
  await api('departments/'+dept+'/tickets/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:newStatus})});
  closeModal();
  showBoard(dept);
}

async function addComment(dept,id){
  var body=document.getElementById('new-comment').value.trim();
  if(!body)return;
  await api('departments/'+dept+'/tickets/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({comment:body})});
  showTicket(dept,id);
}

function showCreate(){showCreateFor(departments[0]?departments[0].name:'')}
async function showCreateFor(defaultDept){
  var m=document.getElementById('modal');
  var types=await api('ticket-types');
  var deptOpts=departments.map(function(d){return '<option value="'+d.name+'"'+(d.name===defaultDept?' selected':'')+'>'+d.name+'</option>'}).join('');
  var typeOpts=types.map(function(t){return '<option value="'+esc(t)+'">'}).join('');
  m.innerHTML='<h2>Create Ticket</h2>'+
    '<label>Department</label><select id="c-dept">'+deptOpts+'</select>'+
    '<label>Type</label><input id="c-type" list="type-list" value="feature" placeholder="Type or select..." style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:8px 12px;border-radius:6px;font-family:inherit;font-size:13px"><datalist id="type-list">'+typeOpts+'</datalist>'+
    '<label>Priority</label><select id="c-priority"><option>medium</option><option>critical</option><option>high</option><option>low</option></select>'+
    '<label>Title</label><input id="c-title" placeholder="Short descriptive title">'+
    '<label>Description</label><textarea id="c-desc" placeholder="Detailed description..."></textarea>'+
    '<label>Acceptance Criteria (one per line)</label><textarea id="c-ac" rows="3" placeholder="Criterion 1"></textarea>'+
    '<label>Labels (comma-separated)</label><input id="c-labels" placeholder="auth, api, urgent">'+
    '<div class="actions"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="createTicket()">Create Ticket</button></div>';
  openModal();
  setTimeout(function(){document.getElementById('c-title').focus()},100);
}

async function createTicket(){
  var dept=document.getElementById('c-dept').value;
  var acText=document.getElementById('c-ac').value;
  var labelsText=document.getElementById('c-labels').value;
  var data={
    type:document.getElementById('c-type').value,
    priority:document.getElementById('c-priority').value,
    title:document.getElementById('c-title').value,
    description:document.getElementById('c-desc').value,
    acceptance_criteria:acText.split('\\n').map(function(s){return s.trim()}).filter(Boolean),
    labels:labelsText.split(',').map(function(s){return s.trim()}).filter(Boolean),
  };
  if(!data.title){alert('Title is required');return}
  // Surface failures instead of silently closing the modal. Old behavior
  // assumed the call always succeeded; the script could reject (bad type,
  // missing dept, MCP server down, etc.) and the user would see nothing.
  var t;
  try {
    t=await api('departments/'+dept+'/tickets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  } catch(e) {
    alert('Create failed: network or server error.\\n\\n'+(e&&e.message?e.message:e));
    return;
  }
  if(t&&(t.error||t.ok===false)){
    var msg=t.error||t.stderr||t.stdout||'unknown error';
    alert('Create failed:\\n\\n'+msg);
    return;
  }
  closeModal();
  if(currentDept===dept)showBoard(dept);
  else{await loadDepartments();renderDashboard()}
}

// ===== CHAT =====
var currentChannel=null;

async function showChats(){
  activeView='chat';
  stopAutoRefresh();
  currentDept=null;
  setActiveNav(1);
  var channels=await api('chat');
  renderChatLayout(channels);
  if(channels.length)showChannel(channels[0].name);
  startAutoRefresh(async function(){if(currentChannel)showChannel(currentChannel)});
}

function renderChatLayout(channels){
  var c=document.getElementById('content');
  c.innerHTML='<div class="chat-layout">'+
    '<div class="channel-list" id="channel-list">'+
      channels.map(function(ch){
        return '<div class="channel-item" data-ch="'+esc(ch.name)+'" onclick="showChannel(\\''+esc(ch.name)+'\\')">'+
          '<span class="ch-name"># '+esc(ch.name)+'</span>'+
          '<span class="ch-count">'+ch.messageCount+'</span>'+
        '</div>';
      }).join('')+
      (channels.length?'':'<div class="chat-empty">No chat channels yet</div>')+
    '</div>'+
    '<div class="chat-view" id="chat-view"><div class="chat-empty">Select a channel</div></div>'+
  '</div>';
}

async function showChannel(name){
  currentChannel=name;
  document.querySelectorAll('.channel-item').forEach(function(el){
    el.classList.toggle('active',el.dataset.ch===name);
  });
  var data=await api('chat/'+encodeURIComponent(name));
  var view=document.getElementById('chat-view');
  if(!data.messages||!data.messages.length){
    view.innerHTML='<div class="chat-header"># '+esc(name)+'</div><div class="chat-messages"><div class="chat-empty">No messages in this channel yet</div></div>';
    return;
  }
  view.innerHTML='<div class="chat-header"># '+esc(name)+' <span style="font-weight:normal;font-size:12px;color:#8b949e;margin-left:8px">'+data.messages.length+' messages</span></div>'+
    '<div class="chat-messages" id="chat-msgs">'+
      data.messages.map(function(m){
        return '<div class="chat-msg">'+
          '<div class="msg-header"><span class="msg-author">'+esc(m.author||'system')+'</span><span class="msg-time">'+localShort(m.timestamp)+'</span></div>'+
          '<div class="msg-body md-content">'+renderMarkdown(m.body||'')+'</div>'+
        '</div>';
      }).join('')+
    '</div>';
  var msgs=document.getElementById('chat-msgs');
  if(msgs)msgs.scrollTop=msgs.scrollHeight;
}

// ===== MEETINGS =====
var currentMeeting=null;

var mtgData=[];
var mtgSchedule={};
var mtgFilter='';
var mtgSort='newest';

async function showMeetings(){
  activeView='meetings';
  stopAutoRefresh();
  currentDept=null;
  currentChannel=null;
  setActiveNav(2);
  var data=await api('meetings');
  mtgData=data.meetings||[];
  mtgSchedule=data.schedule||{};
  mtgFilter='';
  renderMeetingsLayout();
  if(mtgData.length)showMeeting(mtgData[0].file);
}

function mtgFilterChange(val){mtgFilter=val;renderMeetingList()}
function mtgSortChange(val){mtgSort=val;renderMeetingList()}

function renderMeetingsLayout(){
  var c=document.getElementById('content');
  var s=mtgSchedule;
  var scheduleHtml=s?'<div class="schedule-box"><h4>Meeting Schedule</h4>'+
    '<div class="sch-row">'+
      '<div class="sch-icon sch-icon-standup">S</div>'+
      '<div class="sch-info"><div class="sch-label">Daily Standup <span class="sch-badge" style="background:#1f3a5f;color:#58a6ff">'+esc(s.standup_cadence||'daily')+'</span></div>'+
      '<div class="sch-time">Last: '+(s.last_standup?localTime(s.last_standup):'Never')+'</div></div></div>'+
    '<div class="sch-row">'+
      '<div class="sch-icon sch-icon-sprint">R</div>'+
      '<div class="sch-info"><div class="sch-label">Sprint Review <span class="sch-badge" style="background:#1f3f2a;color:#3fb950">'+esc(s.sprint_review_cadence||'weekly')+'</span></div>'+
      '<div class="sch-time">Last: '+(s.last_sprint_review?localTime(s.last_sprint_review):'Never')+'</div></div></div>'+
    (s.current_sprint?'<div class="sch-row"><div class="sch-icon" style="background:#2a1f3f;color:#bc8cff">A</div><div class="sch-info"><div class="sch-label">Active Sprints</div><div class="sch-time">'+esc(s.current_sprint).split(',').join(', ')+'</div></div></div>':'')+
    (s.notes?'<div class="sch-notes">'+esc(s.notes)+'</div>':'')+
    '</div>':'';
  c.innerHTML='<div class="meetings-layout">'+
    '<div class="meeting-list">'+
      scheduleHtml+
      '<div style="padding:8px"><input id="mtg-search" placeholder="Search meetings..." oninput="mtgFilterChange(this.value)" style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:4px 8px;border-radius:4px;font-family:inherit;font-size:12px;margin-bottom:4px">'+
      '<select onchange="mtgSortChange(this.value)" style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:4px 8px;border-radius:4px;font-family:inherit;font-size:11px">'+
        '<option value="newest"'+(mtgSort==='newest'?' selected':'')+'>Newest first</option>'+
        '<option value="oldest"'+(mtgSort==='oldest'?' selected':'')+'>Oldest first</option>'+
        '<option value="title"'+(mtgSort==='title'?' selected':'')+'>By title</option>'+
      '</select></div>'+
      '<div id="mtg-list-items"></div>'+
    '</div>'+
    '<div class="meeting-view" id="meeting-view"><div class="meeting-empty">Select a meeting</div></div>'+
  '</div>';
  renderMeetingList();
}

function renderMeetingList(){
  var filtered=mtgData.filter(function(m){
    if(!mtgFilter)return true;
    var s=mtgFilter.toLowerCase();
    return (m.title||'').toLowerCase().indexOf(s)>=0||(m.file||'').toLowerCase().indexOf(s)>=0||(m.date||'').indexOf(s)>=0;
  });
  if(mtgSort==='newest')filtered.sort(function(a,b){return (b.modified||b.date||'').localeCompare(a.modified||a.date||'')});
  else if(mtgSort==='oldest')filtered.sort(function(a,b){return (a.modified||a.date||'').localeCompare(b.modified||b.date||'')});
  else if(mtgSort==='title')filtered.sort(function(a,b){return (a.title||'').localeCompare(b.title||'')});
  var el=document.getElementById('mtg-list-items');
  if(!el)return;
  if(!filtered.length){el.innerHTML='<div class="meeting-empty">No meetings match</div>';return}
  el.innerHTML=filtered.map(function(m){
    var active=currentMeeting===m.file?' style="background:#21262d;border-left:2px solid #58a6ff"':'';
    return '<div class="meeting-item"'+active+' data-file="'+esc(m.file)+'" onclick="showMeeting(\\''+esc(m.file)+'\\')">'+
      '<div class="mt-title">'+esc(m.title)+'</div>'+
      '<div class="mt-date">'+localTime(m.modified)+'</div>'+
    '</div>';
  }).join('');
}

async function showMeeting(file){
  currentMeeting=file;
  renderMeetingList();
  var data=await api('meetings/'+encodeURIComponent(file));
  var view=document.getElementById('meeting-view');
  view.innerHTML='<div style="padding:12px 24px 0;font-size:11px;color:#8b949e;border-bottom:1px solid #21262d;margin-bottom:8px;padding-bottom:8px">'+localTime(data.modified)+'</div><div class="meeting-content">'+renderMarkdown(data.content)+'</div>';
}

// Block-aware Markdown → HTML. Handles GFM tables (the prior renderer
// dropped them entirely, leaving a flat <br>-mashed mess in any reports
// or comments containing tables). Strategy:
//   1. Pull out fenced code blocks first so their contents aren't munged
//      by paragraph / table / list parsing. Replace with placeholders,
//      reinsert at the end.
//   2. Walk lines and group them into blocks: heading, hr, blockquote,
//      table (header + separator + body), unordered list, ordered list,
//      or paragraph. Each block emits proper HTML; inline conversions
//      (bold, code, links) run last on the rendered string.
function renderMarkdown(md){
  if(!md)return '';
  var BT=String.fromCharCode(96);
  var fencedRe=new RegExp('^'+BT+BT+BT+'([a-zA-Z0-9_+-]*)\\\\n([\\\\s\\\\S]*?)\\\\n'+BT+BT+BT+'$','gm');
  var codeStash=[];
  md=String(md).replace(fencedRe,function(_,lang,code){
    var idx=codeStash.length;
    codeStash.push('<pre style="background:#21262d;padding:12px;border-radius:6px;overflow-x:auto;font-size:12px;margin:8px 0"><code>'+escHtml(code)+'</code></pre>');
    return '\\u0000CODEBLOCK'+idx+'\\u0000';
  });

  function escHtml(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
  function inline(s){
    var out=escHtml(s);
    // inline code first so the contents aren't bolded
    out=out.replace(new RegExp(BT+'([^'+BT+']+)'+BT,'g'),'<code>$1</code>');
    out=out.replace(/\\*\\*([^*]+)\\*\\*/g,'<strong style="color:#f0f6fc">$1</strong>');
    out=out.replace(/(?:^|[^*])\\*([^*\\n]+)\\*/g,function(m,t){return m.charAt(0)==='*'?m:m.charAt(0)+'<em>'+t+'</em>'});
    // [text](url)
    out=out.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g,'<a href="$2" style="color:#58a6ff" target="_blank" rel="noopener">$1</a>');
    return out;
  }

  var lines=String(md).split(/\\r?\\n/);
  var html='';
  var i=0;
  function isTableSep(s){return /^\\s*\\|?\\s*:?-{2,}:?\\s*(\\|\\s*:?-{2,}:?\\s*)+\\|?\\s*$/.test(s)}
  function splitRow(s){
    var t=s.trim().replace(/^\\|/,'').replace(/\\|$/,'');
    return t.split('|').map(function(c){return c.trim()});
  }
  while(i<lines.length){
    var line=lines[i];
    // Blank
    if(/^\\s*$/.test(line)){i++;continue}
    // Heading
    var hm=/^(#{1,6})\\s+(.*)$/.exec(line);
    if(hm){var lvl=hm[1].length;html+='<h'+lvl+' style="margin:14px 0 6px;color:#f0f6fc">'+inline(hm[2])+'</h'+lvl+'>';i++;continue}
    // HR
    if(/^\\s*-{3,}\\s*$/.test(line)||/^\\s*\\*{3,}\\s*$/.test(line)){html+='<hr style="border:none;border-top:1px solid #30363d;margin:10px 0">';i++;continue}
    // Blockquote
    if(/^\\s*>\\s?/.test(line)){
      var bq=[];
      while(i<lines.length&&/^\\s*>\\s?/.test(lines[i])){bq.push(lines[i].replace(/^\\s*>\\s?/,''));i++}
      html+='<blockquote style="border-left:3px solid #30363d;padding:4px 12px;margin:8px 0;color:#8b949e">'+inline(bq.join(' '))+'</blockquote>';
      continue;
    }
    // Table — current line has |, next is separator
    if(/\\|/.test(line)&&i+1<lines.length&&isTableSep(lines[i+1])){
      var header=splitRow(line);
      var bodyRows=[];
      i+=2;
      while(i<lines.length&&/\\|/.test(lines[i])&&!/^\\s*$/.test(lines[i])){
        bodyRows.push(splitRow(lines[i]));i++;
      }
      var t='<table style="border-collapse:collapse;margin:8px 0;font-size:12px">';
      t+='<thead><tr>'+header.map(function(c){return '<th style="border:1px solid #30363d;padding:5px 9px;background:#161b22;color:#f0f6fc;text-align:left">'+inline(c)+'</th>'}).join('')+'</tr></thead>';
      t+='<tbody>';
      for(var br=0;br<bodyRows.length;br++){
        t+='<tr>'+bodyRows[br].map(function(c){return '<td style="border:1px solid #30363d;padding:5px 9px;vertical-align:top">'+inline(c)+'</td>'}).join('')+'</tr>';
      }
      t+='</tbody></table>';
      html+=t;
      continue;
    }
    // Unordered list
    if(/^\\s*[-*+]\\s+/.test(line)){
      var ul='<ul style="margin:6px 0;padding-left:22px">';
      while(i<lines.length&&/^\\s*[-*+]\\s+/.test(lines[i])){
        ul+='<li>'+inline(lines[i].replace(/^\\s*[-*+]\\s+/,''))+'</li>';i++;
      }
      ul+='</ul>';html+=ul;continue;
    }
    // Ordered list
    if(/^\\s*\\d+\\.\\s+/.test(line)){
      var ol='<ol style="margin:6px 0;padding-left:22px">';
      while(i<lines.length&&/^\\s*\\d+\\.\\s+/.test(lines[i])){
        ol+='<li>'+inline(lines[i].replace(/^\\s*\\d+\\.\\s+/,''))+'</li>';i++;
      }
      ol+='</ol>';html+=ol;continue;
    }
    // Paragraph — gather consecutive non-blank lines
    var p=[line];i++;
    while(i<lines.length&&!/^\\s*$/.test(lines[i])
        &&!/^(#{1,6})\\s+/.test(lines[i])
        &&!/^\\s*[-*+]\\s+/.test(lines[i])
        &&!/^\\s*\\d+\\.\\s+/.test(lines[i])
        &&!/^\\s*>\\s?/.test(lines[i])
        &&!(/\\|/.test(lines[i])&&i+1<lines.length&&isTableSep(lines[i+1]))){
      p.push(lines[i]);i++;
    }
    html+='<p style="margin:6px 0">'+inline(p.join(' '))+'</p>';
  }

  // Reinsert code blocks
  html=html.replace(/\\u0000CODEBLOCK(\\d+)\\u0000/g,function(_,n){return codeStash[Number(n)]||''});
  return html;
}

// ===== 1. ORG CHART =====
var orgShowActiveOnly=false;

function toggleOrgActive(){orgShowActiveOnly=!orgShowActiveOnly;showOrgChart()}

function renderRosterAgents(agents){
  if(!agents||!agents.length)return '<div class="org-no-agents">No agents spawned yet</div>';
  if(orgShowActiveOnly)agents=agents.filter(function(a){return a.status==='active'});
  if(!agents.length)return '<div class="org-no-agents">No active agents</div>';
  var html='<div class="org-agents-list">';
  for(var a=0;a<agents.length;a++){
    var ag=agents[a];
    // dot-active = live process; dot-claimed = paper assignment, no process yet
    // (assignee written but ic-spawner hasn't launched it / failed to launch)
    // dot-retired = finished; dot-stale = idle/inactive
    var dotClass='dot-stale';
    if(ag.status==='active')   dotClass='dot-active';
    else if(ag.status==='claimed') dotClass='dot-claimed';
    else if(ag.status==='retired') dotClass='dot-retired';
    var statusBadge='';
    if(ag.status==='claimed') statusBadge='<span style="font-size:9px;color:#d29922;background:#382b00;padding:1px 5px;border-radius:3px;margin-left:4px" title="Ticket assignee written but no claude process yet — ic-spawner will launch one within 60s, or claude is queued at the local LLM">claimed</span>';
    var roleIcon='';
    var role=(ag.role||'').toLowerCase();
    if(role.indexOf('vp')===0||role.indexOf('vice')>=0)roleIcon='VP';
    else if(role.indexOf('manager')>=0||role.indexOf('mgr')>=0)roleIcon='MGR';
    else if(role.indexOf('ic')>=0||role.indexOf('engineer')>=0)roleIcon='IC';
    else if(role.indexOf('tester')>=0||role.indexOf('qa')>=0||role.indexOf('verif')>=0)roleIcon='QA';
    else roleIcon='AG';
    html+='<div class="org-agent">';
    html+='<span class="org-agent-icon">'+roleIcon+'</span>';
    html+='<span class="dot '+dotClass+'"></span>';
    html+='<span class="org-agent-role">'+esc(ag.role||'agent')+'</span>';
    html+=statusBadge;
    if(ag.generation&&ag.generation>1)html+='<span class="org-agent-gen">gen '+ag.generation+'</span>';
    if(ag.notes)html+='<span class="org-agent-notes">'+esc(ag.notes.substring(0,60))+'</span>';
    html+='</div>';
  }
  html+='</div>';
  return html;
}

async function showOrgChart(){
  activeView='org';
  stopAutoRefresh();
  setActiveNav(4);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading org chart...</p>';
  var data=await api('org-chart');
  if(data.error){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}

  var html='<div style="margin-bottom:16px;display:flex;align-items:center;gap:16px"><span style="font-size:18px;color:#f0f6fc">'+esc(data.company_name||data.project_name||'Company')+'</span>'+
    '<span style="font-size:11px;color:#8b949e">'+
    '<span class="dot" style="background:#58a6ff"></span> fixed '+
    '<span class="dot" style="background:#3fb950;margin-left:8px"></span> dynamic</span>'+
    '<button class="btn'+(orgShowActiveOnly?' btn-primary':'')+'" style="margin-left:auto;font-size:11px;padding:4px 10px" onclick="toggleOrgActive()">'+(orgShowActiveOnly?'Showing Active Only':'Show Active Only')+'</button>'+
    '</div>';

  // CEO node
  html+='<div class="org-tree">';
  html+='<div class="org-node org-ceo"><div class="org-node-title">CEO</div><div class="org-node-sub">Chief Executive Officer</div></div>';
  html+='<div class="org-children">';

  // Departments
  var depts=data.departments||{};
  var deptNames=Object.keys(depts);
  // Fixed first, then dynamic sorted by wave
  var fixed=deptNames.filter(function(d){return depts[d].type==='fixed'});
  var dynamic=deptNames.filter(function(d){return depts[d].type==='dynamic'}).sort(function(a,b){return (depts[a].wave||0)-(depts[b].wave||0)});

  var roster=data._roster||{};

  for(var i=0;i<fixed.length;i++){
    var name=fixed[i];var d=depts[name];
    html+='<div class="org-node org-fixed">';
    html+='<div class="org-node-title">'+esc(d.vp||name)+'</div>';
    html+='<div class="org-node-sub">'+esc(d.purpose||'')+'</div>';
    html+='<div class="org-node-badge org-badge-fixed">fixed</div>';
    html+=renderRosterAgents(roster[name]||[]);
    html+='</div>';
  }

  for(var i=0;i<dynamic.length;i++){
    var name=dynamic[i];var d=depts[name];
    html+='<div class="org-node org-dynamic">';
    html+='<div class="org-node-title">'+esc(d.vp||name)+'</div>';
    html+='<div class="org-node-sub">'+esc(d.purpose||'')+'</div>';
    html+='<div class="org-node-meta">';
    html+='<span class="org-node-badge org-badge-dynamic">wave '+esc(d.wave)+'</span>';
    html+='<span class="org-node-badge" style="background:#21262d;color:#8b949e">'+esc(d.depth||'')+'</span>';
    if(d.managers)html+='<span class="org-node-badge" style="background:#21262d;color:#8b949e">'+d.managers+' mgr</span>';
    html+='</div>';
    if(d.components&&d.components.length){
      html+='<div class="org-components">';
      for(var j=0;j<d.components.length;j++){
        html+='<span class="org-component">'+esc(d.components[j])+'</span>';
      }
      html+='</div>';
    }
    if(d.dependencies&&d.dependencies.length){
      html+='<div class="org-deps">depends on: '+d.dependencies.map(esc).join(', ')+'</div>';
    }
    // Roster agents for this department
    html+=renderRosterAgents(roster[name]||[]);
    // Teams if present
    if(d.teams){
      html+='<div class="org-teams">';
      var teamNames=Object.keys(d.teams);
      for(var t=0;t<teamNames.length;t++){
        var tn=teamNames[t];var team=d.teams[tn];
        html+='<div class="org-team"><span class="org-team-name">'+esc(team.manager||tn)+'</span>';
        if(team.components)html+=' <span class="org-team-detail">'+team.components.length+' components</span>';
        if(team.ics)html+=' <span class="org-team-detail">'+team.ics+' ICs</span>';
        if(team.testers)html+=' <span class="org-team-detail">'+team.testers+' testers</span>';
        if(team.scope)html+=' <span class="org-team-detail">'+esc(team.scope)+'</span>';
        html+='</div>';
      }
      html+='</div>';
    }
    html+='</div>';
  }

  html+='</div></div>'; // close org-children and org-tree

  // Waves summary
  if(data.waves&&data.waves.length){
    html+='<div style="margin-top:24px"><span style="font-size:14px;color:#f0f6fc">Wave Plan</span>';
    html+='<div style="display:flex;gap:12px;margin-top:8px;flex-wrap:wrap">';
    for(var w=0;w<data.waves.length;w++){
      var wave=data.waves[w];
      html+='<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 14px;min-width:140px">';
      html+='<div style="font-size:12px;color:#58a6ff;font-weight:600">Wave '+esc(wave.wave)+'</div>';
      if(wave.departments){
        for(var wd=0;wd<wave.departments.length;wd++){
          html+='<div style="font-size:12px;color:#c9d1d9;margin-top:2px">'+esc(wave.departments[wd])+'</div>';
        }
      }
      html+='</div>';
    }
    html+='</div></div>';
  }

  // Totals
  if(data.total_components||data.total_agents_estimate){
    html+='<div style="margin-top:16px;display:flex;gap:16px">';
    if(data.total_components)html+='<div class="summary-card"><div class="summary-val">'+data.total_components+'</div><div class="summary-label">Components</div></div>';
    if(data.total_agents_estimate)html+='<div class="summary-card"><div class="summary-val">'+data.total_agents_estimate+'</div><div class="summary-label">Est. Agents</div></div>';
    html+='<div class="summary-card"><div class="summary-val">'+fixed.length+'</div><div class="summary-label">Fixed Depts</div></div>';
    html+='<div class="summary-card"><div class="summary-val">'+dynamic.length+'</div><div class="summary-label">Dynamic Depts</div></div>';
    html+='</div>';
  }

  c.innerHTML=html;
}

function renderOrgNode(node,depth){
  // Legacy fallback - not used by showOrgChart anymore
  if(!node)return '';
  return '<pre style="color:#8b949e;font-size:12px">'+esc(JSON.stringify(node,null,2))+'</pre>';
  return html;
}

// ===== 2. ROSTER =====
var rosterData=[];
var rosterSort={col:'role',dir:1};
var rosterFilters={search:'',department:'',status:'',role:''};

var cycleData={};

async function showRoster(){
  activeView='roster';
  stopAutoRefresh();
  setActiveNav(5);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading roster...</p>';
  var data=await api('roster');
  if(data.error){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}
  rosterData=Array.isArray(data)?data:(data.agents||data.roster||[]);
  cycleData=await api('agent-cycles');
  renderRosterTable();
  startAutoRefresh(async function(){var f=saveFocus();var d=await api('roster');rosterData=Array.isArray(d)?d:(d.agents||d.roster||[]);cycleData=await api('agent-cycles');renderRosterTable();restoreFocus(f)});
}

function sortRosterBy(col){
  if(rosterSort.col===col)rosterSort.dir*=-1;
  else{rosterSort.col=col;rosterSort.dir=1}
  renderRosterRows();
}

function rosterFilterChange(field,val){rosterFilters[field]=val;renderRosterRows()}

function renderRosterTable(){
  var allDepts={},allStatuses={},allRoles={};
  for(var i=0;i<rosterData.length;i++){
    var a=rosterData[i];
    allDepts[a.department||a.dept||'-']=1;
    allStatuses[a.status||'unknown']=1;
    allRoles[a.role||'?']=1;
  }
  var active=0,retired=0;
  for(var i=0;i<rosterData.length;i++){
    var st=rosterData[i].status||'';
    if(st==='active')active++;
    else if(st==='retired'||st==='terminated'||st==='completed')retired++;
  }
  var total=rosterData.length;
  var cols=[{k:'role',l:'Role'},{k:'department',l:'Department'},{k:'status',l:'Status'},{k:'cycles',l:'Cycles'},{k:'generation',l:'Gen'},{k:'messages',l:'Messages'},{k:'last_active',l:'Last Active'},{k:'ticket',l:'Ticket'}];

  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Agent Roster</span></div>';
  html+='<div class="summary-row">'+
    '<div class="summary-card"><div class="num">'+total+'</div><div class="lbl">Total</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+active+'</div><div class="lbl">Active</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#484f58">'+retired+'</div><div class="lbl">Completed/Retired</div></div>'+
  '</div>';
  html+='<div class="filter-bar">';
  html+='<input id="roster-search" placeholder="Search..." value="'+esc(rosterFilters.search)+'" oninput="rosterFilterChange(\\'search\\',this.value)">';
  html+='<select onchange="rosterFilterChange(\\'role\\',this.value)"><option value="">All Roles</option>';
  Object.keys(allRoles).sort().forEach(function(r){html+='<option'+(rosterFilters.role===r?' selected':'')+'>'+esc(r)+'</option>'});
  html+='</select>';
  html+='<select onchange="rosterFilterChange(\\'department\\',this.value)"><option value="">All Departments</option>';
  Object.keys(allDepts).sort().forEach(function(d){html+='<option'+(rosterFilters.department===d?' selected':'')+'>'+esc(d)+'</option>'});
  html+='</select>';
  html+='<select onchange="rosterFilterChange(\\'status\\',this.value)"><option value="">All Statuses</option>';
  Object.keys(allStatuses).sort().forEach(function(s){html+='<option'+(rosterFilters.status===s?' selected':'')+'>'+esc(s)+'</option>'});
  html+='</select>';
  if(rosterFilters.search||rosterFilters.department||rosterFilters.status||rosterFilters.role){
    html+='<span class="filter-clear" onclick="rosterFilters={search:\\'\\',department:\\'\\',status:\\'\\',role:\\'\\'};renderRosterRows()">Clear filters</span>';
  }
  html+='<span class="filter-count" id="roster-filter-count"></span>';
  html+='</div>';
  html+='<table class="data-table"><thead><tr>';
  for(var c2=0;c2<cols.length;c2++){
    var cls=rosterSort.col===cols[c2].k?'sorted':'';
    var arrow=rosterSort.col===cols[c2].k?(rosterSort.dir>0?'&#9650;':'&#9660;'):'&#9650;';
    html+='<th class="'+cls+'" onclick="sortRosterBy(\\''+cols[c2].k+'\\')">'+cols[c2].l+'<span class="sort-arrow">'+arrow+'</span></th>';
  }
  html+='</tr></thead><tbody id="roster-tbody"></tbody></table>';
  document.getElementById('content').innerHTML=html;
  renderRosterRows();
}

function renderRosterRows(){
  var agents=rosterData.filter(function(a){
    if(rosterFilters.search){
      var s=rosterFilters.search.toLowerCase();
      var hay=((a.role||'')+(a.department||'')+(a.assigned_ticket||'')+(a.name||'')).toLowerCase();
      if(hay.indexOf(s)<0)return false;
    }
    if(rosterFilters.department&&(a.department||a.dept||'-')!==rosterFilters.department)return false;
    if(rosterFilters.status&&(a.status||'unknown')!==rosterFilters.status)return false;
    if(rosterFilters.role&&(a.role||'?')!==rosterFilters.role)return false;
    return true;
  });
  var col=rosterSort.col,dir=rosterSort.dir;
  agents.sort(function(a,b){
    var va=getRosterVal(a,col),vb=getRosterVal(b,col);
    if(typeof va==='number'&&typeof vb==='number')return (va-vb)*dir;
    va=String(va).toLowerCase();vb=String(vb).toLowerCase();
    return va<vb?-1*dir:va>vb?1*dir:0;
  });
  var html='';
  for(var i=0;i<agents.length;i++){
    var a=agents[i];
    var st=a.status||'unknown';
    var dotClass='dot-stale';
    if(st==='active') dotClass='dot-active';
    else if(st==='claimed') dotClass='dot-claimed';
    else if(st==='retired'||st==='terminated'||st==='completed') dotClass='dot-retired';
    var rowStyle=(st==='retired'||st==='terminated'||st==='completed')?'opacity:0.5':'';
    html+='<tr style="'+rowStyle+'">'+
      '<td>'+esc(a.role||a.name||a.agent_id||'?')+'</td>'+
      '<td>'+esc(a.department||a.dept||'-')+'</td>'+
      '<td><span class="dot '+dotClass+'"></span>'+esc(st)+'</td>'+
      '<td>'+getAgentCycles(a)+'</td>'+
      '<td>'+esc(a.generation||a.gen||'-')+'</td>'+
      '<td>'+esc(a.message_count!=null?a.message_count:a.messages!=null?a.messages:'-')+'</td>'+
      '<td>'+(a.last_active?localTime(a.last_active):a.last_seen?localTime(a.last_seen):'-')+'</td>'+
      '<td>'+esc(a.assigned_ticket||a.ticket||'-')+'</td>'+
    '</tr>';
  }
  var el=document.getElementById('roster-tbody');
  if(el)el.innerHTML=html;
  var fc=document.getElementById('roster-filter-count');
  if(fc)fc.textContent='Showing '+agents.length+' of '+rosterData.length;
}

function getAgentCycles(a){
  var role=a.role||'';
  var dept=a.department||a.dept||'';
  var key=role+'_'+dept;
  var entry=cycleData[key];
  if(entry)return String(entry.cycles);
  // Try partial matches
  for(var k in cycleData){
    if(k.indexOf(role)>=0||k.indexOf(dept)>=0)return String(cycleData[k].cycles);
  }
  return '-';
}

function getRosterVal(a,col){
  if(col==='role')return a.role||a.name||a.agent_id||'';
  if(col==='department')return a.department||a.dept||'';
  if(col==='status')return a.status||'';
  if(col==='cycles'){var c=getAgentCycles(a);return c==='-'?0:parseInt(c)||0}
  if(col==='generation')return parseInt(a.generation||a.gen||0)||0;
  if(col==='messages')return parseInt(a.message_count||a.messages||0)||0;
  if(col==='last_active')return a.last_active||a.last_seen||'';
  if(col==='ticket')return a.assigned_ticket||a.ticket||'';
  return '';
}

// ===== 3. SPRINT =====
async function showSprint(){
  activeView='sprint';
  stopAutoRefresh();
  setActiveNav(3);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading sprint data...</p>';
  var data=await api('sprint');
  if(data.error){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}
  var t=data.totals||{};
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Sprint Dashboard</span></div>';
  html+='<div class="summary-row">'+
    '<div class="summary-card"><div class="num">'+esc(t.total||0)+'</div><div class="lbl">Total Tickets</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+esc(t.done||0)+'</div><div class="lbl">Done</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#bc8cff">'+esc((t['in-progress']||0)+(t['in-review']||0))+'</div><div class="lbl">In Progress</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#58a6ff">'+esc(t['sprint-current']||0)+'</div><div class="lbl">Sprint Queue</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#8b949e">'+esc(t.backlog||0)+'</div><div class="lbl">Backlog</div></div>'+
  '</div>';
  var deptKeys=Object.keys(data.departments||{});
  for(var i=0;i<deptKeys.length;i++){
    var dk=deptKeys[i];
    var dd=data.departments[dk];
    var cnt=dd.counts||{};
    var total=cnt.total||0;
    var done=cnt.done||0;
    var inProg=(cnt['in-progress']||0)+(cnt['in-review']||0);
    var pctDone=total?Math.round(done/total*100):0;
    var pctProg=total?Math.round(inProg/total*100):0;
    var sprintId=dd.sprint&&dd.sprint.sprint_id?dd.sprint.sprint_id:'';
    var velocity=dd.sprint&&dd.sprint.velocity?dd.sprint.velocity:'';
    html+='<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:12px">';
    html+='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">';
    html+='<span style="font-size:14px;color:#f0f6fc;font-weight:600">'+esc(dk)+'</span>';
    html+='<span style="font-size:11px;color:#8b949e">';
    if(sprintId)html+='Sprint: '+esc(sprintId)+' ';
    if(velocity)html+='Velocity: '+esc(velocity)+' ';
    html+=esc(done)+'/'+esc(total)+' done</span></div>';
    html+='<div class="progress-bar">';
    if(pctDone>0)html+='<div class="progress-fill fill-done" style="width:'+pctDone+'%;display:inline-block;vertical-align:top">'+pctDone+'%</div>';
    if(pctProg>0)html+='<div class="progress-fill fill-progress" style="width:'+pctProg+'%;display:inline-block;vertical-align:top">'+pctProg+'%</div>';
    html+='</div>';
    html+='<div style="display:flex;gap:12px;margin-top:6px;font-size:11px;color:#8b949e">';
    html+='<span>Backlog: '+(cnt.backlog||0)+'</span>';
    html+='<span>Sprint: '+(cnt['sprint-current']||0)+'</span>';
    html+='<span>Active: '+inProg+'</span>';
    html+='<span style="color:#3fb950">Done: '+done+'</span>';
    html+='</div></div>';
  }
  c.innerHTML=html;
  startAutoRefresh(showSprint);
}

// ===== 4. RESEARCH =====
var rdData=null;
var rdFilter='';
var rdCurrentFile=null;

// ===== REPORTS (formerly R&D + Marketing) =====
// Aggregates every report-style document the company produces. Each entry
// shows structured metadata tags (dept, type, author, date) up top so the
// reader can scan provenance without opening the file.
var reportsData={reports:[],recommendations:''};
var reportsCurrentPath=null;
var reportsFilter='';
var reportsDeptFilter='';
var reportsTypeFilter='';

async function showReports(){
  activeView='reports';
  stopAutoRefresh();
  setActiveNav(6);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading reports...</p>';
  reportsData=await api('reports');
  reportsCurrentPath=null;
  renderReportsLayout();
}

function reportsFilterChange(val){reportsFilter=val;renderReportsList()}
function reportsDeptChange(val){reportsDeptFilter=val;renderReportsList()}
function reportsTypeChange(val){reportsTypeFilter=val;renderReportsList()}

function uniq(arr){var s={};var out=[];for(var i=0;i<arr.length;i++){if(arr[i]&&!s[arr[i]]){s[arr[i]]=1;out.push(arr[i])}}return out}

function renderReportsLayout(){
  var d=reportsData;
  var reports=d.reports||[];
  var depts=uniq(reports.map(function(r){return r.dept||'(none)'})).sort();
  var types=uniq(reports.map(function(r){return r.type||'report'})).sort();
  var deptOpts='<option value="">All depts</option>'+depts.map(function(x){return '<option value="'+esc(x)+'"'+(reportsDeptFilter===x?' selected':'')+'>'+esc(x)+'</option>'}).join('');
  var typeOpts='<option value="">All types</option>'+types.map(function(x){return '<option value="'+esc(x)+'"'+(reportsTypeFilter===x?' selected':'')+'>'+esc(x)+'</option>'}).join('');
  var html='<div style="margin-bottom:12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">'+
    '<span style="font-size:16px;color:#f0f6fc">Reports</span>'+
    '<span style="font-size:12px;color:#8b949e">'+reports.length+' total</span>'+
    (d.recommendations?'<button class="btn" style="margin-left:auto;font-size:11px" onclick="showReportsRecommendations()">R&amp;D Recommendations</button>':'')+
  '</div>';
  html+='<div class="filter-bar" style="margin-bottom:8px;display:flex;gap:6px;flex-wrap:wrap">'+
    '<input id="reports-search" placeholder="Search title / content..." style="flex:1;min-width:200px" oninput="reportsFilterChange(this.value)" value="'+esc(reportsFilter)+'">'+
    '<select onchange="reportsDeptChange(this.value)">'+deptOpts+'</select>'+
    '<select onchange="reportsTypeChange(this.value)">'+typeOpts+'</select>'+
  '</div>';
  html+='<div class="panel-layout" style="min-height:calc(100vh - 200px)">';
  html+='<div class="panel-left" id="reports-list" style="max-height:calc(100vh - 200px);overflow-y:auto"></div>';
  html+='<div class="panel-right" id="reports-detail" style="max-height:calc(100vh - 200px);overflow-y:auto"><div class="chat-empty">Select a report to read</div></div>';
  html+='</div>';
  document.getElementById('content').innerHTML=html;
  renderReportsList();
}

function reportTag(label,value,color){
  if(!value)return '';
  return '<span style="display:inline-block;background:#21262d;border:1px solid #30363d;color:'+(color||'#c9d1d9')+';font-size:10px;padding:1px 6px;border-radius:3px;margin-right:4px"><span style="color:#6e7681">'+esc(label)+':</span> '+esc(value)+'</span>';
}

function renderReportsList(){
  var reports=(reportsData.reports||[]).filter(function(r){
    if(reportsDeptFilter&&(r.dept||'(none)')!==reportsDeptFilter)return false;
    if(reportsTypeFilter&&(r.type||'report')!==reportsTypeFilter)return false;
    if(reportsFilter){
      var s=reportsFilter.toLowerCase();
      var hay=((r.title||'')+(r.file||'')+(r.author||'')+(r.dept||'')+(r.content||'')).toLowerCase();
      if(hay.indexOf(s)<0)return false;
    }
    return true;
  });
  var el=document.getElementById('reports-list');
  if(!el)return;
  var html='';
  for(var i=0;i<reports.length;i++){
    var r=reports[i];
    var active=reportsCurrentPath===r.path?' style="background:#21262d;border-left:2px solid #58a6ff"':'';
    var tags=reportTag('dept',r.dept||'')+reportTag('type',r.type||'')+(r.author?reportTag('by',r.author,'#3fb950'):'');
    html+='<div class="panel-item"'+active+' onclick="showReportDetail(\\''+esc(r.path)+'\\')">'+
      '<div style="font-size:12px;color:#f0f6fc;line-height:1.3">'+esc((r.title||r.file).substring(0,70))+'</div>'+
      '<div style="margin-top:3px">'+tags+'</div>'+
      '<div style="font-size:10px;color:#484f58;margin-top:3px">'+localDate(r.date)+' &middot; '+esc(r.file)+'</div>'+
    '</div>';
  }
  if(!reports.length)html='<div class="chat-empty">No reports match the filters</div>';
  el.innerHTML=html;
}

function showReportDetail(rpath){
  reportsCurrentPath=rpath;
  var r=(reportsData.reports||[]).find(function(x){return x.path===rpath});
  var el=document.getElementById('reports-detail');
  if(!el||!r)return;
  var meta='<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid #30363d">'+
    reportTag('dept',r.dept||'(none)')+
    reportTag('type',r.type||'report')+
    (r.author?reportTag('author',r.author,'#3fb950'):'')+
    reportTag('date',localTime(r.date))+
    reportTag('path',r.path,'#8b949e')+
  '</div>';
  el.innerHTML='<div style="padding:16px"><div style="font-size:14px;color:#f0f6fc;margin-bottom:6px">'+esc(r.title||r.file)+'</div>'+meta+'<div class="md-content">'+renderMarkdown(r.content)+'</div></div>';
  renderReportsList();
}

function showReportsRecommendations(){
  reportsCurrentPath='__recommendations__';
  var el=document.getElementById('reports-detail');
  if(!el)return;
  el.innerHTML='<div style="padding:16px"><div style="font-size:11px;color:#58a6ff;margin-bottom:8px">R&amp;D Technology Recommendations</div><div class="md-content">'+renderMarkdown(reportsData.recommendations||'')+'</div></div>';
  renderReportsList();
}

// ===== 5. LOGS =====
var currentLogFile=null;
async function showLogs(){
  activeView='logs';
  stopAutoRefresh();
  setActiveNav(7);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading logs...</p>';
  var files=await api('logs');
  if(!Array.isArray(files))files=[];
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">System Logs</span></div>';
  html+='<div class="panel-layout">';
  html+='<div class="panel-left" id="log-list">';
  if(files.length){
    for(var i=0;i<files.length;i++){
      var f=files[i];
      var sizeStr=f.size>1024*1024?(f.size/1024/1024).toFixed(1)+'M':f.size>1024?(f.size/1024).toFixed(1)+'K':f.size+'B';
      html+='<div class="panel-item" data-log="'+esc(f.file)+'" onclick="showLogFile(\\''+esc(f.file)+'\\')">'+
        '<span>'+esc(f.file)+'</span>'+
        '<span class="item-meta">'+sizeStr+'</span>'+
      '</div>';
    }
  } else {
    html+='<div class="chat-empty">No log files found</div>';
  }
  html+='</div>';
  html+='<div class="panel-right" id="log-view" style="padding:0"><div class="chat-empty">Select a log file</div></div>';
  html+='</div>';
  c.innerHTML=html;
  if(files.length)showLogFile(files[0].file);
  startAutoRefresh(function(){if(currentLogFile)showLogFile(currentLogFile)});
}

async function showLogFile(name){
  currentLogFile=name;
  document.querySelectorAll('#log-list .panel-item').forEach(function(el){
    el.classList.toggle('active',el.dataset.log===name);
  });
  var data=await api('logs/'+encodeURIComponent(name));
  var view=document.getElementById('log-view');
  view.innerHTML='<div style="padding:12px 16px;border-bottom:1px solid #30363d;font-size:13px;color:#f0f6fc">'+esc(name)+
    ' <span style="font-size:11px;color:#8b949e;margin-left:8px">'+esc(data.totalLines||0)+' total lines (showing last 200)</span></div>'+
    '<div class="log-viewer" id="log-content">'+esc(data.content||'')+'</div>';
  var lc=document.getElementById('log-content');
  if(lc)lc.scrollTop=lc.scrollHeight;
}

// ===== 6. PLANS =====
var currentPlanDept=null;
var currentPlanTab='plan';
// (Plans panel removed in 0.4.45 — under the sprint-staged lifecycle the
// VPs file backlog tickets and the CEO writes the per-sprint direction
// doc; nobody updates per-dept plan.md anymore. The panel was showing
// stale placeholders. The /api/departments/:dept/plan and /contracts
// endpoints stay for back-compat in case external clients hit them.)

// ===== 7. PROCESSES =====
var procData=[];
var procSort={col:'role',dir:1};
var procFilters={search:'',status:''};

async function showProcesses(){
  activeView='processes';
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading processes...</p>';
  var data=await api('processes');
  procData=data.processes||[];
  renderProcTable();
  startAutoRefresh(async function(){var f=saveFocus();var d=await api('processes');procData=d.processes||[];renderProcTable();restoreFocus(f)});
}

function sortProcBy(col){
  if(procSort.col===col)procSort.dir*=-1;
  else{procSort.col=col;procSort.dir=1}
  renderProcTable();
}

function procFilterChange(field,val){procFilters[field]=val;var f=saveFocus();renderProcTable();restoreFocus(f)}

function renderProcTable(){
  var procs=procData.filter(function(p){
    if(procFilters.search){
      var s=procFilters.search.toLowerCase();
      var hay=((p.role||'')+(p.department||'')).toLowerCase();
      if(hay.indexOf(s)<0)return false;
    }
    if(procFilters.status==='alive'&&!p.alive)return false;
    if(procFilters.status==='dead'&&p.alive)return false;
    return true;
  });
  var col=procSort.col,dir=procSort.dir;
  procs.sort(function(a,b){
    var va=getProcVal(a,col),vb=getProcVal(b,col);
    if(typeof va==='number'&&typeof vb==='number')return (va-vb)*dir;
    va=String(va).toLowerCase();vb=String(vb).toLowerCase();
    return va<vb?-1*dir:va>vb?1*dir:0;
  });
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Running Processes</span></div>';
  if(!procs.length){
    html+='<p style="color:#8b949e;text-align:center;padding:40px">No processes found in processes.json</p>';
    document.getElementById('content').innerHTML=html;return;
  }
  var aliveCount=0;
  for(var i=0;i<procs.length;i++){if(procs[i].alive)aliveCount++}
  html+='<div class="summary-row">'+
    '<div class="summary-card"><div class="num">'+procs.length+'</div><div class="lbl">Total</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+aliveCount+'</div><div class="lbl">Alive</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#f85149">'+(procData.length-aliveCount)+'</div><div class="lbl">Dead</div></div>'+
  '</div>';
  html+='<div class="filter-bar">';
  html+='<input placeholder="Search..." value="'+esc(procFilters.search)+'" oninput="procFilterChange(\\'search\\',this.value)">';
  html+='<select onchange="procFilterChange(\\'status\\',this.value)"><option value="">All</option><option value="alive"'+(procFilters.status==='alive'?' selected':'')+'>Alive</option><option value="dead"'+(procFilters.status==='dead'?' selected':'')+'>Dead</option></select>';
  if(procFilters.search||procFilters.status){html+='<span class="filter-clear" onclick="procFilters={search:\\'\\',status:\\'\\'};renderProcTable()">Clear</span>'}
  html+='<span class="filter-count">Showing '+procs.length+' of '+procData.length+'</span>';
  html+='</div>';
  var cols=[{k:'role',l:'Role'},{k:'department',l:'Department'},{k:'interval',l:'Interval'},{k:'pid',l:'PID'},{k:'status',l:'Status'},{k:'started_at',l:'Started'}];
  html+='<table class="data-table"><thead><tr>';
  for(var c2=0;c2<cols.length;c2++){
    var cls=procSort.col===cols[c2].k?'sorted':'';
    var arrow=procSort.col===cols[c2].k?(procSort.dir>0?'&#9650;':'&#9660;'):'&#9650;';
    html+='<th class="'+cls+'" onclick="sortProcBy(\\''+cols[c2].k+'\\')">'+cols[c2].l+'<span class="sort-arrow">'+arrow+'</span></th>';
  }
  html+='</tr></thead><tbody>';
  for(var i=0;i<procs.length;i++){
    var p=procs[i];
    var alive=p.alive;
    html+='<tr>'+
      '<td>'+esc(p.role||p.name||'?')+'</td>'+
      '<td>'+esc(p.department||p.dept||'-')+'</td>'+
      '<td>'+esc(p.interval||p.schedule||'-')+'</td>'+
      '<td>'+esc(p.pid||p.PID||'-')+'</td>'+
      '<td><span class="dot '+(alive?'dot-alive':'dot-dead')+'"></span>'+(alive?'Alive':'Dead')+'</td>'+
      '<td>'+(p.started_at?localTime(p.started_at):p.start_time?localTime(p.start_time):'-')+'</td>'+
    '</tr>';
  }
  html+='</tbody></table>';
  document.getElementById('content').innerHTML=html;
}

function getProcVal(p,col){
  if(col==='role')return p.role||p.name||'';
  if(col==='department')return p.department||p.dept||'';
  if(col==='interval')return p.interval||p.schedule||'';
  if(col==='pid')return parseInt(p.pid||p.PID||0)||0;
  if(col==='status')return p.alive?'alive':'dead';
  if(col==='started_at')return p.started_at||p.start_time||'';
  return '';
}

// ===== 7b. AGENT ACTIVITY (live spawns + cycle history + per-spawn stream) =====
var activityData={active:[],cycles:[]};
var activityStream={spawnId:null,events:[],next:0,total:0,timer:null};
var globalStream={open:false,events:[],cursor:'',total:0,active_count:0};
var actSort={col:'started_ts',dir:-1};       // for currently-running table
var actFilters={search:'',role:'',dept:'',source:'',status:''};
var cycSort={col:'ended_at',dir:-1};         // for recent cycles table
var cycFilters={search:'',role:'',dept:'',status:''};

async function showActivity(){
  activeView='activity';
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading agent activity...</p>';
  await refreshActivity();
  startAutoRefresh(refreshActivity);
}

async function refreshActivity(){
  if(activityStream.spawnId){await refreshSpawnStream();return}
  if(globalStream.open){await refreshGlobalStream();return}
  var f=saveFocus();
  var a=await api('spawns/active').catch(function(){return{active:[]}});
  var h=await api('spawns/cycles?limit=200').catch(function(){return{cycles:[]}});
  activityData={active:a.active||[],cycles:h.cycles||[]};
  renderActivity();
  restoreFocus(f);
}

function fmtElapsed(s){
  s=Math.max(0,Math.floor(s||0));
  if(s<60)return s+'s';
  if(s<3600)return Math.floor(s/60)+'m '+(s%60)+'s';
  return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
}

// Sort helpers shared with both tables
function actSortBy(col){if(actSort.col===col)actSort.dir*=-1;else{actSort.col=col;actSort.dir=1}var f=saveFocus();renderActivity();restoreFocus(f)}
function cycSortBy(col){if(cycSort.col===col)cycSort.dir*=-1;else{cycSort.col=col;cycSort.dir=1}var f=saveFocus();renderActivity();restoreFocus(f)}
function actFilterChange(field,val){actFilters[field]=val;var f=saveFocus();renderActivity();restoreFocus(f)}
function cycFilterChange(field,val){cycFilters[field]=val;var f=saveFocus();renderActivity();restoreFocus(f)}
function actClearFilters(){actFilters={search:'',role:'',dept:'',source:'',status:''};renderActivity()}
function cycClearFilters(){cycFilters={search:'',role:'',dept:'',status:''};renderActivity()}

// Get value for sorting / extracting unique sets
function getActVal(s,col){
  if(col==='started_ts'||col==='elapsed_s'||col==='stream_lines'||col==='pid')return Number(s[col]||0);
  if(col==='alive')return s.alive?1:0;
  return String(s[col]==null?'':s[col]).toLowerCase();
}
function getCycVal(c,col){
  if(col==='duration_s')return Number(c.duration_s||0);
  if(col==='num_turns')return Number((c.result||{}).num_turns||0);
  if(col==='status'){var r=c.result||{};return c.exit_code===0&&!r.is_error?'ok':(c.exit_code===0?'error':'exit')}
  if(col==='ended_at')return c.ended_at||'';
  if(col==='result')return ((c.result||{}).result_text||'').toLowerCase();
  return String(c[col]==null?'':c[col]).toLowerCase();
}

function thBtn(label,col,sortState,sorter){
  var arrow=sortState.col===col?(sortState.dir>0?'&#9650;':'&#9660;'):'&#9651;';
  var cls=sortState.col===col?' class="sorted"':'';
  return '<th'+cls+' onclick="'+sorter+'(\\''+col+'\\')">'+label+' <span class="sort-arrow">'+arrow+'</span></th>';
}

function uniqueVals(arr,key){var s={};for(var i=0;i<arr.length;i++){var v=arr[i][key];if(v!=null&&v!=='')s[v]=1}return Object.keys(s).sort()}

function selectFilter(field,val,opts,onchange){
  var html='<select onchange="'+onchange+'(\\''+field+'\\',this.value)"><option value="">All '+field+'</option>';
  for(var i=0;i<opts.length;i++){
    var o=opts[i],sel=val===o?' selected':'';
    html+='<option value="'+esc(o)+'"'+sel+'>'+esc(o)+'</option>';
  }
  return html+'</select>';
}

function renderActivity(){
  // Hide dead/orphaned spawns from "Currently running" — they're noise. Cycles
  // history is unaffected (it's the auditable record of what actually ran).
  var a=(activityData.active||[]).filter(function(s){return s.alive});
  var h=activityData.cycles;
  var totalEvents=0;
  for(var i=0;i<a.length;i++){totalEvents+=Number(a[i].stream_lines||0)}

  var html='<div style="margin-bottom:16px;display:flex;align-items:center;gap:12px">'+
    '<span style="font-size:16px;color:#f0f6fc">Agent Activity</span>'+
    '<button class="btn" style="margin-left:auto" onclick="openGlobalStream()" title="Combined live stream of every active spawn, color-coded by source">⊕ Watch Global</button>'+
  '</div>';
  html+='<div class="summary-row">'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+a.length+'</div><div class="lbl">Active spawns</div></div>'+
    '<div class="summary-card"><div class="num">'+totalEvents+'</div><div class="lbl">Total events (live)</div></div>'+
    '<div class="summary-card"><div class="num">'+h.length+'</div><div class="lbl">Recent cycles</div></div>'+
  '</div>';

  // ----- Live (active) -----
  html+='<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">Currently running</h3>';
  if(!a.length){
    html+='<p style="color:#8b949e;padding:12px">Nothing in flight. (claude-spawn.sh registers active spawns at start; if VPs are mid-turn but nothing shows here, the spawn pre-dates this hive version.)</p>';
  }else{
    // Filter
    var fa=a.filter(function(s){
      if(actFilters.search){var q=actFilters.search.toLowerCase();var hay=((s.role||'')+(s.agent||'')+(s.dept||'')+(s.ticket||'')+(s.spawn_id||'')).toLowerCase();if(hay.indexOf(q)<0)return false}
      if(actFilters.role&&(s.role||'')!==actFilters.role)return false;
      if(actFilters.dept&&(s.dept||'')!==actFilters.dept)return false;
      if(actFilters.source&&(s.source||'')!==actFilters.source)return false;
      if(actFilters.status==='alive'&&!s.alive)return false;
      if(actFilters.status==='dead'&&s.alive)return false;
      return true;
    });
    // Sort
    var col=actSort.col,dir=actSort.dir;
    fa.sort(function(x,y){var vx=getActVal(x,col),vy=getActVal(y,col);if(typeof vx==='number'&&typeof vy==='number')return (vx-vy)*dir;return vx<vy?-1*dir:vx>vy?1*dir:0});

    html+='<div class="filter-bar">';
    html+='<input placeholder="Search role/agent/dept/ticket/spawn_id" value="'+esc(actFilters.search)+'" oninput="actFilterChange(\\'search\\',this.value)">';
    html+=selectFilter('role',actFilters.role,uniqueVals(a,'role'),'actFilterChange');
    html+=selectFilter('dept',actFilters.dept,uniqueVals(a,'dept'),'actFilterChange');
    html+=selectFilter('source',actFilters.source,uniqueVals(a,'source'),'actFilterChange');
    html+='<select onchange="actFilterChange(\\'status\\',this.value)"><option value="">All status</option><option value="alive"'+(actFilters.status==='alive'?' selected':'')+'>Alive</option><option value="dead"'+(actFilters.status==='dead'?' selected':'')+'>Dead</option></select>';
    if(actFilters.search||actFilters.role||actFilters.dept||actFilters.source||actFilters.status)html+='<span class="filter-clear" onclick="actClearFilters()">Clear</span>';
    html+='<span class="filter-count">Showing '+fa.length+' of '+a.length+'</span>';
    html+='</div>';

    html+='<table class="data-table"><thead><tr>';
    html+=thBtn('Role','role',actSort,'actSortBy');
    html+=thBtn('Agent','agent',actSort,'actSortBy');
    html+=thBtn('Dept','dept',actSort,'actSortBy');
    html+=thBtn('Ticket','ticket',actSort,'actSortBy');
    html+=thBtn('Started','started_ts',actSort,'actSortBy');
    html+=thBtn('Elapsed','elapsed_s',actSort,'actSortBy');
    html+=thBtn('Events','stream_lines',actSort,'actSortBy');
    html+=thBtn('PID','pid',actSort,'actSortBy');
    html+=thBtn('Source','source',actSort,'actSortBy');
    html+='<th></th></tr></thead><tbody>';
    for(var i=0;i<fa.length;i++){
      var s=fa[i];
      var deadStyle=s.alive?'':'opacity:0.5;text-decoration:line-through';
      var srcBadge=s.source==='tagged'?'<span style="color:#3fb950">tagged</span>':'<span style="color:#d29922" title="claude process found via /proc scan — not registered by claude-spawn.sh, so identity is unknown. Likely spawned by Claude Code Agent tool from inside a parent.">/proc</span>';
      var watchBtn=s.source==='proc-scan'?'<span style="color:#6e7681" title="No stream file — only claude-spawn.sh registered spawns are watchable">no stream</span>':'<button class="btn" data-spawn-id="'+esc(s.spawn_id)+'" onclick="openSpawnStream(this.dataset.spawnId)">Watch</button>';
      html+='<tr style="'+deadStyle+'">'+
        '<td>'+esc(s.role||'?')+'</td>'+
        '<td>'+esc(s.agent||'?')+'</td>'+
        '<td>'+esc(s.dept||'?')+'</td>'+
        '<td>'+esc(s.ticket||'-')+'</td>'+
        '<td title="'+esc(s.started_at||'')+'">'+esc(localShort(s.started_at))+'</td>'+
        '<td data-elapsed-from="'+(s.started_ts||0)+'">'+fmtElapsed(s.elapsed_s)+'</td>'+
        '<td>'+(s.stream_lines||0)+'</td>'+
        '<td style="color:#8b949e" title="ppid='+(s.ppid||'?')+'">'+(s.pid||'-')+(s.alive?'':' ✗')+'</td>'+
        '<td>'+srcBadge+'</td>'+
        '<td>'+watchBtn+'</td>'+
      '</tr>';
    }
    html+='</tbody></table>';
  }

  // ----- History (cycles) -----
  html+='<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">Recent cycles (last '+h.length+')</h3>';
  if(!h.length){
    html+='<p style="color:#8b949e;padding:12px">No completed cycles yet (logs/cycles.jsonl is empty).</p>';
  }else{
    var fh=h.filter(function(c){
      if(cycFilters.search){var q=cycFilters.search.toLowerCase();var hay=((c.role||'')+(c.agent||'')+(c.dept||'')+(c.ticket||'')+((c.result||{}).result_text||'')).toLowerCase();if(hay.indexOf(q)<0)return false}
      if(cycFilters.role&&(c.role||'')!==cycFilters.role)return false;
      if(cycFilters.dept&&(c.dept||'')!==cycFilters.dept)return false;
      if(cycFilters.status){var r=c.result||{};var st=c.exit_code===0&&!r.is_error?'ok':(c.exit_code===0?'error':'exit');if(st!==cycFilters.status)return false}
      return true;
    });
    fh.sort(function(x,y){var vx=getCycVal(x,cycSort.col),vy=getCycVal(y,cycSort.col);if(typeof vx==='number'&&typeof vy==='number')return (vx-vy)*cycSort.dir;return vx<vy?-1*cycSort.dir:vx>vy?1*cycSort.dir:0});

    html+='<div class="filter-bar">';
    html+='<input placeholder="Search role/agent/dept/ticket/result" value="'+esc(cycFilters.search)+'" oninput="cycFilterChange(\\'search\\',this.value)">';
    html+=selectFilter('role',cycFilters.role,uniqueVals(h,'role'),'cycFilterChange');
    html+=selectFilter('dept',cycFilters.dept,uniqueVals(h,'dept'),'cycFilterChange');
    html+='<select onchange="cycFilterChange(\\'status\\',this.value)"><option value="">All status</option><option value="ok"'+(cycFilters.status==='ok'?' selected':'')+'>ok</option><option value="error"'+(cycFilters.status==='error'?' selected':'')+'>error</option><option value="exit"'+(cycFilters.status==='exit'?' selected':'')+'>exit !=0</option></select>';
    if(cycFilters.search||cycFilters.role||cycFilters.dept||cycFilters.status)html+='<span class="filter-clear" onclick="cycClearFilters()">Clear</span>';
    html+='<span class="filter-count">Showing '+fh.length+' of '+h.length+'</span>';
    html+='</div>';

    html+='<table class="data-table"><thead><tr>';
    html+=thBtn('Ended','ended_at',cycSort,'cycSortBy');
    html+=thBtn('Role','role',cycSort,'cycSortBy');
    html+=thBtn('Agent','agent',cycSort,'cycSortBy');
    html+=thBtn('Dept','dept',cycSort,'cycSortBy');
    html+=thBtn('Duration','duration_s',cycSort,'cycSortBy');
    html+=thBtn('Turns','num_turns',cycSort,'cycSortBy');
    html+=thBtn('Status','status',cycSort,'cycSortBy');
    html+=thBtn('Result','result',cycSort,'cycSortBy');
    html+='<th></th></tr></thead><tbody>';
    for(var i=0;i<fh.length;i++){
      var c=fh[i];
      var r=c.result||{};
      var statusColor=c.exit_code===0&&!r.is_error?'#3fb950':'#f85149';
      var statusLabel=c.exit_code===0?(r.is_error?'error':'ok'):'exit '+c.exit_code;
      var preview=esc((r.result_text||'').slice(0,120));
      html+='<tr>'+
        '<td title="'+esc(c.ended_at||'')+'">'+esc(localShort(c.ended_at))+'</td>'+
        '<td>'+esc(c.role||'?')+'</td>'+
        '<td>'+esc(c.agent||'?')+'</td>'+
        '<td>'+esc(c.dept||'?')+'</td>'+
        '<td>'+fmtElapsed(c.duration_s)+'</td>'+
        '<td>'+(r.num_turns||'-')+'</td>'+
        '<td style="color:'+statusColor+'">'+esc(statusLabel)+'</td>'+
        '<td style="color:#8b949e;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+preview+'">'+preview+'</td>'+
        '<td><button class="btn" data-spawn-id="'+esc(c.spawn_id)+'" onclick="openSpawnStream(this.dataset.spawnId)">Replay</button></td>'+
      '</tr>';
    }
    html+='</tbody></table>';
  }

  document.getElementById('content').innerHTML=html;
}

function openSpawnStream(spawnId){
  activityStream={spawnId:spawnId,events:[],next:0,total:0};
  refreshSpawnStream();
}

function closeSpawnStream(){
  activityStream={spawnId:null,events:[],next:0,total:0};
  renderActivity();
}

// Serialize stream fetches. Without this, concurrent SSE-driven refreshes
// (process-change + cycle-change + spawn-stream-change can all fire within
// ~500 ms during an active spawn) call refreshSpawnStream in parallel. Each
// in-flight call sees the SAME activityStream.next cursor — the first
// hasn't returned to advance it — so all of them fetch the same window of
// events and APPEND them to activityStream.events. The render then shows
// every event N times. Guard with an in-flight flag + a pending-bit so
// late events still get one follow-up pass once the in-flight call lands.
var spawnStreamBusy=false, spawnStreamPending=false;
async function refreshSpawnStream(){
  if(!activityStream.spawnId)return;
  if(spawnStreamBusy){ spawnStreamPending=true; return; }
  spawnStreamBusy=true;
  try {
    do {
      spawnStreamPending=false;
      if(!activityStream.spawnId)break;
      var url='spawns/'+encodeURIComponent(activityStream.spawnId)+'/stream?from='+activityStream.next+'&limit=500';
      var data=await api(url).catch(function(){return{events:[],next:activityStream.next,total:activityStream.total}});
      if(data.events&&data.events.length){
        for(var i=0;i<data.events.length;i++)activityStream.events.push(data.events[i]);
      }
      activityStream.next=data.next||activityStream.next;
      activityStream.total=data.total||activityStream.total;
      renderSpawnStream();
    } while(spawnStreamPending);
  } finally {
    spawnStreamBusy=false;
  }
}

function renderSpawnStream(){
  // Look up the spawn's metadata so the header can show useful info instead
  // of just the opaque spawn id + event count. Live spawns live in
  // activityData.active; finished ones in activityData.cycles. Active wins
  // when both are present (race during transition).
  var meta=null, alive=false;
  var act=(activityData&&activityData.active)||[];
  for(var i=0;i<act.length;i++){
    if(act[i].spawn_id===activityStream.spawnId){ meta=act[i]; alive=!!act[i].alive; break; }
  }
  if(!meta){
    var cyc=(activityData&&activityData.cycles)||[];
    for(var j=0;j<cyc.length;j++){
      if(cyc[j].spawn_id===activityStream.spawnId){ meta=cyc[j]; break; }
    }
  }

  // ── Row 1: nav + identity ────────────────────────────────────────────
  var roleAgent = meta
    ? '<span style="color:#79c0ff">'+esc(meta.role||'?')+'</span> · <span style="color:#c9d1d9">'+esc(meta.agent||'?')+'</span>'
    : '<span style="color:#8b949e">unknown spawn</span>';
  var deptTicket = meta
    ? '<span style="color:#8b949e">'+esc(meta.dept||meta.department||'-')+'</span>'+
      (meta.ticket?' · <span style="color:#d2a8ff">'+esc(meta.ticket)+'</span>':'')
    : '';

  // ── Row 2: timing, status, counts ────────────────────────────────────
  var statusPill;
  if(alive){
    statusPill='<span style="color:#3fb950">● alive</span>';
  } else if(meta && typeof meta.exit_code==='number'){
    var ok=meta.exit_code===0 && !(meta.result&&meta.result.is_error);
    statusPill=ok
      ? '<span style="color:#3fb950">✓ done</span>'
      : '<span style="color:#f85149">✗ exit '+meta.exit_code+(meta.result&&meta.result.is_error?' (error)':'')+'</span>';
  } else {
    statusPill='<span style="color:#8b949e">○ no longer registered</span>';
  }

  var timing='';
  if(meta){
    if(alive){
      // Live: launched at + elapsed. The elapsed value gets a
      // data-elapsed-from attribute so tickAsync rewrites it every second
      // — the rest of the panel stays put.
      timing='launched '+esc(localTime(meta.started_at||''))+
             ' · elapsed <span data-elapsed-from="'+(meta.started_ts||0)+'">'+
               esc(fmtElapsed(meta.elapsed_s))+'</span>';
    } else if(meta.ended_at){
      // Done: ended at + duration (fixed, no live tick needed)
      timing='ended '+esc(localTime(meta.ended_at))+
             ' · ran '+esc(fmtElapsed(meta.duration_s));
    } else if(meta.started_at){
      timing='launched '+esc(localTime(meta.started_at));
    }
  }

  var turnsBadge=(meta && meta.result && meta.result.num_turns)
    ? ' · turns '+esc(String(meta.result.num_turns))
    : (meta && meta.num_turns ? ' · turns '+esc(String(meta.num_turns)) : '');

  var pidBadge=(meta && meta.pid)
    ? ' · <span style="color:#6e7681;font-family:monospace" title="ppid='+esc(String(meta.ppid||'?'))+'">pid '+esc(String(meta.pid))+'</span>'
    : '';

  var html='<div style="margin-bottom:12px">'+
    '<div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">'+
      '<button class="btn" onclick="closeSpawnStream()">← Back to activity</button>'+
      '<span style="font-size:14px">'+roleAgent+
        (deptTicket?' &nbsp; '+deptTicket:'')+'</span>'+
      '<span style="margin-left:auto;font-size:12px">'+statusPill+'</span>'+
    '</div>'+
    '<div style="font-size:12px;color:#8b949e">'+
      timing+
      ' · <span style="color:#c9d1d9">'+activityStream.total+' events</span>'+
      turnsBadge+
      pidBadge+
      ' · <span style="color:#6e7681;font-family:monospace" title="spawn id">'+esc(activityStream.spawnId)+'</span>'+
    '</div>'+
  '</div>';
  if(!activityStream.events.length){
    html+='<p style="color:#8b949e;padding:12px">No events yet (claude may still be generating its first message).</p>';
    var _ss0=saveScroll();
    try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_ss0);}
    return;
  }
  html+='<div id="spawn-stream" style="display:flex;flex-direction:column;gap:6px;max-height:78vh;overflow:auto;padding:6px;background:#0d1117;border:1px solid #30363d;border-radius:6px">';
  // Render newest first: walk events in reverse but keep the original index
  // as the visible #N so a given event keeps its identifier across refreshes.
  for(var i=activityStream.events.length-1;i>=0;i--){
    var frag=renderStreamEvent(activityStream.events[i],i);
    if(frag) html+=frag;
  }
  html+='</div>';
  // Preserve scroll position: the innerHTML write wipes the DOM and the
  // browser resets scroll to the top.  Save state, restore (snapping to
  // bottom if the user was following the tail).
  var _ss=saveScroll();
  try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_ss);}
}

// ── Watch Global: combined stream of every active spawn ─────────────────────
function openGlobalStream(){
  globalStream={open:true,events:[],cursor:'',total:0,active_count:0};
  refreshGlobalStream();
}
function closeGlobalStream(){
  globalStream={open:false,events:[],cursor:'',total:0,active_count:0};
  renderActivity();
}

// ── Gate Output: stdout/stderr of the deterministic gate run ──────────
var gateOutput={ticket:null,output:''};
async function openGateOutput(ticket){
  gateOutput={ticket:ticket,output:''};
  try {
    var data=await api('logs/gate-'+encodeURIComponent(ticket)+'.log');
    if(gateOutput.ticket!==ticket)return;  // stale response guard
    gateOutput.output=data.content||'';
  } catch(e) {
    gateOutput.output='(failed to load gate output)';
  }
  renderGateOutput();
}
function closeGateOutput(){
  gateOutput={ticket:null,output:''};
  renderVerification();
}
function renderGateOutput(){
  var tid=gateOutput.ticket;
  var html='<div style="margin-bottom:12px;display:flex;align-items:center;gap:12px">'+
    '<button class="btn" onclick="closeGateOutput()">← Back to verification</button>'+
    '<span style="font-size:14px;color:#7ee787">Gate run: <code>'+esc(tid)+'</code></span>'+
  '</div>';
  if(!gateOutput.output){
    html+='<p style="color:#8b949e;padding:12px">No gate output yet — gates may not have run for this ticket.</p>';
    var _sgo=saveScroll();
    try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_sgo);}
    return;
  }
  html+='<pre style="margin:0;padding:8px 10px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;word-break:break-word;max-height:75vh;overflow-y:auto;overflow-x:auto">'+esc(gateOutput.output)+'</pre>';
  var _sgo2=saveScroll();
  try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_sgo2);}
}

// Same concurrent-fetch hazard as refreshSpawnStream — guard identically.
var globalStreamBusy=false, globalStreamPending=false;
async function refreshGlobalStream(){
  if(!globalStream.open)return;
  if(globalStreamBusy){ globalStreamPending=true; return; }
  globalStreamBusy=true;
  try {
    do {
      globalStreamPending=false;
      if(!globalStream.open)break;
      var url='spawns/global-stream?limit=400'+(globalStream.cursor?'&cursor='+encodeURIComponent(globalStream.cursor):'');
      var data=await api(url).catch(function(){return{events:[],cursor:globalStream.cursor,total:globalStream.total,active_count:0}});
      if(data.events&&data.events.length){
        for(var i=0;i<data.events.length;i++)globalStream.events.push(data.events[i]);
      }
      globalStream.cursor=data.cursor||globalStream.cursor;
      globalStream.total=data.total||globalStream.total;
      globalStream.active_count=data.active_count||0;
      // cap retained events so the page doesn't grow without bound
      if(globalStream.events.length>1500)globalStream.events=globalStream.events.slice(-1500);
      renderGlobalStream();
    } while(globalStreamPending);
  } finally {
    globalStreamBusy=false;
  }
}
// Stable color hash per spawn_id so each agent gets a consistent stripe
function spawnColor(id){
  var palette=['#79c0ff','#d2a8ff','#7ee787','#ffa657','#ff7b72','#a5d6ff','#f0883e','#bc8cff','#3fb950','#d29922'];
  var h=0;for(var i=0;i<id.length;i++){h=(h*31+id.charCodeAt(i))|0}
  return palette[Math.abs(h)%palette.length];
}
function renderGlobalStream(){
  var html='<div style="margin-bottom:12px;display:flex;align-items:center;gap:12px">'+
    '<button class="btn" onclick="closeGlobalStream()">← Back to activity</button>'+
    '<span style="color:#8b949e">Global stream · '+globalStream.active_count+' active spawns · '+globalStream.events.length+' rendered events ('+globalStream.total+' total)</span>'+
  '</div>';
  if(!globalStream.events.length){
    html+='<p style="color:#8b949e;padding:12px">No events yet — waiting for spawns to emit data.</p>';
    var _gs=saveScroll();
    try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_gs);}
    return;
  }
  html+='<div id="global-stream" style="display:flex;flex-direction:column;gap:4px;max-height:80vh;overflow:auto;padding:6px;background:#0d1117;border:1px solid #30363d;border-radius:6px">';
  // Newest first — walk in reverse so the most recent emit is at the top.
  for(var i=globalStream.events.length-1;i>=0;i--){
    var w=globalStream.events[i];
    var ev=w.event||{type:'?'};
    // Skip thinking-token-count system events (subtype contains 'thinking').
    if((ev.type==='system') && (ev.subtype||'').indexOf('thinking')>=0) continue;
    var color=spawnColor(w.spawn_id||'');
    var label=esc((w.role||'?')+' · '+(w.agent||'?')+(w.dept&&w.dept!=='?'? ' / '+esc(w.dept):'')+(w.ticket?' · '+esc(w.ticket):'')+' · #'+w.line);
    html+='<div style="border-left:3px solid '+color+';padding:6px 8px;background:#0d1117">'+
      '<div style="font-size:10px;color:'+color+';margin-bottom:4px;font-family:ui-monospace,monospace">'+label+'</div>'+
      renderStreamEvent(ev,w.line)+
    '</div>';
  }
  html+='</div>';
  var _gs2=saveScroll();
  try{document.getElementById('content').innerHTML=html;}finally{restoreScroll(_gs2);}
}

// Render a string in a <pre> with proper wrapping, optionally collapsed by
// default if it's huge. textColor is applied to the <pre>.
function preBlock(text,textColor,collapseAbove){
  text=String(text==null?'':text);
  var color=textColor||'#c9d1d9';
  var preStyle='margin:0;padding:6px 8px;background:#010409;border:1px solid #21262d;border-radius:4px;color:'+color+';font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;word-break:break-word;overflow-x:auto;max-height:520px;overflow-y:auto';
  collapseAbove=collapseAbove||4000;
  if(text.length<=collapseAbove){
    return '<pre style="'+preStyle+'">'+esc(text)+'</pre>';
  }
  var head=text.slice(0,collapseAbove);
  return '<details><summary style="cursor:pointer;color:#8b949e;font-size:11px;margin-bottom:4px">show full ('+text.length.toLocaleString()+' chars)</summary><pre style="'+preStyle+'">'+esc(text)+'</pre></details><pre style="'+preStyle+'">'+esc(head)+(text.length>collapseAbove?'\\n…':'')+'</pre>';
}

// Pretty-print JSON; on stringify failure return the raw value.
function prettyJson(obj){
  try { return JSON.stringify(obj,null,2); }
  catch(e) { try { return String(obj); } catch(_) { return '[unserialisable]'; } }
}

function renderStreamEvent(ev,idx){
  // claude --output-format stream-json events: system/init, assistant
  // (with content blocks: text, tool_use), user (with tool_result), result.
  var t=ev.type||'?';
  var bg='#161b22',headerColor='#c9d1d9',label=t,body='';

  if(t==='system'){
    // Skip thinking-token-count events (SYSTEM:THINKING_TOKENS from Claude CLI).
    // Keep the final thinking text block — that's an assistant message with a
    // thinking content block, handled above.
    if((ev.subtype||'').indexOf('thinking')>=0) return '';
    bg='#1c2128'; headerColor='#8b949e'; label='system:'+(ev.subtype||'');
    body=preBlock(prettyJson(ev),'#8b949e');
  }
  else if(t==='assistant'){
    bg='#0d1f2d'; headerColor='#79c0ff'; label='assistant';
    var msg=ev.message||{};
    var blocks=msg.content||[];
    var parts=[];
    for(var b=0;b<blocks.length;b++){
      var blk=blocks[b];
      if(blk.type==='text'){
        parts.push('<div style="font-size:11px;color:#79c0ff;margin:6px 0 4px">text</div>'+preBlock(blk.text||'','#c9d1d9'));
      }
      else if(blk.type==='tool_use'){
        var argsJson=prettyJson(blk.input==null?{}:blk.input);
        parts.push(
          '<div style="font-size:11px;color:#d2a8ff;margin:6px 0 4px"><b>tool_use</b> · '+esc(blk.name||'(unnamed)')+(blk.id?' <span style="color:#6e7681">'+esc(blk.id)+'</span>':'')+'</div>'+
          preBlock(argsJson,'#d2a8ff')
        );
      }
      else if(blk.type==='thinking'){
        parts.push('<div style="font-size:11px;color:#8b949e;margin:6px 0 4px;font-style:italic">thinking</div>'+preBlock(blk.thinking||'','#8b949e'));
      }
      else {
        parts.push('<div style="font-size:11px;color:#8b949e;margin:6px 0 4px">'+esc(blk.type||'unknown block')+'</div>'+preBlock(prettyJson(blk),'#8b949e'));
      }
    }
    body=parts.length ? parts.join('') : preBlock(prettyJson(ev),'#79c0ff');
  }
  else if(t==='user'){
    bg='#0d1f1c'; headerColor='#7ee787'; label='tool_result';
    var msg2=ev.message||{}; var blks2=msg2.content||[];
    var rparts=[];
    for(var b2=0;b2<blks2.length;b2++){
      var blk2=blks2[b2];
      if(blk2.type==='tool_result'){
        var content=blk2.content;
        var rendered='';
        if(typeof content==='string'){
          rendered=content;
        } else if(Array.isArray(content)){
          var pieces=[];
          for(var ci=0;ci<content.length;ci++){
            var c=content[ci];
            if(c && typeof c==='object' && typeof c.text==='string') pieces.push(c.text);
            else pieces.push(prettyJson(c));
          }
          rendered=pieces.join('\\n---\\n');
        } else {
          rendered=prettyJson(content);
        }
        var errMark=blk2.is_error?'<span style="color:#f85149;font-weight:bold">[error]</span> ':'';
        var idLine=blk2.tool_use_id?'<span style="color:#6e7681">id: '+esc(blk2.tool_use_id)+'</span>':'';
        rparts.push(
          '<div style="font-size:11px;margin:6px 0 4px">'+errMark+idLine+'</div>'+
          preBlock(rendered, blk2.is_error?'#f85149':'#7ee787')
        );
      } else {
        rparts.push('<div style="font-size:11px;color:#8b949e;margin:6px 0 4px">'+esc(blk2.type||'block')+'</div>'+preBlock(prettyJson(blk2),'#8b949e'));
      }
    }
    body=rparts.length ? rparts.join('') : preBlock(prettyJson(ev),'#7ee787');
  }
  else if(t==='result'){
    bg='#0d2818'; headerColor='#3fb950'; label='result';
    var summary='turns='+(ev.num_turns||'-')+' · duration='+Math.round((ev.duration_ms||0)/1000)+'s · cost=$'+((ev.total_cost_usd||0).toFixed(4))+(ev.is_error?' · <span style="color:#f85149">ERROR</span>':'');
    body='<div style="font-size:12px;color:#c9d1d9;margin-bottom:6px">'+summary+'</div>';
    if(ev.result) body+='<div style="font-size:11px;color:#3fb950;margin:6px 0 4px">final assistant message</div>'+preBlock(ev.result,'#c9d1d9');
    body+='<div style="font-size:11px;color:#8b949e;margin:8px 0 4px">raw event</div>'+preBlock(prettyJson(ev),'#8b949e');
  }
  else {
    body=preBlock(prettyJson(ev),'#8b949e');
  }

  return '<div style="background:'+bg+';border-left:3px solid '+headerColor+';padding:10px 12px;border-radius:4px;margin-bottom:6px">'+
    '<div style="font-size:11px;color:'+headerColor+';text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">#'+idx+' · '+esc(label)+'</div>'+
    body+
  '</div>';
}

// ===== 7c. ROUNDS (queue runner: current round, next plan, history) =========
var roundsData={current:null,next:null,history:[],opened:null};

async function showRounds(){
  activeView='rounds';
  stopAutoRefresh();
  setActiveNav(-1);
  document.getElementById('content').innerHTML='<p style="color:#8b949e">Loading rounds...</p>';
  await refreshRounds();
  startAutoRefresh(refreshRounds);
}

async function refreshRounds(){
  if(roundsData.opened){await refreshRoundDetail();return}
  var f=saveFocus();
  var cur  = await api('rounds/current').catch(function(){return{}});
  var nxt  = await api('rounds/next').catch(function(){return{}});
  var hist = await api('rounds/history?limit=30').catch(function(){return{rounds:[]}});
  roundsData={current:cur||null, next:nxt||null, history:(hist&&hist.rounds)||[], opened:null};
  renderRounds();
  restoreFocus(f);
}

function statusDot(st){
  var c='#8b949e';
  if(st==='running') c='#d29922';
  else if(st==='done') c='#3fb950';
  else if(st==='failed') c='#f85149';
  else if(st==='skipped') c='#6e7681';
  return '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+c+';margin-right:6px"></span>';
}

function renderRounds(){
  var c = roundsData.current || {};
  var n = roundsData.next || {};
  // If the current round is an empty shell (round-promote made it but
  // sprint-lifecycle hasn't enqueued anything yet), display the previous
  // round's entries so the panel doesn't appear to wipe.
  var liveEntries = c.entries || [];
  var displayRound = c;
  var fellBack = false;
  if (!liveEntries.length && c.previous_round && (c.previous_round.entries||[]).length) {
    displayRound = c.previous_round;
    fellBack = true;
  }
  var entries = displayRound.entries || [];
  var cur = displayRound.cursor || 0;
  var counts = {queued:0, running:0, done:0, failed:0, skipped:0};
  for (var i=0;i<entries.length;i++){var s=entries[i].status||'queued';counts[s]=(counts[s]||0)+1}
  var nextEntries = (n && n.entries) || [];
  var stage = c.stage || '';
  var loopIter = (c.loop_iteration!=null) ? c.loop_iteration : '';

  var html='<div style="margin-bottom:14px"><span style="font-size:16px;color:#f0f6fc">Round Runner</span>'+
    (stage?' <span style="color:#58a6ff;font-size:13px;margin-left:10px">stage: '+esc(stage)+'</span>':'')+
    (loopIter!==''?' <span style="color:#8b949e;font-size:12px;margin-left:6px">(loop '+esc(String(loopIter))+')</span>':'')+
    '</div>';
  html+='<div class="summary-row">'+
    '<div class="summary-card"><div class="num">'+entries.length+'</div><div class="lbl">Entries '+(fellBack?'(prev round)':'this round')+'</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#d29922">'+counts.running+'</div><div class="lbl">Running</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+counts.done+'</div><div class="lbl">Done</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#f85149">'+(counts.failed||0)+'</div><div class="lbl">Failed</div></div>'+
    '<div class="summary-card"><div class="num">'+roundsData.history.length+'</div><div class="lbl">Recent rounds</div></div>'+
  '</div>';

  // Current round (or previous, if the current shell is empty)
  var heading = fellBack ? 'Most recent round' : 'Current round';
  html+='<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">'+heading+
    (displayRound.round_id?(' · '+esc(displayRound.round_id)):'')+
    (displayRound.source?(' <span style="color:#8b949e;font-size:12px">['+esc(displayRound.source)+']</span>'):'')+
    (fellBack?' <span style="color:#8b949e;font-size:12px;margin-left:6px">(complete; awaiting next stage enqueue)</span>':'')+
    '</h3>';
  if(!entries.length){
    html+='<p style="color:#8b949e;padding:12px">No round in flight. Round-runner will bootstrap one on next iteration.</p>';
  } else {
    html+='<table class="data-table"><thead><tr><th></th><th>#</th><th>Kind</th><th>Agent</th><th>Dept</th><th>Role</th><th>Ticket</th><th>Status</th><th>Started</th><th>Duration</th><th>Queued by</th></tr></thead><tbody>';
    for (var i=0;i<entries.length;i++){
      var e=entries[i];
      var pointer = (!fellBack && i===cur) ? '<span style="color:#d29922">▶</span>' : ((!fellBack && i<cur) ? '<span style="color:#6e7681">·</span>' : '');
      var dur = '-';
      if (e.duration_s) dur = fmtElapsed(e.duration_s);
      else if (e.status==='running' && e.started_ts) dur = fmtElapsed(Math.floor(Date.now()/1000) - e.started_ts);
      html+='<tr style="'+((!fellBack && i<cur)?'opacity:0.55':'')+'">'+
        '<td>'+pointer+'</td>'+
        '<td style="color:#6e7681">'+i+'</td>'+
        '<td>'+esc(e.kind||'?')+'</td>'+
        '<td>'+esc(e.agent||'?')+'</td>'+
        '<td>'+esc(e.dept||'-')+'</td>'+
        '<td>'+esc(e.role||'-')+'</td>'+
        '<td>'+esc(e.ticket||'-')+'</td>'+
        '<td>'+statusDot(e.status)+esc(e.status||'queued')+'</td>'+
        '<td title="'+esc(e.started_at||'')+'">'+(e.started_at?esc(localShort(e.started_at)):'-')+'</td>'+
        '<td>'+dur+'</td>'+
        '<td style="color:#8b949e">'+esc(e.queued_by||'')+'</td>'+
      '</tr>';
    }
    html+='</tbody></table>';
  }

  // (The "Next round (CEO's pending plan)" section was removed — under the
  // sprint-staged lifecycle the CEO no longer plans agent turns. Stage-advance
  // enqueues each stage's entries directly.)

  // History
  html+='<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">History (last '+roundsData.history.length+')</h3>';
  if(!roundsData.history.length){
    html+='<p style="color:#8b949e;padding:12px">No completed rounds yet.</p>';
  } else {
    html+='<table class="data-table"><thead><tr><th>Round</th><th>Source</th><th>Started</th><th>Duration</th><th>Entries</th><th>Done</th><th>Skipped</th><th>Failed</th><th></th></tr></thead><tbody>';
    for (var i=0;i<roundsData.history.length;i++){
      var r=roundsData.history[i];
      var sc=r.status_counts||{};
      html+='<tr>'+
        '<td>'+esc(r.round_id||'?')+'</td>'+
        '<td>'+esc(r.source||'-')+'</td>'+
        '<td title="'+esc(r.started_at||'')+'">'+esc(localShort(r.started_at))+'</td>'+
        '<td>'+(r.duration_s?fmtElapsed(r.duration_s):'-')+'</td>'+
        '<td>'+(r.entry_count||0)+'</td>'+
        '<td style="color:#3fb950">'+(sc.done||0)+'</td>'+
        '<td style="color:#6e7681">'+(sc.skipped||0)+'</td>'+
        '<td style="color:#f85149">'+(sc.failed||0)+'</td>'+
        '<td><button class="btn" data-rid="'+esc(r.round_id)+'" onclick="openRound(this.dataset.rid)">View</button></td>'+
      '</tr>';
    }
    html+='</tbody></table>';
  }

  document.getElementById('content').innerHTML=html;
}

async function openRound(rid){
  roundsData.opened=rid;
  await refreshRoundDetail();
}
function closeRound(){
  roundsData.opened=null;
  renderRounds();
}
async function refreshRoundDetail(){
  if(!roundsData.opened)return;
  var d = await api('rounds/'+encodeURIComponent(roundsData.opened)).catch(function(){return{error:'fetch failed'}});
  var html='<div style="margin-bottom:12px"><button class="btn" onclick="closeRound()">← Back to rounds</button> <span style="color:#8b949e;margin-left:12px">'+esc(roundsData.opened)+'</span></div>';
  if(d.error){html+='<p style="color:#f85149">'+esc(d.error)+'</p>';document.getElementById('content').innerHTML=html;return}
  if(d.rationale)html+='<p style="color:#c9d1d9;margin:6px 0 12px;font-style:italic">'+esc(d.rationale)+'</p>';
  var entries=d.entries||[];
  html+='<table class="data-table"><thead><tr><th>#</th><th>Kind</th><th>Agent</th><th>Dept</th><th>Role</th><th>Ticket</th><th>Status</th><th>Started</th><th>Duration</th><th>Exit</th></tr></thead><tbody>';
  for(var i=0;i<entries.length;i++){
    var e=entries[i];
    html+='<tr>'+
      '<td>'+i+'</td>'+
      '<td>'+esc(e.kind||'?')+'</td>'+
      '<td>'+esc(e.agent||'?')+'</td>'+
      '<td>'+esc(e.dept||'-')+'</td>'+
      '<td>'+esc(e.role||'-')+'</td>'+
      '<td>'+esc(e.ticket||'-')+'</td>'+
      '<td>'+statusDot(e.status)+esc(e.status||'?')+'</td>'+
      '<td>'+(e.started_at?esc(localShort(e.started_at)):'-')+'</td>'+
      '<td>'+(e.duration_s?fmtElapsed(e.duration_s):'-')+'</td>'+
      '<td>'+(e.exit_code==null?'-':e.exit_code)+'</td>'+
    '</tr>';
  }
  html+='</tbody></table>';
  document.getElementById('content').innerHTML=html;
}

// ===== 7d. GITHUB (mirrored issues + PRs) ===================================
var ghData = { issues: {}, prs: {} };

async function showGitHub(){
  activeView = 'github';
  stopAutoRefresh();
  setActiveNav(-1);
  document.getElementById('content').innerHTML = '<p style="color:#8b949e">Loading GitHub mirror...</p>';
  await refreshGitHub();
  startAutoRefresh(refreshGitHub);
}

async function refreshGitHub(){
  var f = saveFocus();
  var d = await api('gh/mirror').catch(function(){return {issues:{},prs:{}}});
  ghData = d || {issues:{},prs:{}};
  renderGitHub();
  restoreFocus(f);
}

function renderGitHub(){
  var issues = ghData.issues || {};
  var prs    = ghData.prs    || {};
  var iKeys = Object.keys(issues).sort(function(a,b){return Number(b)-Number(a)});
  var pKeys = Object.keys(prs).sort(function(a,b){return Number(b)-Number(a)});
  // Discriminator: prefer GH-truth state field (OPEN/CLOSED/MERGED) when
  // present; fall back to merged_at for legacy mirror entries written before
  // the dashboard added live-refresh.
  function prState(p){
    var s = (p.state || '').toLowerCase();
    if (s === 'merged') return 'merged';
    if (s === 'closed') return 'closed';
    if (s === 'open')   return 'open';
    return p.merged_at ? 'merged' : (p.closed_at ? 'closed' : 'open');
  }
  var openIssues   = iKeys.filter(function(k){return (issues[k].last_seen_state||'').toLowerCase()==='open'}).length;
  var closedIssues = iKeys.length - openIssues;
  var openPrs   = pKeys.filter(function(k){return prState(prs[k])==='open'}).length;
  var mergedPrs = pKeys.filter(function(k){return prState(prs[k])==='merged'}).length;
  var closedPrs = pKeys.filter(function(k){return prState(prs[k])==='closed'}).length;

  var html = '<div style="margin-bottom:14px;display:flex;align-items:center;gap:10px">'+
    '<span style="font-size:16px;color:#f0f6fc">GitHub Mirror</span>';
  if (ghData._sync_error) {
    html += '<span style="font-size:11px;color:#f85149" title="'+esc(ghData._sync_error)+'">⚠ live sync failed (showing last cached state)</span>';
  } else if (ghData._last_gh_sync_ms) {
    html += '<span style="font-size:11px;color:#6e7681">synced '+localShort(new Date(Number(ghData._last_gh_sync_ms)).toISOString())+'</span>';
  }
  html += '</div>';
  html += '<div class="summary-row">'+
    '<div class="summary-card"><div class="num">'+iKeys.length+'</div><div class="lbl">Total issues mirrored</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#3fb950">'+openIssues+'</div><div class="lbl">Open</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#6e7681">'+closedIssues+'</div><div class="lbl">Closed</div></div>'+
    '<div class="summary-card"><div class="num">'+pKeys.length+'</div><div class="lbl">Total PRs tracked</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#d29922">'+openPrs+'</div><div class="lbl">PRs open</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#a371f7">'+mergedPrs+'</div><div class="lbl">PRs merged</div></div>'+
    '<div class="summary-card"><div class="num" style="color:#6e7681">'+closedPrs+'</div><div class="lbl">PRs closed</div></div>'+
  '</div>';

  html += '<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">Issues</h3>';
  if (!iKeys.length) {
    html += '<p style="color:#8b949e;padding:12px">No GitHub issues mirrored yet. The poller runs between rounds (round-promote.sh). Check that gh is authed and a remote is configured.</p>';
  } else {
    html += '<table class="data-table"><thead><tr><th>#</th><th>Hive ticket</th><th>State</th><th>Mirrored</th><th>Last synced</th></tr></thead><tbody>';
    for (var i=0;i<iKeys.length;i++){
      var k = iKeys[i], it = issues[k];
      var stColor = it.last_seen_state==='open' ? '#3fb950' : '#6e7681';
      html += '<tr>'+
        '<td><a href="#" onclick="return false" style="color:#58a6ff">#'+esc(k)+'</a></td>'+
        '<td>'+esc(it.ticket_id||'?')+'</td>'+
        '<td style="color:'+stColor+'">'+esc(it.last_seen_state||'?')+'</td>'+
        '<td title="'+esc(it.mirrored_at||'')+'">'+esc(localShort(it.mirrored_at))+'</td>'+
        '<td title="'+esc(it.last_synced_at||'')+'">'+esc(localShort(it.last_synced_at))+'</td>'+
      '</tr>';
    }
    html += '</tbody></table>';
  }

  html += '<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">Pull Requests</h3>';
  if (!pKeys.length) {
    html += '<p style="color:#8b949e;padding:12px">No PRs tracked yet. PRs are opened by gh-pr-open.sh when a ticket transitions to in-review.</p>';
  } else {
    html += '<table class="data-table"><thead><tr><th>PR</th><th>Hive ticket</th><th>Branch</th><th>State</th><th>Opened</th><th>Closed/Merged</th></tr></thead><tbody>';
    for (var i=0;i<pKeys.length;i++){
      var k = pKeys[i], pr = prs[k];
      var st = prState(pr);
      var stColor = st==='merged' ? '#a371f7' : (st==='closed' ? '#6e7681' : '#d29922');
      var endTime = pr.merged_at || pr.closed_at;
      html += '<tr>'+
        '<td>#'+esc(k)+'</td>'+
        '<td>'+esc(pr.ticket_id||'?')+'</td>'+
        '<td style="color:#8b949e">'+esc(pr.branch||'?')+'</td>'+
        '<td style="color:'+stColor+'">'+esc(st)+'</td>'+
        '<td title="'+esc(pr.opened_at||'')+'">'+esc(localShort(pr.opened_at))+'</td>'+
        '<td title="'+esc(endTime||'')+'">'+(endTime?esc(localShort(endTime)):'-')+'</td>'+
      '</tr>';
    }
    html += '</tbody></table>';
  }

  document.getElementById('content').innerHTML = html;
}

// ===== 7e. RELEASES (release records + history) =============================
var releaseData = { current: null, history: [] };

async function showReleases(){
  activeView = 'releases';
  stopAutoRefresh();
  setActiveNav(-1);
  document.getElementById('content').innerHTML = '<p style="color:#8b949e">Loading releases...</p>';
  await refreshReleases();
  startAutoRefresh(refreshReleases);
}

async function refreshReleases(){
  var f = saveFocus();
  var c = await api('releases/current').catch(function(){return null});
  var h = await api('releases/history?limit=30').catch(function(){return {releases:[]}});
  releaseData = { current: c, history: (h && h.releases) || [] };
  renderReleases();
  restoreFocus(f);
}

function verdictColor(v){
  if (v === 'GO') return '#3fb950';
  if (v === 'NO-GO') return '#f85149';
  return '#8b949e';
}
function phaseColor(p){
  if (p === 'done') return '#3fb950';
  if (p === 'failed') return '#f85149';
  if (p === 'adversarial') return '#d29922';
  return '#a371f7';
}

function renderReleases(){
  var c = releaseData.current;
  var html = '<div style="margin-bottom:14px"><span style="font-size:16px;color:#f0f6fc">Releases</span></div>';

  html += '<h3 style="color:#f0f6fc;margin:6px 0 8px;font-size:14px">Current</h3>';
  if (!c || c.error) {
    html += '<p style="color:#8b949e;padding:12px">No release in flight. CEO can request one during standup-synth by including a <code>release-cycle vX.Y.Z</code> entry in the next plan.</p>';
  } else {
    var pc = phaseColor(c.phase || '');
    var vc = verdictColor(c.adversarial_verdict || '');
    html += '<div style="background:#161b22;border-left:3px solid '+pc+';padding:10px 14px;border-radius:4px">'+
      '<div style="font-size:14px;color:#f0f6fc;margin-bottom:6px"><b>'+esc(c.version||'?')+'</b> '+
        '<span style="color:#8b949e">phase=</span><span style="color:'+pc+'">'+esc(c.phase||'?')+'</span> '+
        '<span style="color:#8b949e">verdict=</span><span style="color:'+vc+'">'+esc(c.adversarial_verdict||'pending')+'</span>'+
      '</div>'+
      '<div style="font-size:12px;color:#c9d1d9">started '+esc(localShort(c.started_at||''))+
        (c.ended_at?' · ended '+esc(localShort(c.ended_at)):'')+'</div>'+
      (c.release_url ? '<div style="margin-top:6px"><a href="'+esc(c.release_url)+'" target="_blank" style="color:#58a6ff">'+esc(c.release_url)+'</a></div>' : '')+
      (c.tarball_sha256 ? '<div style="font-size:11px;color:#6e7681;margin-top:4px;font-family:ui-monospace,monospace">sha256: '+esc(c.tarball_sha256)+'</div>' : '')+
      (c.blockers && c.blockers.length ? '<div style="margin-top:8px;color:#f85149"><b>Blockers:</b><ul>'+c.blockers.map(function(b){return '<li>'+esc(b)+'</li>'}).join('')+'</ul></div>' : '')+
      (c.go_rationale ? '<div style="margin-top:8px;color:#3fb950"><b>GO rationale:</b> '+esc(c.go_rationale)+'</div>' : '')+
      (c.nogo_rationale ? '<div style="margin-top:8px;color:#f85149"><b>NO-GO rationale:</b> '+esc(c.nogo_rationale)+'</div>' : '')+
    '</div>';
  }

  // History — sourced from sprint archive AND gh release list (merged on
  // version tag). Each row shows where the data came from so it's obvious
  // when GitHub has a release that the sprint archive missed (or vice versa).
  html += '<h3 style="color:#f0f6fc;margin:18px 0 8px;font-size:14px">History (last '+releaseData.history.length+')</h3>';
  if (!releaseData.history.length) {
    html += '<p style="color:#8b949e;padding:12px">No releases recorded. (If you expected GitHub releases here, check that <code>gh auth status</code> works in this project — the dashboard pulls from <code>gh release list</code>.)</p>';
  } else {
    function statusColor(s){
      if(s==='published')return '#3fb950';
      if(s==='failed')return '#f85149';
      if(s==='draft')return '#d29922';
      if(s==='prerelease')return '#a371f7';
      return '#8b949e';
    }
    function sourceBadge(src){
      if(!src)return '';
      var tip='';
      if(src.indexOf('gh')>=0&&src.indexOf('sprint')>=0) tip='Both sources agree: sprint archive recorded the release AND GitHub has it published.';
      else if(src==='gh') tip='Found on GitHub but NOT in the sprint archive (release happened outside the sprint-staged pipeline, or the sprint state was lost).';
      else if(src==='sprint-archive') tip='Recorded by sprint archive but NOT on GitHub yet (publish step may have failed, or no remote configured).';
      var color=src==='gh'?'#d29922':(src==='sprint-archive'?'#8b949e':'#3fb950');
      return '<span title="'+esc(tip)+'" style="color:'+color+';font-size:10px;border:1px solid '+color+';padding:1px 5px;border-radius:3px;margin-left:6px">'+esc(src)+'</span>';
    }
    html += '<table class="data-table"><thead><tr><th>Version</th><th>Status</th><th>Sprint</th><th>Published</th><th>Tarball</th><th>Source</th></tr></thead><tbody>';
    for (var i=0;i<releaseData.history.length;i++){
      var r = releaseData.history[i];
      var sc = statusColor(r.status||'');
      var when = r.gh_published_at || r.ended || '';
      html += '<tr>'+
        '<td><b>'+esc(r.version||'?')+'</b></td>'+
        '<td style="color:'+sc+'">'+esc(r.status||'unknown')+'</td>'+
        '<td style="color:#8b949e;font-size:11px">'+esc(r.sprint_id||'-')+'</td>'+
        '<td title="'+esc(when)+'">'+(when?esc(localShort(when)):'-')+'</td>'+
        '<td style="color:#6e7681;font-family:ui-monospace,monospace;font-size:10px">'+esc(r.tarball||'-')+'</td>'+
        '<td>'+sourceBadge(r.source)+'</td>'+
      '</tr>';
    }
    html += '</tbody></table>';
  }

  document.getElementById('content').innerHTML = html;
}

// ===== 8. INBOX/OUTBOX =====
var commsTab='compose';
var commsInbox=null;
var commsOutbox=null;

async function showInbox(){
  activeView='inbox';
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading...</p>';
  commsInbox=await api('inbox');
  commsOutbox=await api('outbox');
  renderComms();
}

function switchCommsTab(tab){commsTab=tab;document.querySelectorAll('.comms-tab').forEach(function(el){el.classList.toggle('active',el.dataset.tab===tab)});renderCommsContent()}

function renderComms(){
  var qsRaw=(commsOutbox.userQuestions||'')+(commsOutbox.ceoEscalations||'');
  var qItems=parseQuestionItems(qsRaw);
  var qDismissed=getDismissedQuestions();
  var newCount=qItems.filter(function(it){return !qDismissed[it.hash]}).length;
  var hasApprovals=!!commsOutbox.pendingApprovals;
  var badge=newCount||hasApprovals?' <span style="background:#da3633;color:#fff;font-size:10px;padding:1px 5px;border-radius:8px">'+(newCount||'!')+'</span>':'';
  var c=document.getElementById('content');
  var html='<div style="margin-bottom:12px;display:flex;align-items:center"><span style="font-size:16px;color:#f0f6fc">Communications</span>'+badge+'<button class="btn" style="margin-left:auto;font-size:11px;padding:4px 10px" onclick="clearOutbox()">Clear Resolved</button></div>';
  html+='<div class="wt-tabs" style="margin-bottom:0">';
  var tabs=[{k:'compose',l:'Send Message'},{k:'questions',l:'Questions'+badge},{k:'notifications',l:'Notifications'},{k:'history',l:'History'},{k:'prefs',l:'Preferences'}];
  for(var i=0;i<tabs.length;i++){
    html+='<div class="wt-tab comms-tab'+(commsTab===tabs[i].k?' active':'')+'" data-tab="'+tabs[i].k+'" onclick="switchCommsTab(\\''+tabs[i].k+'\\')">'+tabs[i].l+'</div>';
  }
  html+='</div><div id="comms-content" style="background:#161b22;border:1px solid #30363d;border-top:none;border-radius:0 0 8px 8px;padding:16px;min-height:300px"></div>';
  c.innerHTML=html;
  renderCommsContent();
}

function renderCommsContent(){
  var el=document.getElementById('comms-content');
  if(!el)return;
  var html='';

  if(commsTab==='compose'){
    html+='<div style="margin-bottom:12px;font-size:13px;color:#8b949e">Send a message, feature request, or bug report to the company.</div>';
    html+='<div style="display:flex;gap:8px;margin-bottom:8px">';
    html+='<select id="msg-type" style="background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:6px 10px;border-radius:6px;font-family:inherit;font-size:12px"><option value="feature">Feature Request</option><option value="bug">Bug Report</option><option value="feedback">Feedback</option></select>';
    html+='<select id="msg-dept" style="background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:6px 10px;border-radius:6px;font-family:inherit;font-size:12px"><option value="auto">Auto-route</option>';
    if(departments.length)for(var di=0;di<departments.length;di++){html+='<option>'+esc(departments[di].name)+'</option>'}
    html+='</select></div>';
    html+='<textarea id="user-msg" rows="4" style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:8px 12px;border-radius:6px;font-family:inherit;font-size:13px;resize:vertical" placeholder="Describe what you want..."></textarea>';
    html+='<div style="display:flex;gap:8px;margin-top:8px;align-items:center">';
    html+='<button class="btn btn-primary" onclick="sendUserRequest()">Send</button>';
    html+='<span id="request-confirmation" style="display:none;color:#3fb950;font-size:12px"></span>';
    html+='</div>';

  } else if(commsTab==='questions'){
    var qs=(commsOutbox.userQuestions||'')+(commsOutbox.ceoEscalations||'');
    var items=parseQuestionItems(qs);
    var dismissed=getDismissedQuestions();
    var newItems=items.filter(function(it){return !dismissed[it.hash]});
    var oldItems=items.filter(function(it){return dismissed[it.hash]});
    if(newItems.length){
      html+='<div style="display:flex;align-items:center;margin-bottom:10px"><span style="font-size:12px;color:#f0883e;font-weight:600">'+newItems.length+' new</span>';
      html+='<button class="btn" style="margin-left:auto;font-size:11px;padding:3px 8px" onclick="dismissAllQuestions()">Dismiss All</button></div>';
      for(var qi=0;qi<newItems.length;qi++){
        var it=newItems[qi];
        var isResolved=it.text.indexOf('RESOLVED')!==-1||it.text.indexOf('Status**: Complete')!==-1||it.text.indexOf('Status**: RESOLVED')!==-1;
        var borderColor=isResolved?'#3fb950':'#f0883e';
        html+='<div style="border-left:3px solid '+borderColor+';padding:8px 12px;margin-bottom:10px;background:#0d1117;border-radius:0 6px 6px 0;position:relative">';
        if(isResolved)html+='<span style="position:absolute;top:8px;right:8px;font-size:10px;background:#238636;color:#fff;padding:1px 6px;border-radius:8px">Resolved</span>';
        html+='<div class="md-content" style="font-size:13px">'+renderMarkdown(it.text)+'</div>';
        html+='<div style="display:flex;gap:6px;margin-top:6px">';
        html+='<button class="btn" style="font-size:11px;padding:3px 8px" onclick="dismissQuestion('+String.fromCharCode(39)+it.hash+String.fromCharCode(39)+')">Dismiss</button>';
        if(!isResolved)html+='<button class="btn" style="font-size:11px;padding:3px 8px;color:#f0883e" onclick="replyToQuestion('+qi+')">Reply</button>';
        html+='</div></div>';
      }
      html+='<div id="question-reply-area"></div>';
    } else {
      html+='<p style="color:#8b949e;font-size:13px">No new questions from the company.</p>';
    }
    if(oldItems.length){
      html+='<details style="margin-top:12px"><summary style="color:#484f58;font-size:12px;cursor:pointer">'+oldItems.length+' dismissed</summary>';
      for(var oi=0;oi<oldItems.length;oi++){
        html+='<div style="border-left:2px solid #21262d;padding:6px 12px;margin-top:6px;opacity:0.6"><div class="md-content" style="font-size:12px">'+renderMarkdown(oldItems[oi].text)+'</div>';
        html+='<button class="btn" style="font-size:10px;padding:2px 6px;margin-top:4px" onclick="undismissQuestion('+String.fromCharCode(39)+oldItems[oi].hash+String.fromCharCode(39)+')">Restore</button></div>';
      }
      html+='</details>';
    }
    if(commsOutbox.pendingApprovals){
      html+='<div style="margin-top:16px;border-top:1px solid #30363d;padding-top:12px"><div style="font-size:12px;color:#f0883e;margin-bottom:8px;font-weight:600">Pending Approvals</div><div class="md-content">'+renderMarkdown(commsOutbox.pendingApprovals)+'</div></div>';
    }

  } else if(commsTab==='notifications'){
    if(commsOutbox.notifications){
      html+='<div class="md-content">'+renderMarkdown(commsOutbox.notifications)+'</div>';
    } else {
      html+='<p style="color:#8b949e;font-size:13px">No notifications.</p>';
    }

  } else if(commsTab==='history'){
    if(commsOutbox.userAnswers){
      html+='<div style="font-size:12px;color:#3fb950;margin-bottom:8px;font-weight:600">Your Answers</div>';
      html+='<div class="md-content" style="border-left:3px solid #3fb950;padding-left:12px;margin-bottom:16px">'+renderMarkdown(commsOutbox.userAnswers)+'</div>';
    }
    if(commsInbox.userRequests){
      html+='<div style="font-size:12px;color:#58a6ff;margin-bottom:8px;font-weight:600">Your Requests</div>';
      html+='<div class="md-content" style="border-left:3px solid #58a6ff;padding-left:12px;margin-bottom:16px">'+renderMarkdown(commsInbox.userRequests)+'</div>';
    }
    if(commsInbox.ticketRequests&&commsInbox.ticketRequests.length){
      html+='<div style="font-size:12px;color:#8b949e;margin-bottom:8px;font-weight:600">Agent Ticket Requests ('+commsInbox.ticketRequests.length+')</div>';
      for(var i=0;i<Math.min(commsInbox.ticketRequests.length,10);i++){
        var tr=commsInbox.ticketRequests[i];
        html+='<div style="border-left:3px solid #30363d;padding-left:12px;margin-bottom:8px;font-size:12px"><span style="color:#8b949e">'+esc(tr.file)+'</span> '+localDate(tr.modified)+'</div>';
      }
      if(commsInbox.ticketRequests.length>10)html+='<p style="color:#484f58;font-size:11px">...and '+(commsInbox.ticketRequests.length-10)+' more</p>';
    }
    if(!commsOutbox.userAnswers&&!commsInbox.userRequests&&!(commsInbox.ticketRequests&&commsInbox.ticketRequests.length)){
      html+='<p style="color:#8b949e;font-size:13px">No communication history yet.</p>';
    }

  } else if(commsTab==='prefs'){
    if(commsOutbox.userPreferences){
      html+='<div style="font-size:12px;color:#8b949e;margin-bottom:8px">Stored preferences (from your previous answers):</div>';
      html+='<pre style="color:#c9d1d9;font-size:12px;background:#0d1117;padding:12px;border-radius:6px;overflow-x:auto">'+esc(JSON.stringify(commsOutbox.userPreferences,null,2))+'</pre>';
    } else {
      html+='<p style="color:#8b949e;font-size:13px">No preferences stored yet. When you answer company questions, your info is saved here.</p>';
    }
  }

  el.innerHTML=html;
}

async function clearOutbox(){
  if(!confirm('Archive all resolved outbox items?'))return;
  var res=await api('outbox-clear',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  if(res.ok)showInbox();
}

function parseQuestionItems(raw){
  if(!raw)return [];
  var sections=raw.split(/\\n---\\n/);
  var items=[];
  for(var i=0;i<sections.length;i++){
    var s=sections[i].trim();
    if(!s||s==='(No pending questions)')continue;
    // Strip top-level headings that are just file titles
    if(/^#\\s+(User Questions|CEO Escalations)\\s*$/m.test(s)){
      s=s.replace(/^#\\s+(User Questions|CEO Escalations)[^\\n]*\\n*/m,'').trim();
      if(!s)continue;
    }
    // Generate a stable hash from first 200 chars
    var hashSrc=s.slice(0,200);
    var h=0;for(var j=0;j<hashSrc.length;j++){h=((h<<5)-h)+hashSrc.charCodeAt(j);h|=0}
    items.push({text:s,hash:'q'+Math.abs(h).toString(36)});
  }
  return items;
}
function getDismissedQuestions(){try{return JSON.parse(localStorage.getItem('dismissed-questions')||'{}')}catch(e){return {}}}
function saveDismissedQuestions(d){localStorage.setItem('dismissed-questions',JSON.stringify(d))}
function dismissQuestion(hash){var d=getDismissedQuestions();d[hash]=Date.now();saveDismissedQuestions(d);renderCommsContent()}
function undismissQuestion(hash){var d=getDismissedQuestions();delete d[hash];saveDismissedQuestions(d);renderCommsContent()}
function dismissAllQuestions(){
  var qs=(commsOutbox.userQuestions||'')+(commsOutbox.ceoEscalations||'');
  var items=parseQuestionItems(qs);
  var d=getDismissedQuestions();
  for(var i=0;i<items.length;i++)d[items[i].hash]=Date.now();
  saveDismissedQuestions(d);renderCommsContent();
}
var currentQuestionItems=[];
function replyToQuestion(idx){
  var qs=(commsOutbox.userQuestions||'')+(commsOutbox.ceoEscalations||'');
  var items=parseQuestionItems(qs);
  var dismissed=getDismissedQuestions();
  var newItems=items.filter(function(it){return !dismissed[it.hash]});
  var item=newItems[idx];
  if(!item)return;
  var area=document.getElementById('question-reply-area');
  if(!area)return;
  // Extract a short subject from the question
  var subj=item.text.split('\\n')[0].replace(/^#+\\s*/,'').slice(0,80);
  area.innerHTML='<div style="margin-top:8px;border-top:1px solid #30363d;padding-top:8px"><div style="font-size:11px;color:#8b949e;margin-bottom:4px">Replying to: '+esc(subj)+'</div>'
    +'<textarea id="user-answer" rows="3" style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:8px 12px;border-radius:6px;font-family:inherit;font-size:13px;resize:vertical" placeholder="Type your answer..."></textarea>'
    +'<div style="display:flex;gap:8px;margin-top:6px;align-items:center">'
    +'<button class="btn btn-primary" onclick="sendUserAnswer()">Send Answer</button>'
    +'<button class="btn" onclick="document.getElementById('+String.fromCharCode(39)+'question-reply-area'+String.fromCharCode(39)+').innerHTML='+String.fromCharCode(39,39)+'">Cancel</button>'
    +'<span id="answer-confirmation" style="display:none;color:#3fb950;font-size:12px"></span></div></div>';
  document.getElementById('user-answer').focus();
}
async function sendUserAnswer(){
  var el=document.getElementById('user-answer');
  var answer=el.value.trim();
  if(!answer){alert('Type an answer first');return}
  var btn=event.target;btn.disabled=true;btn.textContent='Sending...';
  var res=await api('user-answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answer:answer})});
  el.value='';
  btn.disabled=false;btn.textContent='Send Answer';
  if(res.ok){
    var conf=document.getElementById('answer-confirmation');
    if(conf){conf.style.display='block';conf.textContent='Sent at '+localTime(res.timestamp)}
    setTimeout(showInbox,2000);
  }
}

async function sendUserRequest(){
  var el=document.getElementById('user-msg');
  var msg=el.value.trim();
  if(!msg){alert('Type a message first');return}
  var type=document.getElementById('msg-type').value;
  var dept=document.getElementById('msg-dept').value;
  var btn=event.target;btn.disabled=true;btn.textContent='Sending...';
  var res;
  try {
    res=await api('user-request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,type:type,dept:dept})});
  } catch(e){
    alert('Send failed: network or server error.\\n\\n'+(e&&e.message?e.message:e));
    btn.disabled=false;btn.textContent='Send';
    return;
  }
  btn.disabled=false;btn.textContent='Send';
  if(!res||res.error||res.ok===false){
    alert('Send failed:\\n\\n'+((res&&(res.error||res.stderr||res.stdout))||'unknown error (check .company/logs/mcp-server.log)'));
    return;
  }
  el.value='';
  var conf=document.getElementById('request-confirmation');
  if(conf){conf.style.display='block';conf.textContent='Sent'+(res.timestamp?' at '+localTime(res.timestamp):'')}
  setTimeout(showInbox,2000);
}

// ===== 9. MARKETING =====
var currentMarketingTab='reports';
async function showMarketing(){
  activeView='marketing';
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading marketing data...</p>';
  var data=await api('marketing');
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Marketing</span></div>';
  html+='<div class="tab-bar">'+
    '<button class="tab-btn'+(currentMarketingTab==='reports'?' active':'')+'" onclick="currentMarketingTab=\\'reports\\';showMarketing()">Reports ('+((data.reports||[]).length)+')</button>'+
    '<button class="tab-btn'+(currentMarketingTab==='output'?' active':'')+'" onclick="currentMarketingTab=\\'output\\';showMarketing()">Output ('+((data.output||[]).length)+')</button>'+
  '</div>';
  var files=currentMarketingTab==='reports'?(data.reports||[]):(data.output||[]);
  if(!files.length){
    html+='<p style="color:#8b949e;text-align:center;padding:40px">No '+currentMarketingTab+' files found</p>';
  } else {
    for(var i=0;i<files.length;i++){
      var f=files[i];
      var sizeStr=f.size>1024?(f.size/1024).toFixed(1)+'K':f.size+'B';
      html+='<div class="panel-item" style="background:#161b22;border:1px solid #30363d;border-radius:6px;margin:6px 0;cursor:pointer" onclick="showMarketingFile(\\''+esc(currentMarketingTab)+'\\',\\''+esc(f.file)+'\\')">'+
        '<span style="color:#f0f6fc">'+esc(f.file)+'</span>'+
        '<span class="item-meta">'+localDate(f.modified)+' &middot; '+sizeStr+'</span>'+
      '</div>';
    }
  }
  c.innerHTML=html;
}

async function showMarketingFile(type,file){
  var data=await api('marketing/'+encodeURIComponent(type)+'/'+encodeURIComponent(file));
  var m=document.getElementById('modal');
  m.innerHTML='<h2>'+esc(file)+'</h2>'+
    '<div class="md-content" style="max-height:60vh;overflow-y:auto">'+renderMarkdown(data.content||'')+'</div>'+
    '<div class="actions"><button class="btn" onclick="closeModal()">Close</button></div>';
  openModal();
}

// ===== 10. GIT =====
async function showGit(){
  activeView='git';
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading git info...</p>';
  var data=await api('git');
  if(data.error&&!data.log){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Git Repository</span></div>';

  html+='<div style="margin-bottom:16px">';
  html+='<span style="font-size:13px;color:#8b949e">Current branch: </span>';
  html+='<span class="branch-badge current">'+esc(data.current_branch||'unknown')+'</span>';
  html+='</div>';

  if(data.branches&&data.branches.length>1){
    html+='<details style="margin-bottom:16px"><summary class="section-hdr" style="cursor:pointer;user-select:none">Branches ('+data.branches.length+')</summary>';
    html+='<div style="margin-top:8px;display:flex;flex-direction:column;gap:2px">';
    for(var i=0;i<data.branches.length;i++){
      var b=data.branches[i];
      var isCurrent=b===data.current_branch;
      html+='<a href="#" onclick="showBranchHistory(\\\''+esc(b)+'\\\');return false" class="branch-badge'+(isCurrent?' current':'')+'" style="text-decoration:none;width:fit-content">'+esc(b)+'</a>';
    }
    html+='</div></details>';
  }

  // Worktrees
  var wts=data.worktrees||[];
  if(wts.length){
    html+='<h3 class="section-hdr">Worktrees ('+wts.length+')</h3>';
    html+='<table class="data-table"><thead><tr><th>Branch</th><th>Ticket</th><th>Path</th><th>Status</th><th title="Files changed vs main, plus uncommitted edits">Files</th><th title="Commits the branch has that main does not">Commits</th><th>HEAD</th></tr></thead><tbody>';
    for(var i=0;i<wts.length;i++){
      var w=wts[i];
      var statusBadge='';
      if(w.isMain)statusBadge='<span class="branch-badge current">main</span>';
      else if(w.detached)statusBadge='<span style="color:#f0883e;font-size:11px">detached</span>';
      else if(w.dirty)statusBadge='<span style="color:#d29922;font-size:11px">dirty</span>';
      else statusBadge='<span style="color:#3fb950;font-size:11px">clean</span>';
      var wtClickName=esc(w.branch||(w.path||'').split('/').pop()||'');
      // Files cell: total + breakdown tooltip (e.g. "5 (3 committed + 2 unstaged)")
      var filesCell='-';
      if(w.changedFiles>0){
        var breakdown=[];
        if(w.committedFiles>0) breakdown.push(w.committedFiles+' committed');
        if(w.uncommittedFiles>0) breakdown.push(w.uncommittedFiles+' unstaged');
        var tip=breakdown.join(' + ');
        var color=w.uncommittedFiles>0?'#d29922':'#58a6ff';
        filesCell='<span style="color:'+color+'" title="'+tip+'">'+w.changedFiles+'</span>';
      }
      var commitsCell=w.aheadCommits>0
        ? '<span style="color:#58a6ff" title="Commits ahead of main">'+w.aheadCommits+'</span>'
        : '-';
      html+='<tr class="wt-clickable" onclick="showWorktree(\\''+wtClickName+'\\',\\''+esc(w.path||'')+'\\',\\''+esc(w.branch||'')+'\\',\\''+esc(w.ticket||'')+'\\',\\''+esc((w.head||'').slice(0,8))+'\\','+!!w.isMain+')" style="'+(w.isMain?'background:#161b22':'')+'" title="Click to browse this worktree">';
      html+='<td><span class="branch-badge'+(w.isMain?' current':'')+'">'+esc(w.branch||'?')+'</span></td>';
      html+='<td>'+(w.ticket?'<span style="color:#58a6ff">'+esc(w.ticket)+'</span>':'-')+'</td>';
      html+='<td style="font-size:11px;color:#8b949e;max-width:300px;overflow:hidden;text-overflow:ellipsis">'+esc(w.path||'')+'</td>';
      html+='<td>'+statusBadge+'</td>';
      html+='<td>'+filesCell+'</td>';
      html+='<td>'+commitsCell+'</td>';
      html+='<td style="font-size:10px;color:#484f58">'+esc((w.head||'').slice(0,8))+'</td>';
      html+='</tr>';
    }
    html+='</tbody></table>';
  }

  html+='<h3 class="section-hdr">Recent Commits</h3>';
  var log=data.log||[];
  if(log.length){
    html+='<div class="timeline">';
    for(var i=0;i<log.length;i++){
      var cm=log[i];
      html+='<div class="timeline-item" style="cursor:pointer" onclick="showCommitDiff(\\''+esc(cm.hash)+'\\')">'+
        '<span class="commit-hash">'+esc(cm.hash)+'</span>'+
        '<span class="commit-msg">'+esc(cm.message)+'</span>'+
        '<span class="commit-date">'+localDate(cm.date)+'</span>'+
      '</div>';
    }
    html+='</div>';
  } else {
    html+='<p style="color:#8b949e;font-size:13px">No commits found</p>';
  }
  c.innerHTML=html;
  // No auto-refresh on git page - too expensive. User can navigate away and back to refresh.
}

// ===== WORKTREE BROWSER =====
var wtCurrentName='';
var wtCurrentPath='';
var wtCurrentBranch='';
var wtCurrentTicket='';
var wtCurrentHead='';
var wtCurrentTab='files';
var wtBrowsePath='';
var wtDiffData=null;
var wtExpandedDiffs={};

function getFileIcon(name) {
  var ext = (name || '').split('.').pop().toLowerCase();
  if (ext === 'ts' || ext === 'tsx') return '<span style="color:#3178c6">TS</span>';
  if (ext === 'js' || ext === 'jsx') return '<span style="color:#f7df1e">JS</span>';
  if (ext === 'json') return '<span style="color:#8b949e">{}</span>';
  if (ext === 'md') return '<span style="color:#58a6ff">MD</span>';
  if (ext === 'css' || ext === 'scss') return '<span style="color:#563d7c">CS</span>';
  if (ext === 'html') return '<span style="color:#e44d26">HT</span>';
  if (ext === 'py') return '<span style="color:#3572a5">PY</span>';
  if (ext === 'rs') return '<span style="color:#dea584">RS</span>';
  if (ext === 'go') return '<span style="color:#00add8">GO</span>';
  if (ext === 'sh' || ext === 'bash') return '<span style="color:#89e051">SH</span>';
  if (ext === 'yml' || ext === 'yaml') return '<span style="color:#cb171e">YM</span>';
  if (ext === 'toml') return '<span style="color:#9c4221">TM</span>';
  if (ext === 'svg' || ext === 'png' || ext === 'jpg' || ext === 'gif') return '<span style="color:#a371f7">IM</span>';
  return '<span style="color:#8b949e">--</span>';
}

function formatFileSize(bytes) {
  if (bytes === 0) return '';
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return bytes + ' B';
}

async function showWorktree(name, wtpath, branch, ticket, head, isMain) {
  activeView='worktree';
  stopAutoRefresh();
  setActiveNav(-1);
  wtCurrentName = name;
  wtCurrentPath = wtpath || '';
  wtCurrentBranch = branch || name;
  wtCurrentTicket = ticket || '';
  wtCurrentHead = head || '';
  wtCurrentTab = 'files';
  wtBrowsePath = '';
  wtDiffData = null;
  wtExpandedDiffs = {};
  renderWorktreeView();
  loadWorktreeFiles('');
}

function renderWorktreeView() {
  var c = document.getElementById('content');
  var html = '';
  // Header bar
  html += '<div class="wt-header">';
  html += '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">';
  html += '<button class="btn" onclick="showGit()">&larr; Back to Git</button>';
  html += '<span class="branch-badge">'+esc(wtCurrentBranch)+'</span>';
  if (wtCurrentTicket) html += '<span style="color:#58a6ff;font-size:13px">'+esc(wtCurrentTicket)+'</span>';
  if (wtCurrentHead) html += '<span style="font-size:11px;color:#484f58">'+esc(wtCurrentHead)+'</span>';
  html += '</div>';
  if (wtCurrentPath) html += '<div style="font-size:11px;color:#484f58;margin-top:6px">'+esc(wtCurrentPath)+'</div>';
  html += '</div>';
  // Tab bar
  html += '<div class="wt-tabs">';
  html += '<button class="wt-tab'+(wtCurrentTab==='files'?' active':'')+'" onclick="switchWtTab(\\'files\\')">Files</button>';
  html += '<button class="wt-tab'+(wtCurrentTab==='changes'?' active':'')+'" onclick="switchWtTab(\\'changes\\')">Changes</button>';
  html += '</div>';
  // Content area
  html += '<div id="wt-content"><p style="color:#8b949e">Loading...</p></div>';
  c.innerHTML = html;
}

function switchWtTab(tab) {
  wtCurrentTab = tab;
  // Update tab highlight without full re-render
  document.querySelectorAll('.wt-tab').forEach(function(t) {
    t.classList.toggle('active', t.textContent.toLowerCase().indexOf(tab) === 0);
  });
  if (tab === 'files') {
    loadWorktreeFiles(wtBrowsePath);
  } else {
    loadWorktreeDiff();
  }
}

function buildBreadcrumb(browsePath) {
  var html = '<div class="breadcrumb">';
  html += '<a onclick="browseWorktreeDir(\\'\\')">root</a>';
  if (browsePath) {
    var parts = browsePath.split('/');
    var accumulated = '';
    for (var i = 0; i < parts.length; i++) {
      accumulated += (i > 0 ? '/' : '') + parts[i];
      html += ' / ';
      if (i === parts.length - 1) {
        html += '<span>' + esc(parts[i]) + '</span>';
      } else {
        html += '<a onclick="browseWorktreeDir(\\'' + esc(accumulated) + '\\')">' + esc(parts[i]) + '</a>';
      }
    }
  }
  html += '</div>';
  return html;
}

async function loadWorktreeFiles(browsePath) {
  wtBrowsePath = browsePath;
  var content = document.getElementById('wt-content');
  if (!content) return;
  content.innerHTML = '<p style="color:#8b949e">Loading files...</p>';
  var data = await api('git/worktrees/' + encodeURIComponent(wtCurrentName) + '/files?path=' + encodeURIComponent(browsePath));
  if (data.error) {
    content.innerHTML = '<p style="color:#f85149">' + esc(data.error) + '</p>';
    return;
  }
  if (data.type === 'directory') {
    var html = buildBreadcrumb(browsePath);
    html += '<div class="file-list">';
    // Show parent dir link if not at root
    if (browsePath) {
      var parentPath = browsePath.split('/').slice(0, -1).join('/');
      html += '<div class="file-entry" onclick="browseWorktreeDir(\\'' + esc(parentPath) + '\\')">';
      html += '<span class="file-icon">..</span>';
      html += '<span class="file-name dir">..</span>';
      html += '</div>';
    }
    if (!data.entries || !data.entries.length) {
      html += '<div style="padding:16px;color:#484f58;font-size:13px;text-align:center">Empty directory</div>';
    }
    for (var i = 0; i < (data.entries || []).length; i++) {
      var entry = data.entries[i];
      if (entry.type === 'dir') {
        html += '<div class="file-entry" onclick="browseWorktreeDir(\\'' + esc(entry.path) + '\\')">';
        html += '<span class="file-icon" style="color:#58a6ff">&#128193;</span>';
        html += '<span class="file-name dir">' + esc(entry.name) + '</span>';
        html += '</div>';
      } else {
        html += '<div class="file-entry" onclick="viewWorktreeFile(\\'' + esc(entry.path) + '\\')">';
        html += '<span class="file-icon">' + getFileIcon(entry.name) + '</span>';
        html += '<span class="file-name">' + esc(entry.name) + '</span>';
        html += '<span class="file-size">' + formatFileSize(entry.size) + '</span>';
        html += '</div>';
      }
    }
    html += '</div>';
    content.innerHTML = html;
  } else if (data.type === 'file') {
    renderFileContent(data.path, data.content);
  }
}

function browseWorktreeDir(dirPath) {
  loadWorktreeFiles(dirPath);
}

function viewWorktreeFile(filePath) {
  wtBrowsePath = filePath;
  var content = document.getElementById('wt-content');
  if (!content) return;
  content.innerHTML = '<p style="color:#8b949e">Loading file...</p>';
  api('git/worktrees/' + encodeURIComponent(wtCurrentName) + '/files?path=' + encodeURIComponent(filePath)).then(function(data) {
    if (data.error) {
      content.innerHTML = '<p style="color:#f85149">' + esc(data.error) + '</p>';
      return;
    }
    renderFileContent(data.path, data.content);
  });
}

function renderFileContent(filePath, text) {
  var content = document.getElementById('wt-content');
  if (!content) return;
  var dirPath = filePath.split('/').slice(0, -1).join('/');
  var fileName = filePath.split('/').pop();
  var html = buildBreadcrumb(filePath);
  html += '<div class="file-viewer">';
  html += '<div class="file-viewer-header">';
  html += '<span>' + esc(fileName) + '</span>';
  html += '<button class="btn" style="font-size:11px;padding:2px 8px" onclick="browseWorktreeDir(\\'' + esc(dirPath) + '\\')">Back to folder</button>';
  html += '</div>';
  var lines = (text || '').split('\\n');
  for (var i = 0; i < lines.length; i++) {
    html += '<div class="file-line">';
    html += '<span class="file-line-num">' + (i + 1) + '</span>';
    html += '<span class="file-line-content">' + esc(lines[i]) + '</span>';
    html += '</div>';
  }
  html += '</div>';
  content.innerHTML = html;
}

async function loadWorktreeDiff() {
  var content = document.getElementById('wt-content');
  if (!content) return;
  if (wtDiffData) {
    renderDiffView(wtDiffData);
    return;
  }
  content.innerHTML = '<p style="color:#8b949e">Loading diff...</p>';
  var data = await api('git/worktrees/' + encodeURIComponent(wtCurrentName) + '/diff');
  if (data.error) {
    content.innerHTML = '<p style="color:#f85149">' + esc(data.error) + '</p>';
    return;
  }
  wtDiffData = data.files || [];
  renderDiffView(wtDiffData);
}

function renderDiffView(files) {
  var content = document.getElementById('wt-content');
  if (!content) return;
  if (!files || !files.length) {
    content.innerHTML = '<p style="color:#8b949e;text-align:center;padding:40px">No changes detected between this worktree and main branch.</p>';
    return;
  }
  var totalAdds = 0, totalDels = 0;
  for (var i = 0; i < files.length; i++) {
    totalAdds += files[i].additions || 0;
    totalDels += files[i].deletions || 0;
  }
  var html = '<div class="diff-summary">';
  html += '<span>' + files.length + ' files changed</span>';
  html += '<span class="total-adds">+' + totalAdds + '</span>';
  html += '<span class="total-dels">-' + totalDels + '</span>';
  html += '</div>';
  html += '<div class="diff-file-list">';
  for (var i = 0; i < files.length; i++) {
    var f = files[i];
    var expanded = wtExpandedDiffs[f.path] || false;
    html += '<div class="diff-file-header" onclick="toggleDiffFile(\\'' + esc(f.path) + '\\')">';
    html += '<span style="font-size:10px;color:#484f58">' + (expanded ? '&#9660;' : '&#9654;') + '</span>';
    html += '<span class="diff-adds">+' + (f.additions || 0) + '</span>';
    html += '<span class="diff-dels">-' + (f.deletions || 0) + '</span>';
    html += '<span class="diff-file-path">' + esc(f.path) + '</span>';
    html += '</div>';
    if (expanded) {
      html += '<div class="diff-content">';
      for (var h = 0; h < (f.hunks || []).length; h++) {
        var hunk = f.hunks[h];
        html += '<div class="diff-hunk-header">' + esc(hunk.header) + '</div>';
        for (var l = 0; l < (hunk.lines || []).length; l++) {
          var line = hunk.lines[l];
          var lineClass = line.type === 'add' ? 'add' : line.type === 'del' ? 'del' : 'context';
          var prefix = line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' ';
          html += '<div class="diff-line ' + lineClass + '">';
          html += '<span class="diff-line-num">' + (line.oldNum || '') + '</span>';
          html += '<span class="diff-line-num">' + (line.newNum || '') + '</span>';
          html += '<span class="diff-line-content">' + prefix + esc(line.content) + '</span>';
          html += '</div>';
        }
      }
      html += '</div>';
    }
  }
  html += '</div>';
  content.innerHTML = html;
}

function toggleDiffFile(filePath) {
  wtExpandedDiffs[filePath] = !wtExpandedDiffs[filePath];
  renderDiffView(wtDiffData);
}

// ===== 10b. COMMIT/BRANCH HISTORY =====
var gitNavStack=[];

function gitBack(){
  if(gitNavStack.length>0){
    var prev=gitNavStack.pop();
    if(prev.type==='branch')showBranchHistory(prev.branch,true);
    else if(prev.type==='git')showGit();
    else showGit();
  } else {
    showGit();
  }
}

async function showCommitDiff(hash,skipPush){
  if(!skipPush)gitNavStack.push(activeView==='branch'?{type:'branch',branch:gitCurrentBranch}:{type:'git'});
  activeView='commit';
  stopAutoRefresh();
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading commit '+esc(hash)+'...</p>';
  var data=await api('git/commit/'+encodeURIComponent(hash));
  if(data.error&&!data.commit){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}
  var cm=data.commit||{};
  var backLabel=gitNavStack.length>0&&gitNavStack[gitNavStack.length-1].type==='branch'?'Branch':'Git';
  var html='<div style="margin-bottom:12px"><button class="btn" onclick="gitBack()">&larr; '+backLabel+'</button></div>';
  html+='<div class="wt-header"><div style="display:flex;align-items:center;gap:12px">'+
    '<span style="font-family:monospace;color:#58a6ff;font-size:14px">'+esc(cm.hash?cm.hash.slice(0,8):'?')+'</span>'+
    '<span style="font-size:16px;color:#f0f6fc">'+esc(cm.message||'')+'</span></div>'+
    '<div style="font-size:12px;color:#8b949e;margin-top:4px">by '+esc(cm.author||'?')+' on '+localTime(cm.date)+'</div></div>';
  var files=data.files||[];
  if(files.length){
    html+=renderDiffHtml(files);
  } else {
    html+='<p style="color:#8b949e;text-align:center;padding:40px">No diff available for this commit</p>';
  }
  c.innerHTML=html;
}

var gitCurrentBranch='';

async function showBranchHistory(branch,skipPush){
  if(!skipPush)gitNavStack.push({type:'git'});
  activeView='branch';
  gitCurrentBranch=branch;
  stopAutoRefresh();
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading branch '+esc(branch)+'...</p>';
  var data=await api('git/branch-history/'+encodeURIComponent(branch));
  if(data.error&&!data.commits){c.innerHTML='<p style="color:#f85149">'+esc(data.error)+'</p>';return}
  var html='<div style="margin-bottom:12px"><button class="btn" onclick="gitBack()">&larr; Git</button></div>';
  html+='<div class="wt-header"><div style="display:flex;align-items:center;gap:12px">'+
    '<span class="branch-badge">'+esc(branch)+'</span>';
  if(data.ticketId)html+='<span style="color:#58a6ff">'+esc(data.ticketId)+'</span>';
  html+='</div></div>';
  // Commits
  var commits=data.commits||[];
  if(commits.length){
    html+='<h3 class="section-hdr">Commits ('+commits.length+')</h3>';
    html+='<div class="timeline">';
    for(var i=0;i<commits.length;i++){
      var cm=commits[i];
      html+='<div class="timeline-item" style="cursor:pointer" onclick="showCommitDiff(\\''+esc(cm.hash)+'\\')">'+
        '<span class="commit-hash">'+esc(cm.hash?cm.hash.slice(0,8):'')+'</span>'+
        '<span class="commit-msg">'+esc(cm.message)+'</span>'+
        '<span class="commit-date">'+localTime(cm.date)+'</span>'+
      '</div>';
    }
    html+='</div>';
  }
  // Merge diff
  var mergeDiff=data.mergeDiff||[];
  if(data.mergeCommit){
    html+='<h3 class="section-hdr">Merge Commit</h3>';
    html+='<div style="margin-bottom:12px;font-size:13px;color:#8b949e">'+
      '<span style="font-family:monospace;color:#58a6ff">'+esc(data.mergeCommit.hash?data.mergeCommit.hash.slice(0,8):'')+'</span> '+
      esc(data.mergeCommit.message||'')+' ('+localTime(data.mergeCommit.date)+')</div>';
  }
  if(mergeDiff.length){
    html+='<h3 class="section-hdr">Changes ('+mergeDiff.length+' files)</h3>';
    html+=renderDiffHtml(mergeDiff);
  } else if(!commits.length){
    html+='<p style="color:#8b949e;text-align:center;padding:40px">No history found for this branch</p>';
  }
  c.innerHTML=html;
}

// Shared diff HTML generator (used by commit diff, branch diff - returns HTML string)
function renderDiffHtml(files){
  var html='<div class="diff-file-list">';
  for(var i=0;i<files.length;i++){
    var f=files[i];
    var fid='cdiff-'+i;
    html+='<div class="diff-file-header" onclick="var el=document.getElementById(\\''+fid+'\\');if(el)el.style.display=el.style.display===\\'none\\'?\\'\\':\\'none\\'">'+
      '<span class="diff-adds">+'+esc(f.additions||0)+'</span>'+
      '<span class="diff-dels">-'+esc(f.deletions||0)+'</span>'+
      '<span class="diff-file-path">'+esc(f.path||f.file||'')+'</span></div>';
    html+='<div class="diff-content" id="'+fid+'" style="display:none">';
    var hunks=f.hunks||[];
    for(var h=0;h<hunks.length;h++){
      var hunk=hunks[h];
      if(hunk.header)html+='<div class="diff-hunk-header">'+esc(hunk.header)+'</div>';
      var lines=hunk.lines||[];
      for(var li=0;li<lines.length;li++){
        var ln=lines[li];
        var cls=ln.type==='add'?'add':ln.type==='del'?'del':'context';
        var prefix=ln.type==='add'?'+':ln.type==='del'?'-':' ';
        html+='<div class="diff-line '+cls+'">'+
          '<span class="diff-line-num">'+(ln.oldNum||'')+'</span>'+
          '<span class="diff-line-num">'+(ln.newNum||'')+'</span>'+
          '<span class="diff-line-content">'+prefix+' '+esc(ln.content||ln.text||'')+'</span></div>';
      }
    }
    html+='</div>';
  }
  html+='</div>';
  return html;
}

// ===== 11. BRIEF =====
async function showBrief(){
  activeView='brief';
  stopAutoRefresh();
  setActiveNav(-1);
  var c=document.getElementById('content');
  c.innerHTML='<p style="color:#8b949e">Loading brief...</p>';
  var data=await api('brief');
  var html='<div style="margin-bottom:16px"><span style="font-size:16px;color:#f0f6fc">Project Brief</span></div>';

  if(data.manifest){
    html+='<h3 class="section-hdr">Manifest</h3>';
    html+='<div class="manifest-grid">';
    var mf=data.manifest;
    var keys=['project_name','status','mode','sprint','cycle_count','version','created_at','last_updated','company_dir'];
    for(var i=0;i<keys.length;i++){
      var k=keys[i];
      if(mf[k]!=null){
        html+='<div class="manifest-field"><div class="mf-label">'+esc(k.replace(/_/g,' '))+'</div><div class="mf-value">'+esc(typeof mf[k]==='object'?JSON.stringify(mf[k]):String(mf[k]))+'</div></div>';
      }
    }
    var allKeys=Object.keys(mf);
    for(var i=0;i<allKeys.length;i++){
      if(keys.indexOf(allKeys[i])<0){
        var v=mf[allKeys[i]];
        if(v!=null&&typeof v!=='object'){
          html+='<div class="manifest-field"><div class="mf-label">'+esc(allKeys[i].replace(/_/g,' '))+'</div><div class="mf-value">'+esc(String(v))+'</div></div>';
        }
      }
    }
    html+='</div>';
  }

  if(data.brief){
    html+='<h3 class="section-hdr">Brief</h3>';
    html+='<div class="finding-card"><div class="finding-body md-content">'+renderMarkdown(data.brief)+'</div></div>';
  } else {
    html+='<p style="color:#8b949e;font-size:13px">No brief.md found</p>';
  }
  c.innerHTML=html;
}

function setActiveNav(idx){
  document.querySelectorAll('#nav > button, #nav > .dropdown-wrap > button:first-child').forEach(function(b,i){b.classList.toggle('active',i===idx)});
}

checkStatus();
</script>
</body>
</html>`;

const server = http.createServer(async (req, res) => {
  if (req.method === 'OPTIONS') { res.writeHead(200, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET,POST,PATCH', 'Access-Control-Allow-Headers': 'Content-Type' }); res.end(); return; }
  if (req.url.startsWith('/api/')) return handleApi(req, res);
  res.writeHead(200, { 'Content-Type': 'text/html' });
  res.end(HTML);
});

server.listen(port, () => {
  console.log(`\n  Company Board running at http://localhost:${port}\n`);
  if (companyDir) {
    console.log(`  Connected to: ${companyDir}\n`);
    // When --dir was passed on the command line, /api/connect never runs,
    // so file watchers were not installed. Install them now so SSE
    // events fire immediately for command-line-launched dashboards.
    try { setupWatchers(); } catch (e) {
      console.error(`  setupWatchers failed: ${e.message}`);
    }
  } else {
    console.log(`  Open the UI to connect to a project directory.\n`);
  }
});
