#!/usr/bin/env node
// Company Board — HTTP entry point (G8: split out of the old 5,677-line
// monolith). The frontend is real files under static/ (no template
// literal, no escaping); API routes live in api/handlers.js, the MCP
// client in mcp-client.js, the SSE file-watcher in sse.js. Zero
// dependencies, no build step — plain node http.
const http = require('http');
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
let companyDir = args.find((a, i) => args[i - 1] === '--dir') || null;
const port = parseInt(args.find((a, i) => args[i - 1] === '--port') || '3456');

const api = require('./api/handlers.js');
const { setupWatchers, startHeartbeat } = require('./sse.js');
api.setCompanyDir(companyDir);
// T3-2 (0.21): the GitHub sync timer (dashboard-process, no new daemon).
api.startGhSyncTimer();
api.setPort(port);

const STATIC_DIR = path.join(__dirname, 'static');
const STATIC_FILES = {
  '/app.js': { type: 'application/javascript', file: 'app.js' },
  '/styles.css': { type: 'text/css', file: 'styles.css' },
};

function serveStatic(res, urlPath) {
  if (urlPath === '/') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(fs.readFileSync(path.join(STATIC_DIR, 'app.html')));
    return;
  }
  const f = STATIC_FILES[urlPath];
  if (f) {
    res.writeHead(200, { 'Content-Type': f.type + '; charset=utf-8' });
    res.end(fs.readFileSync(path.join(STATIC_DIR, f.file)));
    return;
  }
  res.writeHead(404, { 'Content-Type': 'text/plain' });
  res.end('Not found');
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'OPTIONS') { res.writeHead(200, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET,POST,PATCH', 'Access-Control-Allow-Headers': 'Content-Type' }); res.end(); return; }
  if (req.url.startsWith('/api/')) {
    // A rejected handleApi promise (e.g. readBody() on a malformed JSON
    // body) used to escape as an unhandled rejection — Node >= 15 kills
    // the process on unhandled rejections, so one bad request crashed the
    // whole dashboard. Answer with a 4xx instead.
    api.handleApi(req, res).catch((e) => {
      try {
        if (!res.headersSent) res.writeHead(400, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
        res.end(JSON.stringify({ error: 'bad request: ' + (e && e.message ? e.message : String(e)) }));
      } catch (_) {}
    });
    return;
  }
  serveStatic(res, (req.url || '/').split('?')[0]);
});

// T2 (0.21): the keepalive heartbeat runs regardless of connection state —
// a client may hold /api/events open before (or without) any company being
// connected, and an idle pipe still has to stay warm.
startHeartbeat();

server.listen(port, () => {
  console.log(`\n  Company Board running at http://localhost:${port}\n`);
  if (companyDir) {
    console.log(`  Connected to: ${companyDir}\n`);
    // When --dir was passed on the command line, /api/connect never runs,
    // so file watchers were not installed. Install them now so SSE
    // events fire immediately for command-line-launched dashboards.
    try { setupWatchers(companyDir); } catch (e) {
      console.error(`  setupWatchers failed: ${e.message}`);
    }
  } else {
    console.log(`  Open the UI to connect to a project directory.\n`);
  }
});
