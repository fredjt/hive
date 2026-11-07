#!/usr/bin/env node
// the CLI, and agents all share the same gating + audit logic.
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
module.exports = { mcpCall, _mcpInitialize, _parseMcpResponse };
