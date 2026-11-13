// API route handlers (G8: split out of server.js). Owns the runtime
// companyDir/port state: the CLI dir is set by server.js at startup,
// /api/connect may replace it at runtime.
'use strict';

const fs = require('fs');
const path = require('path');
const { execSync, spawn } = require('child_process');

const { mcpCall } = require('../mcp-client.js');
const { sseClients, broadcastSSE, setupWatchers } = require('../sse.js');

const STATUSES = ['backlog', 'sprint-current', 'in-progress', 'in-review', 'done'];
const SERVER_STARTED_AT = Math.floor(Date.now() / 1000);

let companyDir = null;
let port = 3456;

function setCompanyDir(d) { companyDir = d; }
function setPort(p) { port = p; }

function jsonReply(res, data, st = 200) {
  res.writeHead(st, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
  res.end(JSON.stringify(data));
}

function readJson(fp) {
  try { return JSON.parse(fs.readFileSync(fp, 'utf8')); } catch { return null; }
}

// ── T3 (0.21): GitHub mirror sync ─────────────────────────────────────
// gh is the source of truth; the sidecar file (integrations/github-
// mirror.json) is a ticket-link overlay + the last-good cache, written
// by the sync ITSELF on success. The on-demand /api/gh/mirror reuses
// this (with a 60 s request cache); the dashboard-process timer (T3-2)
// reuses it too and broadcasts gh-change on a state delta.
const GH_SYNC_TTL_MS = Math.max(1,
  parseInt(process.env.HIVE_DASHBOARD_GH_SYNC_SECS, 10) || 60) * 1000;
let ghSyncCache = null;
let ghSyncAtMs = 0;
let ghFingerprint = '';

function ghRepoInfo(projectRoot) {
  // Deep links from `git remote get-url origin` (git CLI — no gh needed
  // for the URL). No remote → url null → rows render plain numbers.
  try {
    const url = execSync('git remote get-url origin',
      { cwd: projectRoot, encoding: 'utf8', timeout: 5000,
        stdio: ['ignore', 'pipe', 'pipe'] }).trim();
    const m = url.match(/github\.com[:/]([^/]+\/[^/]+?)(?:\.git)?$/i);
    if (!m) return { name: url || null, url: null };
    const name = m[1];
    return { name, url: 'https://github.com/' + name };
  } catch { return { name: null, url: null }; }
}

function ghCmd(cmd, cwd) {
  try {
    const out = execSync(cmd,
      { cwd, encoding: 'utf8', timeout: 8000, stdio: ['ignore', 'pipe', 'pipe'] });
    return { ok: true, out, err: '' };
  } catch (e) {
    return { ok: false, out: '', err: String((e && (e.stderr || e.message)) || e).slice(0, 300) };
  }
}

// feature/<TID> convention — the branch name IS the ticket link.
function ghTicketFromBranch(branch) {
  if (!branch) return null;
  const m = String(branch).match(/^feature\/([A-Z0-9][A-Z0-9-]*)$/);
  return m ? m[1] : null;
}

function syncGithubMirror(mirrorFile, sidecar) {
  const projectRoot = (companyDir || '').replace(/\.company$/, '') || process.cwd();
  const diagnostics = { gh_on_path: false, gh_authed: false,
                        remote_configured: false, stale: false, error: null };
  const repo = ghRepoInfo(projectRoot);
  diagnostics.remote_configured = !!repo.url;

  diagnostics.gh_on_path = ghCmd('command -v gh', projectRoot).ok;
  if (!diagnostics.gh_on_path) {
    diagnostics.error = 'gh is not on PATH (install: https://cli.github.com)';
  } else {
    const auth = ghCmd('gh auth status', projectRoot);
    diagnostics.gh_authed = auth.ok;
    if (!auth.ok) diagnostics.error = 'gh is not authenticated (gh auth login)';
  }

  const canSync = diagnostics.remote_configured && diagnostics.gh_on_path && diagnostics.gh_authed;
  if (!canSync) {
    if (!diagnostics.error) diagnostics.error = 'cannot reach GitHub (check the diagnostic block)';
    // Last-good cache (the sidecar) + the named defect.
    return {
      repo, issues: (sidecar && sidecar.issues) || {}, prs: (sidecar && sidecar.prs) || {},
      _last_gh_sync_ms: (sidecar && sidecar._last_gh_sync_ms) || null,
      _sync_error: diagnostics.error,
      _diagnostics: Object.assign({}, diagnostics, { stale: true }),
    };
  }

  try {
    const prs = JSON.parse(ghFetchOrThrow('gh pr list --state all --limit 100 ' +
      '--json number,state,mergedAt,closedAt,headRefName,title', projectRoot));
    const io = JSON.parse(ghFetchOrThrow('gh issue list --state open --limit 100 --json number,title,state', projectRoot));
    const ic = JSON.parse(ghFetchOrThrow('gh issue list --state closed --limit 100 --json number,title,state', projectRoot));

    const prMap = {};
    for (const pr of (prs || [])) {
      const k = String(pr.number);
      const ex = ((sidecar || {}).prs || {})[k] || {};
      const branch = ex.branch || pr.headRefName || null;
      prMap[k] = {
        number: pr.number, title: pr.title || ex.title || null,
        state: (pr.state || '').toLowerCase(), branch,
        ticket: ex.ticket_id || ex.ticket || ghTicketFromBranch(branch),
        merged_at: pr.mergedAt || ex.merged_at || null,
        closed_at: pr.closedAt || ex.closed_at || null,
        opened_at: ex.opened_at || null,
        url: repo.url ? repo.url + '/pull/' + pr.number : null,
      };
    }
    const issueMap = {};
    for (const it of (io || []).concat(ic || [])) {
      const k = String(it.number);
      const ex = ((sidecar || {}).issues || {})[k] || {};
      issueMap[k] = {
        number: it.number, title: it.title || ex.title || null,
        state: (it.state || '').toLowerCase(),
        ticket: ex.ticket_id || ex.ticket || null,
        url: repo.url ? repo.url + '/issues/' + it.number : null,
        last_synced_at: new Date().toISOString(),
      };
    }
    try {
      fs.mkdirSync(path.dirname(mirrorFile), { recursive: true });
      fs.writeFileSync(mirrorFile,
        JSON.stringify({ issues: issueMap, prs: prMap, _last_gh_sync_ms: Date.now() }, null, 2));
    } catch {}
    return {
      repo, issues: issueMap, prs: prMap, _last_gh_sync_ms: Date.now(),
      _sync_error: null, _diagnostics: diagnostics,
    };
  } catch (e) {
    diagnostics.stale = true;
    diagnostics.error = 'gh call failed: ' + String((e && e.message) || e).slice(0, 300);
    return {
      repo, issues: (sidecar && sidecar.issues) || {}, prs: (sidecar && sidecar.prs) || {},
      _last_gh_sync_ms: (sidecar && sidecar._last_gh_sync_ms) || null,
      _sync_error: diagnostics.error, _diagnostics: diagnostics,
    };
  }
}

function ghFetchOrThrow(cmd, cwd) {
  const r = ghCmd(cmd, cwd);
  if (!r.ok) throw new Error(r.err || cmd);
  return r.out;
}

function ghFingerprintOf(result) {
  const fp = (m, f) => Object.keys(m || {}).sort().map(k => k + ':' + f(m[k])).join(',');
  return fp(result.prs, p => p.state || '') + '|' + fp(result.issues, i => i.state || '');
}

// B3 (0.9.0): read LLM usage entries so the rounds panel can price each
// round. llm-usage.log; the legacy llm-tiny.log is still honored for one
// release (same convention as `hive llm stats`). Returns
// [{ts: Date, tokens: number, cost: number}].
function readLlmUsage(companyDir) {
  let fp = null;
  for (const name of ['logs/llm-usage.log', 'logs/llm-tiny.log']) {
    const cand = path.join(companyDir, name);
    if (fs.existsSync(cand)) { fp = cand; break; }
  }
  if (!fp) return [];
  const out = [];
  try {
    const lines = fs.readFileSync(fp, 'utf8').split('\n');
    for (const line of lines) {
      if (!line.trim()) continue;
      let u;
      try { u = JSON.parse(line); } catch { continue; }
      const ts = new Date(u.ts || '');
      if (isNaN(ts.getTime())) continue;
      let tokens = parseInt(u.tokens, 10);
      if (isNaN(tokens)) tokens = 0;
      let cost = parseFloat(u.cost_usd);
      if (isNaN(cost)) cost = 0;
      out.push({ ts, tokens, cost });
    }
  } catch {}
  return out;
}

// B3: sum usage entries falling in [sa, ea] — no new state written; the
// round JSON and the usage log are both read-only here. An open window
// (no ended_at) sums through now.
function usageWindow(usage, saStr, eaStr) {
  const sa = saStr ? new Date(saStr) : new Date(0);
  if (isNaN(sa.getTime())) return { calls: 0, tokens: 0, cost_usd: 0 };
  const ea = eaStr ? new Date(eaStr) : null;
  let calls = 0, tokens = 0, cost = 0;
  for (const u of usage) {
    if (u.ts >= sa && (ea === null || isNaN(ea.getTime()) || u.ts <= ea)) {
      calls += 1; tokens += u.tokens; cost += u.cost;
    }
  }
  return { calls, tokens, cost_usd: cost };
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

// T4 (0.17): the VERSION file at the tree root — the same file hive
// --version and the release build read. The footer shows it.
const HIVE_VERSION = (() => {
  try {
    return fs.readFileSync(
      path.resolve(__dirname, '..', '..', '..', 'VERSION'), 'utf8').trim();
  } catch (e) {
    return 'unknown';
  }
})();

function getDepartments() {
  const dir = companyPath();
  if (!dir) return [];
  const deptDir = path.join(dir, 'departments');
  // T3 (0.17): the department set is the org-chart union the runtime
  // dirs — a fresh company's org-chart depts are listed (with zero
  // ticket counts) before any runtime state is derived, so the board
  // shows the company instead of 'No departments found'.
  const orgChart = readJson(path.join(dir, 'org-chart.json'));
  const validDepts = orgChart ? new Set(Object.keys(orgChart.departments || {})) : null;
  const depts = new Set(validDepts || []);
  if (fs.existsSync(deptDir)) {
    for (const d of fs.readdirSync(deptDir)) {
      if (!fs.statSync(path.join(deptDir, d)).isDirectory()) continue;
      // If we have an org chart, only show valid departments
      if (validDepts && !validDepts.has(d)) continue;
      depts.add(d);
    }
  }
  return [...depts].sort();
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
    setupWatchers(companyDir);
    return jsonReply(res, { connected: true, path: companyDir, departments: getDepartments(),
      started_at: SERVER_STARTED_AT });
  }

  // GET /api/events - SSE endpoint.
  // T2 (0.21): headers out at connect. Node sends response headers with
  //     the first body chunk (or an explicit flushHeaders(), which 0.20
  //     never called), so 0.20 delivered 0 bytes until the first file
  //     change somewhere in the company: EventSource sat in "connecting"
  //     (no onopen, the live indicator stuck on "connecting…") for minutes
  //     on an idle company, and past the browser's ~300 s timeout it
  //     aborted into the flat 5 s reconnect loop. flushHeaders() plus an
  //     immediate `: connected <iso>` comment line makes onopen fire at
  //     connect; the line also doubles as the first keepalive (the
  //     heartbeat — T2 — keeps the pipe warm afterwards).
  if (req.method === 'GET' && parts[0] === 'events') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'Access-Control-Allow-Origin': '*'
    });
    res.flushHeaders();
    res.write(': connected ' + new Date().toISOString() + '\n\n');
    sseClients.add(res);
    req.on('close', () => sseClients.delete(res));
    return;
  }

  // GET /api/status
  if (req.method === 'GET' && parts[0] === 'status') {
    if (!companyPath()) return jsonReply(res, { connected: false });
    return jsonReply(res, { connected: true, path: companyPath(), info: getCompanyInfo(),
      started_at: SERVER_STARTED_AT, version: HIVE_VERSION });
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
      // C.10: forward the create form's labels / acceptance criteria —
      // they used to be collected by the form and silently dropped here,
      // so tickets were created without the AC/labels the user typed.
      labels: Array.isArray(data.labels) ? data.labels.filter(l => typeof l === 'string' && l.trim()) : undefined,
      acceptance_criteria: Array.isArray(data.acceptance_criteria) ? data.acceptance_criteria.filter(a => typeof a === 'string' && a.trim()) : undefined,
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
    // HIVE_ROOT is the global install — projects use installed scripts
    // directly (there is no per-project .hive/ copy).
    const hiveRoot = installDir;
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
    const hiveRoot = installDir;
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
    // Body may carry {ticket_id} to gate ONE specific ticket; without it
    // the dept's first in-review ticket is gated (legacy daemon behavior).
    let body={};
    try { body=await readBody(req); } catch(e){}
    const result = await mcpCall('gates_run', {
      dept: parts[1],
      // project_root omitted: the tool derives it from the company dir
      // (C.10 - explicit paths were a client burden).
      ticket_id: (body && body.ticket_id) || undefined,
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

  // 2d. GET /api/spawns/active — list of currently in-flight agent
  //     spawns. Tagged records only: .company/logs/active/<spawn_id>.json,
  //     written by agent-spawn.sh. Has full identity (agent/dept/role/ticket)
  //     and a stream-file pointer.
  // T1 (0.21): one id field, one stream pointer. 0.21+ records carry
  //     spawn_id + stream_file; orphaned 0.20 records carry only id —
  //     resolve the stream from the convention agent-spawn.sh already
  //     enforces (logs/streams/<spawn_id>.jsonl) and expose spawn_id
  //     (the resolved value) so the frontend can address a real spawn.
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
          // T1 (0.21): the spawn contract, with the 0.20 orphan fallback —
          // spawn_id || id || the record file's stem (agent-spawn.sh makes
          // all three equal), and the stream off stream_file or the
          // logs/streams/<spawn_id>.jsonl convention.
          rec.spawn_id = rec.spawn_id || rec.id || path.basename(f, '.json');
          const streamFile = rec.stream_file
            || path.join(companyPath(), 'logs', 'streams', rec.spawn_id + '.jsonl');
          try {
            const stat = fs.statSync(streamFile);
            rec.stream_bytes = stat.size;
            const data = fs.readFileSync(streamFile, 'utf8');
            const lines = data.length ? data.split('\n').filter(Boolean) : [];
            rec.stream_lines = lines.length;
          } catch { rec.stream_bytes = 0; rec.stream_lines = 0; }
          if (rec.pid) tagged_pids.add(rec.pid);
          // Track pids so we don't double-count
          out.push(rec);
        } catch {}
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
  // time. Each event is an object emitted by the agent harness (stream-json).
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
          // T1 (0.21): the same fallback chain as /api/spawns/active —
          // 0.20 orphan records (id only) were invisible here because the
          // meta map was keyed on rec.spawn_id, which they lack.
          const sid = rec.spawn_id || rec.id || path.basename(f, '.json');
          if (sid) {
            meta[sid] = {
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
  // empty shell (a fresh activity log with no entries yet —
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

  // 2j. GET /api/rounds/history?limit=N — summarized historical rounds.
  // B3: each round is priced from the LLM usage log (entries whose
  // timestamps fall inside the round's started_at/ended_at window), and
  // the response carries a `totals` block — the cumulative 'this sprint'
  // cost across all returned history entries.
  if (req.method === 'GET' && parts[0] === 'rounds' && parts[1] === 'history' && parts.length === 2) {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '20', 10) || 20, 200);
    const histDir = path.join(companyPath(), 'rounds', 'history');
    const usage = readLlmUsage(companyPath());
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
          const w = usageWindow(usage, d.started_at, d.ended_at);
          out.push({
            round_id: d.round_id,
            source: d.source,
            started_at: d.started_at,
            ended_at: d.ended_at,
            duration_s: dur,
            entry_count: entries.length,
            status_counts: counts,
            cost_usd: w.cost_usd,
            tokens: w.tokens,
            calls: w.calls,
          });
        } catch {}
      }
    } catch {}
    const totals = { calls: 0, tokens: 0, cost_usd: 0 };
    for (const r of out) { totals.calls += r.calls; totals.tokens += r.tokens; totals.cost_usd += r.cost_usd; }
    return jsonReply(res, { rounds: out, totals });
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

  // 2m. GET /api/gh/mirror — GitHub's ACTUAL state (T3, 0.21).
  // Contract: gh is the source of truth. The sidecar file
  // (.company/integrations/github-mirror.json) is (a) a ticket-link
  // overlay and (b) the last-known cache, written by the sync ITSELF on
  // success — nothing else in the tree writes it (the old "written by
  // gh-pr-open.sh / gh-pr-merge.sh" contract is gone: the mirror
  // sidecar was removed with the issues integration).
  //   - PRs: gh pr list --state all — every PR on the remote is a row.
  //   - Issues: gh issue list --state open + --state closed (100 each) —
  //     the repo's real issues; no phantom rows without a sidecar.
  //   - "Hive ticket": PR headRefName feature/<TID> convention, else the
  //     sidecar's explicit link; issues map via the sidecar only.
  //   - Deep links from `git remote get-url origin` (git CLI, no gh
  //     needed for the URL); no remote → plain numbers, no dead anchors.
  //   - _diagnostics names which check failed (gh on PATH / gh auth /
  //     remote / network) so the page can say what is wrong; when gh is
  //     unreachable the last good sync is served with stale: true.
  if (req.method === 'GET' && parts[0] === 'gh' && parts[1] === 'mirror' && parts.length === 2) {
    if (!companyPath()) return jsonReply(res, { repo: null, issues: {}, prs: {},
      _last_gh_sync_ms: null, _sync_error: 'no company connected',
      _diagnostics: { gh_on_path: false, gh_authed: false, remote_configured: false, stale: false, error: 'no company connected' } });
    const mirrorFile = path.join(companyPath(), 'integrations', 'github-mirror.json');
    // On-demand 60 s request cache (T3): navigation-time fetches do
    // not re-run gh; the timer keeps the cache warm and broadcasts
    // gh-change on deltas. A failed sync is cached too (re-probing
    // gh auth on every page visit would stall the page).
    if (ghSyncCache && Date.now() - ghSyncAtMs < GH_SYNC_TTL_MS) return jsonReply(res, ghSyncCache);
    let sidecar = { issues: {}, prs: {} };
    try { sidecar = JSON.parse(fs.readFileSync(mirrorFile, 'utf8')); } catch {}
    const sync = syncGithubMirror(mirrorFile, sidecar);
    ghSyncCache = sync; ghSyncAtMs = Date.now();
    return jsonReply(res, sync);
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

  // C2. GET /api/config — the Config & LLM panel: the last saved doctor
  // dump (.company/state/doctor.json, written by `hive doctor` on every
  // run) plus the last `hive llm test` result (.company/state/llm-test
  // .json). Read-only; secret-looking fields are stripped defensively —
  // the panel must be able to render the config without a key ever
  // crossing the wire a second time.
  if (req.method === 'GET' && parts[0] === 'config' && parts.length === 1) {
    const SECRET_KEYS = new Set(['api_key', 'apikey', 'authorization',
                                 'token', 'password', 'secret']);
    const sanitize = (v) => {
      if (Array.isArray(v)) return v.map(sanitize);
      if (v && typeof v === 'object') {
        const o = {};
        for (const [k, val] of Object.entries(v)) {
          if (SECRET_KEYS.has(k.toLowerCase())) continue;
          o[k] = sanitize(val);
        }
        return o;
      }
      return v;
    };
    const readState = (name) => {
      try { return sanitize(JSON.parse(fs.readFileSync(
        path.join(companyPath(), 'state', name), 'utf8'))); }
      catch { return null; }
    };
    return jsonReply(res, { doctor: readState('doctor.json'),
                            llm_test: readState('llm-test.json') });
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

  const SKIP_DIRS = new Set(['.git', 'node_modules', '.company', '.worktrees']);

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
module.exports = { handleApi, setCompanyDir, setPort };
// T3-2 (0.21): the dashboard-process sync timer (NO new daemon). Every
// HIVE_DASHBOARD_GH_SYNC_SECS (default 60) it re-syncs when a company is
// connected and broadcasts gh-change ONLY on a state delta (new PR,
// state flip, merge) — a PR merged in a shell flips on the page within
// one interval, no navigation.
let ghTimer = null;
function startGhSyncTimer() {
  if (ghTimer) return;
  ghTimer = setInterval(() => {
    if (!companyDir) return;
    try {
      const mirrorFile = path.join(companyDir, 'integrations', 'github-mirror.json');
      if (!(ghSyncCache && Date.now() - ghSyncAtMs < GH_SYNC_TTL_MS)) {
        let sidecar = { issues: {}, prs: {} };
        try { sidecar = JSON.parse(fs.readFileSync(mirrorFile, 'utf8')); } catch {}
        ghSyncCache = syncGithubMirror(mirrorFile, sidecar);
        ghSyncAtMs = Date.now();
      }
      const fp = ghFingerprintOf(ghSyncCache);
      if (ghFingerprint && fp !== ghFingerprint) {
        broadcastSSE('gh-change', { changed: true, time: new Date().toISOString() });
      }
      ghFingerprint = fp;
    } catch {}
  }, GH_SYNC_TTL_MS);
  if (ghTimer.unref) ghTimer.unref();
}

module.exports = { handleApi, setCompanyDir, setPort, startGhSyncTimer };
