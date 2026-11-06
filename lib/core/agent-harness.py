#!/usr/bin/env python3
"""Hive agent harness — built-in AI agent harness.

This is the SOLE place that runs an AI agent inside hive. Every spawned agent
(going through agent-spawn.sh) uses this harness instead of the external CLI.

The harness supports:
  - Shell command execution (with safety limits)
  - MCP tool calls over HTTP (for ticket/chat/status operations)
  - Structured output compatible with existing log consumers

Responsibilities:
  1. Read a prompt from stdin or command-line argument.
  2. Run a conversation loop where the agent can:
     - Execute shell commands (via subprocess, with safety limits)
     - Call MCP tools over HTTP (for ticket/chat/status operations)
     - Write to files outside .company/ (project source code, docs, etc.)
  3. Output structured results compatible with existing log consumers.
  4. Respect safety constraints (no writes to .company/, permission limits).

Usage:
  echo "<prompt>" | agent-harness.py [--mcp-url URL] [--max-turns N]
  agent-harness.py -- "<prompt>"
  agent-harness.py --no-mcp -- "<prompt>"

Environment variables:
  HIVE_AGENT              agent name (e.g. "ceo", "vp-it", "ic-engineer")
  HIVE_DEPT               department
  HIVE_ROLE               role label (CEO, VP, IC, etc.)
  HIVE_TICKET             ticket id (optional)
  HIVE_ROOT               hive install root path
  COMPANY_DIR             .company directory path
  PROJECT_ROOT            project root (where .company/ lives)
  MCP_URL                 MCP server URL (default: http://127.0.0.1:7891/mcp)
  MAX_TURNS               max conversation turns (default: 50)
  HIVE_ALLOW_SHELL        if set to "false", disable shell commands
  HIVE_PROVIDER           explicit provider name (openai, anthropic, groq,
                          openrouter, ollama, google, unsloth)
  HIVE_MODEL              model name (optional; [llm].model if unset)
  HIVE_API_BASE           base URL (optional; provider default if unset)
  HIVE_API_KEY            API key (optional; [llm].api_key or per-provider
                          env, e.g. OPENAI_API_KEY, if unset)
  # LLM config can also live in the [llm] section of config.toml
  # (etc/config.defaults.toml + ~/.config/hive/config.toml); env wins.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# HTTP client for MCP tool calls
# ---------------------------------------------------------------------------

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


def call_mcp_tool(mcp_url: str, tool_name: str, arguments: dict) -> dict[str, Any]:
    """Call an MCP tool over Streamable HTTP.

    C.2: the protocol itself (initialize handshake, mcp-session-id
    header, notifications/initialized, SSE body parsing) lives in the
    shared SDK-free lib/core/mcp_http.py — httpx only, no mcp SDK.

    Returns a dict with ok/response/error keys compatible with existing
    log consumers that expect the harness result format.
    """
    if _httpx is None:
        return {
            "ok": False,
            "error": "httpx not installed — MCP tool calls disabled",
            "tool": tool_name,
        }
    if mcp_http is None:  # pragma: no cover - module sits next to this file
        return {
            "ok": False,
            "error": "mcp_http module unavailable",
            "tool": tool_name,
        }
    return mcp_http.call_tool(mcp_url, tool_name, arguments)




# ---------------------------------------------------------------------------
# Shell command execution with safety limits
# ---------------------------------------------------------------------------

# Commands that are explicitly blocked for safety
BLOCKED_COMMANDS = {
    "rm", "unlink", "rmdir", "trash-put",
    "mv", "ln", "chmod", "chown", "chgrp", "setfacl",
    "tar", "unzip", "dd", "mkfifo", "mknod",
    "truncate", "sed", "perl", "awk",
}

# Patterns that indicate dangerous operations
BLOCKED_PATTERNS = [
    r"\brm\b.*-rf\b",          # rm -rf (even without -f is risky)
    r"\bchmod\s+777\b",        # world-writable
    r"\bchown\s+root\b",       # taking ownership
    r"\bsudo\b",               # privilege escalation
    r"\bmkfs\b",               # format disks
    r"\bshutdown\b|\breboot\b",# system shutdown
]


def is_safe_command(cmd_line: str) -> tuple[bool, str]:
    """Check if a shell command is safe to execute.

    Returns (is_safe, reason_if_unsafe).
    """
    # Extract the base command
    parts = cmd_line.strip().split()
    if not parts:
        return False, "empty command"

    base_cmd = Path(parts[0]).name

    if base_cmd in BLOCKED_COMMANDS:
        return False, f"command '{base_cmd}' is blocked for safety"

    # Check for dangerous patterns
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd_line):
            return False, f"command matches dangerous pattern: {pattern}"

    return True, ""


def run_shell_command(cmd_line: str, cwd: str | None = None,
                      timeout: int = 120) -> dict[str, Any]:
    """Execute a shell command safely.

    Returns a dict with ok/exit_code/stdout/stderr compatible with existing
    log consumers.
    """
    # Check safety
    is_safe, reason = is_safe_command(cmd_line)
    if not is_safe:
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Command blocked: {reason}",
            "command": cmd_line,
        }

    # Run the command
    try:
        result = subprocess.run(
            cmd_line,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": cmd_line,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "exit_code": 124,
            "stdout": e.stdout or "",
            "stderr": f"timeout after {timeout}s: {e.stderr or ''}",
            "command": cmd_line,
        }
    except Exception as e:
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": f"execution error: {type(e).__name__}: {e}",
            "command": cmd_line,
        }


# ---------------------------------------------------------------------------
# LLM API integration — OpenAI-compatible endpoints
# ---------------------------------------------------------------------------

try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore


# Wire dialects and the provider registry live in llm_providers.py
# (B.2): openai-style /chat/completions, anthropic-style /v1/messages,
# gemini-style :generateContent. Import it by name when possible (the
# harness runs as a script with its own dir on sys.path) and fall back
# to a path-based load so the module also works when this file is
# imported directly (e.g. by the test suite).
try:
    import llm_providers  # type: ignore
except ImportError:  # pragma: no cover - import path depends on caller
    import importlib.util as _ilu
    import os as _os
    try:
        _spec = _ilu.spec_from_file_location(
            "llm_providers",
            _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                          "llm_providers.py"))
        llm_providers = _ilu.module_from_spec(_spec)  # type: ignore
        # Register BEFORE exec: llm_providers uses @dataclass, and
        # dataclasses resolves cls.__module__ through sys.modules. Without
        # this entry the exec raises AttributeError, the handler below
        # swallows it, and the harness silently degrades to the openai-only
        # wire fallback.
        sys.modules["llm_providers"] = llm_providers
        _spec.loader.exec_module(llm_providers)
    except Exception:  # pragma: no cover
        sys.modules.pop("llm_providers", None)
        llm_providers = None  # type: ignore


try:
    import mcp_http  # type: ignore
except ImportError:  # pragma: no cover - import path depends on caller
    import importlib.util as _ilm
    import os as _osm
    try:
        _specm = _ilm.spec_from_file_location(
            "mcp_http",
            _osm.path.join(_osm.path.dirname(_osm.path.abspath(__file__)),
                           "mcp_http.py"))
        mcp_http = _ilm.module_from_spec(_specm)  # type: ignore
        # Same sys.modules-before-exec requirement as llm_providers above.
        sys.modules["mcp_http"] = mcp_http
        _specm.loader.exec_module(mcp_http)
    except Exception:  # pragma: no cover
        sys.modules.pop("mcp_http", None)
        mcp_http = None  # type: ignore


def detect_provider(api_base: str) -> str:
    """Detect the provider from the API base URL.

    Fallback only (B.2): explicit provider selection (HIVE_PROVIDER /
    [llm].provider) is the primary path; this exists so direct callers
    and legacy env-only setups keep working.

    Returns a provider key that can be used to look up configuration.
    """
    if not api_base:
        return "custom"

    lower = api_base.lower()
    if llm_providers is not None:
        # The registry (B.2) is the canonical source: match the provider
        # name or its default base URL against the given base. Entries
        # without a default base (unsloth) still match by name.
        for name, config in llm_providers.PROVIDERS.items():
            if name in lower:
                return name
            default_base = (config.get("base_url") or "").lower()
            if default_base and default_base in lower:
                return name

    # Check for common patterns (also the only path when the
    # llm_providers sibling module is unavailable)
    if "openai" in lower:
        return "openai"
    if "anthropic" in lower:
        return "anthropic"
    if "ollama" in lower or "11434" in lower:
        return "ollama"
    if "openrouter" in lower:
        return "openrouter"
    if "groq" in lower:
        return "groq"

    return "custom"


def wire_style(api_style: str | None, api_base: str | None) -> str:
    """Resolve the wire dialect: explicit api_style wins; otherwise
    detect it from the base URL (B.2 fallback path)."""
    if api_style:
        return api_style
    provider = detect_provider(api_base or "")
    if llm_providers is not None:
        return llm_providers.style_for_provider(provider)
    return "anthropic" if provider == "anthropic" else "openai"


def build_request_payload(
    messages: list[dict],
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    api_style: str | None = None,
    api_base: str | None = None,
) -> dict:
    """Build the request body in the wire dialect's native shape.

    Dialect resolution (B.2): explicit api_style > detection from the
    base URL. Shape building itself lives in llm_providers.build_payload.
    """
    style = wire_style(api_style, api_base if api_base is not None else os.environ.get("HIVE_API_BASE", ""))
    if llm_providers is not None:
        return llm_providers.build_payload(style, messages, model, temperature, max_tokens)
    return {"model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}


def build_headers(api_base: str, api_key: str | None = None,
                  api_style: str | None = None,
                  provider: str = "") -> dict[str, str]:
    """Build headers for the API request (auth header per wire dialect)."""
    style = wire_style(api_style, api_base)
    if llm_providers is not None:
        return llm_providers.build_headers(style, api_key, provider=provider)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def parse_response(resp: httpx.Response, api_style: str | None = None,
                   api_base: str | None = None) -> dict[str, Any]:
    """Parse the API response into a standardized format.

    Handles every wire dialect (openai / anthropic / gemini).
    Returns a dict with ok/content/model/usage/error keys.
    """
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": f"Invalid JSON response: {resp.text[:200]}",
        }

    style = wire_style(api_style, api_base if api_base is not None else os.environ.get("HIVE_API_BASE", ""))
    if llm_providers is not None:
        return llm_providers.parse_response_body(style, data)
    # Fallback: openai shape only.
    choices = data.get("choices", [])
    if not choices:
        return {"ok": False, "error": "No choices in response"}
    return {"ok": True, "content": choices[0].get("message", {}).get("content", "") or "",
            "model": data.get("model", "unknown"),
            "usage": data.get("usage", {})}


def _log_llm_usage(company_dir: str | None, caller: str, mode: str,
                   model: str, turn: int, usage: dict, response: str) -> None:
    """B2: append one entry to .company/logs/llm-usage.log (the rename of
    the retired llm-tiny.log; readers accept both names for one release).
    One entry per LLM call so `hive llm stats`/`tail` can show who burned
    which endpoint and what came back. Fault-tolerant: a logging hiccup
    must never break the agent loop."""
    if not company_dir:
        return
    try:
        log_dir = os.path.join(company_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        first_line = (response or "").strip().splitlines()
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "caller": caller or "harness",
            "mode": mode,
            "model": model or "",
            "turn": turn,
            "tokens": (usage or {}).get("total_tokens", 0),
            # B1 (0.9.0): per-call cost. The provider's usage block carries
            # total_cost_usd when the model is priced (llm_providers
            # .parse_response_body computes it); providers that report no
            # cost leave it 0.0. Readers (hive llm stats) treat a missing
            # cost_usd on OLD entries as 0 — never a parse error.
            "cost_usd": float((usage or {}).get("total_cost_usd", 0.0) or 0.0),
            "response": (first_line[0][:200] if first_line else ""),
        }
        with open(os.path.join(log_dir, "llm-usage.log"), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def call_llm_api(
    messages: list[dict],
    api_base: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    retries: int = 3,
    api_style: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Call an LLM API with retry logic, in the provider's native wire shape.

    Supports every registered provider (B.2): openai, anthropic, groq,
    openrouter, ollama, google. The wire dialect comes from `api_style`
    (explicit provider selection, the primary path since B.2) or falls
    back to detection from the base URL.

    Args:
        messages: Conversation history as list of {role, content} dicts.
        api_base: Base URL for the LLM API (e.g., https://api.openai.com/v1).
        model: Model name to use (e.g., gpt-4o, gemini-2.0-flash).
        api_key: API key for authentication.
        temperature: Sampling temperature (0.0-2.0). Default 0.7.
        max_tokens: Maximum tokens in response. Default 8192.
        retries: Number of retry attempts for transient failures. Default 3.
        api_style: Wire dialect ("openai" | "anthropic" | "gemini");
            resolved from the explicit provider when omitted.
        provider: Provider name (for attribution headers + error reports).

    Returns:
        Dict with ok/content/model/usage/error keys. On failure the error
        names the provider and base URL attempted, the HTTP status, and a
        truncated response body.
    """
    base = api_base or os.environ.get("HIVE_API_BASE")
    mdl = model or os.environ.get("HIVE_MODEL", "gpt-4o")
    key = api_key or os.environ.get("HIVE_API_KEY")
    temp = temperature if temperature is not None else float(os.environ.get("HIVE_TEMPERATURE", "0.7"))
    max_tok = max_tokens if max_tokens is not None else int(os.environ.get("HIVE_MAX_TOKENS", "8192"))

    if not base:
        return {
            "ok": False,
            "error": ("No LLM API configured — set HIVE_PROVIDER + HIVE_MODEL + "
                      "HIVE_API_BASE (+ HIVE_API_KEY) or the [llm] section of "
                      "config.toml"),
            "model": mdl,
        }

    if _httpx is None:
        return {
            "ok": False,
            "error": "httpx not installed — LLM calls disabled",
            "model": mdl,
        }

    style = wire_style(api_style, base)
    headers = build_headers(base, key, api_style=style, provider=provider or "")
    payload = build_request_payload(messages, mdl, temp, max_tok, api_style=style)

    if llm_providers is not None:
        url = llm_providers.endpoint_url(base, style, mdl)
    else:  # pragma: no cover - import path depends on caller
        url = f"{base.rstrip('/')}/chat/completions"
    if not url:
        return {
            "ok": False,
            "error": (f"Cannot derive request URL (provider={provider or style}, "
                      f"base_url={base}) — this wire style needs a model name"),
            "model": mdl,
        }

    def _err(status: int, body: str, extra: str = "") -> dict:
        return {
            "ok": False,
            "error": (f"LLM API failed (provider={provider or style}, "
                      f"base_url={base}{extra}, HTTP {status}): {body[:300]}"),
            "model": mdl,
        }

    last_error = None
    for attempt in range(retries):
        try:
            with _httpx.Client(timeout=120.0) as client:
                resp = client.post(url, json=payload, headers=headers)

                if resp.status_code == 429:  # Rate limit
                    retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                    import time as _time
                    _time.sleep(retry_after)
                    last_error = _err(429, resp.text, " (rate limited)")
                    continue

                if resp.status_code not in (200, 201):
                    last_error = _err(resp.status_code, resp.text)
                    if resp.status_code >= 500:
                        # Server error — retry
                        continue
                    break  # Client error — don't retry

                return parse_response(resp, api_style=style, api_base=base)

        except _httpx.TimeoutException:
            last_error = _err(0, "request timed out")
            continue
        except _httpx.ConnectError as e:
            last_error = _err(0, f"connection error: {e}")
            if attempt < retries - 1:
                import time as _time
                _time.sleep(2 ** attempt)  # Exponential backoff
            continue
        except Exception as e:
            last_error = _err(0, f"{type(e).__name__}: {e}")
            break  # Non-retryable error

    return {
        "ok": False,
        "error": f"LLM API failed after {retries} attempts: "
                 f"{last_error.get('error') if isinstance(last_error, dict) else last_error}",
        "model": mdl,
    }


# Agent conversation loop
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = textwrap.dedent("""\
You are an agent in the Hive orchestration framework. You work on a project as 
part of a virtual company. Your role, department, and assignment are provided 
in your prompt.

## Capabilities

### Shell Commands
You can execute shell commands by wrapping them in bash blocks. Two forms
are accepted — use the first:

```bash: cat /path/to/file.txt```

or a bare fence with bash: on the next line:

```
bash: cat /path/to/file.txt
```

Safety rules:
- You CAN read/write files outside .company/
- You MUST NOT write to .company/ (use MCP tools instead)
- Some dangerous commands are blocked (rm -rf, chmod 777, sudo, etc.)

### MCP Tools
You can call hive MCP tools by wrapping them in mcp blocks. Two forms are
accepted — use the first:

```mcp: ticket_create(dept="backend", type="feature", priority="high", title="Add API endpoint", description="...")```

or a bare fence with mcp: on the next line:

```
mcp: ticket_create(dept="backend", type="feature", priority="high", title="Add API endpoint", description="...")
```

Available tools depend on what's configured. Check the MCP tool list if unsure.

### File Operations
For file reads, use: ```bash: cat /path/to/file```
For file writes outside .company/, use:

```bash: cat > /path/to/file << 'HEREDOC'
content
HEREDOC```

## Rules
- Be concise and focused on your assignment.
- Execute shell commands and MCP tools as needed to complete your task.
- Exit cleanly when your work is done.
- Do NOT spawn other agents — that's the system's job.
- Do NOT touch PRs directly — use ticket operations instead.
""")


def run_agent(
    prompt: str,
    mcp_url: str | None = None,
    no_mcp: bool = False,
    max_turns: int = 50,
    cwd: str | None = None,
    api_base: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    provider: str | None = None,
    api_style: str | None = None,
) -> dict[str, Any]:
    """Run the agent conversation loop.

    Args:
        prompt: The user's assignment prompt.
        mcp_url: MCP server URL (default from env or http://127.0.0.1:7891/mcp).
        no_mcp: If True, disable MCP tool calls.
        max_turns: Maximum number of agent turns (prompt + response pairs).
        cwd: Working directory for shell commands.
        api_base: LLM API base URL (overrides HIVE_API_BASE env var).
        model: Model name (overrides HIVE_MODEL env var).
        api_key: API key for authentication (overrides HIVE_API_KEY env var).
        temperature: Sampling temperature 0.0-2.0 (overrides HIVE_TEMPERATURE).
        max_tokens: Max response tokens (overrides HIVE_MAX_TOKENS).

    Returns:
        Dict with ok/exit_code/stdout/stderr/result compatible with existing
        log consumers that parse the harness stream-json output.
    """
    mcp_url = mcp_url or os.environ.get("MCP_URL", "http://127.0.0.1:7891/mcp")
    company_dir = os.environ.get("COMPANY_DIR", os.path.join(cwd or ".", ".company")) if cwd else os.environ.get("COMPANY_DIR", "./.company")

    # Build conversation history
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    num_turns = 0
    total_cost_usd = 0.0
    total_actions = 0  # C2: every action ever executed (the result event's num_actions)
    assistant_text = ""
    start_time = datetime.now(timezone.utc)

    while num_turns < max_turns:
        # Call the LLM
        llm_result = call_llm_api(
            messages,
            api_base=api_base or os.environ.get("HIVE_API_BASE"),
            model=model or os.environ.get("HIVE_MODEL"),
            api_key=api_key or os.environ.get("HIVE_API_KEY"),
            temperature=temperature,
            max_tokens=max_tokens,
            provider=provider,
            api_style=api_style,
        )

        if not llm_result.get("ok"):
            if num_turns == 0:
                # B.4: fail fast — the endpoint is missing or unreachable
                # before any work has happened; don't finish "successfully"
                # with an apology. The error already names the provider,
                # the base URL, and the status/body.
                end_time = datetime.now(timezone.utc)
                return {
                    "type": "result",
                    "is_error": True,
                    "subtype": "llm-unavailable",
                    "num_turns": 0,
                    "num_actions": 0,
                    "duration_ms": int((end_time - start_time).total_seconds() * 1000),
                    "total_cost_usd": 0.0,
                    "result": f"LLM unavailable: {llm_result.get('error', 'unknown error')}",
                }
            # Mid-loop failure — report it and stop.
            messages.append({"role": "assistant", "content": f"Error: {llm_result.get('error', 'Unknown')}. Cannot proceed without an LLM backend."})
            break

        assistant_text = llm_result["content"]
        total_cost_usd += llm_result.get("usage", {}).get("total_cost_usd", 0.0)

        # B2: per-call usage entry (caller = HIVE_AGENT identity, mode =
        # agent conversation) — the data `hive llm stats`/`tail` read.
        _log_llm_usage(
            company_dir, os.environ.get("HIVE_AGENT", "harness"), "agent",
            llm_result.get("model", ""), num_turns + 1,
            llm_result.get("usage", {}), assistant_text)

        # C.1: stream-json event for this turn — agent-spawn tees the
        # harness stdout to .company/logs/streams/<id>.jsonl and the
        # dashboard replays these per spawn (assistant text + tool events).
        print(json.dumps({"type": "assistant", "turn": num_turns + 1,
                          "text": assistant_text}))

        # Parse the assistant's response for bash: and mcp: blocks
        actions = parse_actions(assistant_text, no_mcp=no_mcp, mcp_url=mcp_url, cwd=cwd, company_dir=company_dir)

        if not actions:
            # C2: an unparseable NEAR-MISS is fed back, never a success.
            # The turn tried to emit a command (tool_call-style wrapper,
            # function= line, or a bash:/mcp: line in a malformed fence)
            # but parse_actions read nothing — ending the run here is the
            # live-run failure ("successful final answer" with zero
            # actions executed). Append the correction and keep going.
            if _near_miss_command_intent(assistant_text):
                print(json.dumps({"type": "parse-warning",
                                  "turn": num_turns + 1,
                                  "text": assistant_text[:500]}))
                messages.append({"role": "assistant", "content": assistant_text})
                messages.append({"role": "user", "content": PARSE_CORRECTION})
                num_turns += 1
                continue
            # Genuinely no command intent (plain prose) — final response.
            messages.append({"role": "assistant", "content": assistant_text})
            break

        # Execute all actions and collect results
        action_results = []
        for action in actions:
            if action["type"] == "bash":
                print(json.dumps({"type": "tool_use", "tool": "bash",
                                  "args": {"command": action["command"]}}))
                result = run_shell_command(action["command"], cwd=cwd)
                out = f"```\n{result['stdout']}\n```"
                if not result['ok']:
                    out += f"\nError: {result['stderr'].strip()}"
                action_results.append(out)
                print(json.dumps({"type": "tool_result", "tool": "bash",
                                  "ok": bool(result.get("ok")),
                                  "response": out[:8000]}))
            elif action["type"] == "mcp":
                tool_name = action["name"]
                tool_args = action["args"]
                print(json.dumps({"type": "tool_use", "tool": tool_name,
                                  "args": tool_args}))
                result = call_mcp_tool(mcp_url, tool_name, tool_args)
                response_text = result.get("response", str(result))
                if not result.get("ok"):
                    response_text = f"Error: {result.get('error', 'Unknown')}"
                action_results.append(f"MCP tool '{tool_name}' returned: {response_text}")
                print(json.dumps({"type": "tool_result", "tool": tool_name,
                                  "ok": bool(result.get("ok")),
                                  "response": response_text[:8000]}))

        # Append action results as user message for the next turn
        combined = "\n\n".join(action_results)
        messages.append({"role": "assistant", "content": assistant_text})
        messages.append({"role": "user", "content": f"Action results:\n{combined}\n\nContinue or finish your work."})

        total_actions += len(actions)  # C2: the result event reports this
        num_turns += 1

    # Build result event compatible with existing log consumers
    end_time = datetime.now(timezone.utc)
    duration_ms = int((end_time - start_time).total_seconds() * 1000)

    return {
        "type": "result",
        "is_error": num_turns >= max_turns,
        "subtype": "final",
        "num_turns": num_turns,
        # C2: how many actions the run actually executed. "completed
        # successfully with zero actions executed" (the live-run failure)
        # is now checkable by any reader of the stream.
        "num_actions": total_actions,
        "duration_ms": duration_ms,
        "total_cost_usd": total_cost_usd,
        "result": assistant_text if num_turns < max_turns else f"Reached max turns ({max_turns}) without completing.",
    }


def parse_actions(text: str, no_mcp: bool = False,
                  mcp_url: str | None = None, cwd: str | None = None,
                  company_dir: str | None = None) -> list[dict]:
    """Parse bash: and mcp: action blocks from agent output.

    Returns a list of action dicts:
      [{"type": "bash", "command": "..."}, ...]
      [{"type": "mcp", "name": "...", "args": {...}}, ...]

    C1: two block forms are accepted for bash: and mcp: (and SYSTEM_PROMPT
    documents exactly these two — the prompt's own example used to parse
    to zero actions):
      fence-line form: ```bash:<cmd>```          (the keyword glued to the fence)
      bare-fence form: ```\nbash: <cmd>\n```     (a lone fence, then a
                                   line starting with the keyword)
    """
    actions = []

    # Parse bash: blocks — fence-line form (the historically supported one).
    for match in re.finditer(r'```bash:\s*(.+?)\s*```', text, re.DOTALL):
        cmd = match.group(1).strip()
        if cmd:
            actions.append({"type": "bash", "command": cmd})
    # Parse bash: blocks — bare-fence form (the one the prompt showed).
    for match in re.finditer(r'```\n[ \t]*bash:\s*(.*?)\s*```', text, re.DOTALL):
        cmd = match.group(1).strip()
        if cmd:
            actions.append({"type": "bash", "command": cmd})

    # Parse mcp: blocks (only if MCP is enabled), same two forms.
    if not no_mcp:
        for match in re.finditer(r'```mcp:\s*(.+?)\s*```', text, re.DOTALL):
            _append_mcp_action(actions, match.group(1).strip())
        for match in re.finditer(r'```\n[ \t]*mcp:\s*(.*?)\s*```', text, re.DOTALL):
            _append_mcp_action(actions, match.group(1).strip())

    return actions


def _append_mcp_action(actions: list, mcp_block: str) -> None:
    """Parse one tool_name(args) call from an mcp: block and append it to
    actions (shared by both block forms — C1)."""
    # Parse tool_name(args)
    mcp_match = re.match(r'(\w+)\((.*)\)', mcp_block)
    if mcp_match:
        name = mcp_match.group(1)
        args_str = mcp_match.group(2)
        try:
            args = json.loads("{" + args_str + "}")
        except json.JSONDecodeError:
            # Try to parse as key=value pairs
            args = {}
            for pair in args_str.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    k = k.strip().strip('"').strip("'")
                    v = v.strip().strip('"').strip("'")
                    # Try to parse value as JSON
                    try:
                        v = json.loads(v)
                    except (json.JSONDecodeError, ValueError):
                        pass
                    args[k] = v
        actions.append({"type": "mcp", "name": name, "args": args})


# ---------------------------------------------------------------------------
# C2: near-miss command detection
# ---------------------------------------------------------------------------

# The corrective user message fed back when a turn tried to emit a command
# but parse_actions could not read it (C2). It shows the parser's own
# accepted forms so the model can self-correct on the next turn.
PARSE_CORRECTION = (
    "I could not parse that command. Use exactly this form — the keyword "
    "glued to the opening fence, nothing else in the block:\n"
    "```bash: <your command>```\n"
    "or, for MCP tools:\n"
    "```mcp: tool_name(arg=value, arg2=value2)```\n"
    "(A bare fence with the keyword on the next line also works. Do not "
    "wrap commands in any other markup — no tool-call tags, no function= "
    "lines.) Retry the command now."
)


def _near_miss_command_intent(text: str) -> bool:
    """C2: does this turn look like a failed attempt to emit a command?

    True when the turn carries a near-miss marker — the model clearly
    intended a command, so 'zero parseable actions' must be fed back as a
    correction, never treated as a successful final answer (the live-run
    failure: a CEO turn whose only command sat in a broken tool-call
    wrapper ended the run 'successfully' with nothing executed):
      - tool_call-style wrappers — any tag spelling with spaces inside
        ('<tool call>', '</tool_call>', '<Tool-Call>'), the exact failure
        observed in the 0.10.0 field run;
      - a 'function=' line (broken tool-call serialization, e.g.
        'function=bash(command=...)');
      - a bash:/mcp: line inside an UNCLOSED fence — a well-formed bare
        fence parses (C1), so only a malformed one (odd number of fence
        markers, keyword after the last one) lands here.
    A turn with genuinely no command intent (plain prose) must NOT match:
    it ends the run as a final answer.
    """
    if re.search(r'</?\s*/?\s*tool[ _-]?call', text, re.I):
        return True
    if re.search(r'(?m)^\s*function\s*=', text, re.I):
        return True
    if text.count('```') % 2 == 1:
        tail = text[text.rfind('```') + 3:]
        if re.search(r'(?m)^\s*(?:bash|mcp)\s*:', tail):
            return True
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _run_selftest(resolved) -> int:
    """A2: the spawn path, probed for real — the exact pre-turn sequence a
    real agent runs (interpreter, LLM client import, provider resolution),
    one line per step with a final verdict. It never calls the LLM."""
    failures = 0
    root = Path(os.environ.get("HIVE_ROOT", "") or "")
    venv_py = root / "venv" / "bin" / "python" if root else None
    # agent-spawn.sh's `[ -x ... ]` test: a present-but-non-executable
    # venv python is NOT the spawn interpreter (spawn falls back to
    # python3 on PATH), so executable — not merely present — is the
    # contract (pathlib has no is_executable).
    expected = str(venv_py) if (venv_py is not None
                                and venv_py.is_file()
                                and os.access(str(venv_py), os.X_OK)) \
        else sys.executable
    ok = os.path.realpath(str(sys.executable)) == os.path.realpath(expected)
    if not ok:
        failures += 1
    print("[selftest] " + ("ok" if ok else "FAIL") + " interpreter: "
          + sys.executable + " (agent-spawn would pick " + expected + ")")
    if httpx is not None:
        print("[selftest] ok import httpx (LLM client module)")
    else:
        failures += 1
        print("[selftest] FAIL import httpx (LLM client module): "
              "No module named 'httpx'")
    if resolved is None:
        failures += 1
        print("[selftest] FAIL llm_providers.resolve: module not importable")
    elif resolved.is_configured:
        print("[selftest] ok llm_providers.resolve: provider="
              + str(resolved.provider) + " model=" + str(resolved.model)
              + " base=" + str(resolved.base_url or "(default)"))
    else:
        failures += 1
        print("[selftest] FAIL llm_providers.resolve: "
              + llm_providers.unconfigured_hint(hive_root=root))
    if failures:
        print("SELFTEST: FAIL — " + str(failures) + " step(s) failed")
        return 1
    print("SELFTEST: OK — spawn path healthy (no LLM call made)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="agent-harness",
        description="Hive built-in agent harness for spawned agents.",
    )
    parser.add_argument("prompt", nargs="?", help="Prompt text (or read from stdin)")
    parser.add_argument("--no-mcp", action="store_true", help="Disable MCP tool calls")
    parser.add_argument("--mcp-url", default=None, help="MCP server URL")
    parser.add_argument("--max-turns", type=int, default=50, help="Max conversation turns")
    parser.add_argument("--cwd", default=None, help="Working directory")
    parser.add_argument("--api-base", default=None, help="LLM API base URL (overrides HIVE_API_BASE)")
    parser.add_argument("--model", default=None, help="Model name (overrides HIVE_MODEL)")
    parser.add_argument("--api-key", default=None, help="API key (overrides HIVE_API_KEY)")
    parser.add_argument("--temperature", type=float, default=None, help="Sampling temperature 0.0-2.0")
    parser.add_argument("--max-tokens", type=int, default=None, help="Max response tokens")
    parser.add_argument("--provider", default=None,
                        help="LLM provider name (openai, anthropic, groq, openrouter, "
                             "ollama, google, unsloth; overrides HIVE_PROVIDER/[llm].provider)")
    parser.add_argument("--selftest", action="store_true",
                        help="A2: probe the spawn path, no LLM call; exit 0/1")
    parser.add_argument("--api-style", dest="api_style", default=None,
                        help="Wire style override (openai, anthropic, gemini)")
    args = parser.parse_args()

    # A2: the spawn path probe short-circuits before any LLM work.
    if args.selftest:
        resolved = llm_providers.resolve() if llm_providers is not None else None
        return _run_selftest(resolved)

    # Resolve LLM provider config: CLI args > HIVE_* env > [llm] config
    # > provider defaults (lib/core/llm_providers.py). Warnings go to
    # stderr; the result JSON on stdout is untouched.
    if llm_providers is not None:
        resolved = llm_providers.resolve()
        cli_explicit = bool(args.api_base)
        for _w in resolved.warnings:
            # CLI-provided --api-base/--model make the config explicit even
            # when env/config are silent — don't warn about that.
            if cli_explicit and "No LLM provider configured" in _w:
                continue
            print(f"[agent-harness] warning: {_w}", file=sys.stderr)
        if not args.api_base:
            args.api_base = resolved.base_url or None
        if not args.model:
            args.model = resolved.model or None
        if not args.api_key:
            args.api_key = resolved.api_key or None
        if not args.provider:
            args.provider = resolved.provider or None
        if not args.api_style:
            args.api_style = resolved.api_style or None

        # B.4: fail fast — if the effective config cannot call an LLM, exit
        # now (before any work happens) with one actionable line naming
        # what to set and where. CLI-provided base+model count as
        # configured even when env/config are silent.
        if not resolved.is_configured and not (args.api_base and args.model):
            missing = []
            if not (resolved.provider or args.provider):
                missing.append("HIVE_PROVIDER (one of: "
                               + ", ".join(sorted(llm_providers.PROVIDERS)) + ")")
            if not (resolved.model or args.model):
                missing.append("HIVE_MODEL")
            if not (resolved.base_url or args.api_base):
                missing.append("HIVE_API_BASE")
            if resolved.needs_key and not (resolved.api_key or args.api_key):
                missing.append("HIVE_API_KEY")
            what = " / ".join(missing) if missing else \
                "HIVE_PROVIDER, HIVE_MODEL, HIVE_API_BASE, HIVE_API_KEY"
            print("[agent-harness] error: no LLM provider configured — set "
                  + what + " (env vars) or the [llm] section of "
                  "~/.config/hive/config.toml — see share/docs/LLM-PROVIDERS.md",
                  file=sys.stderr)
            # Keep the stream log's last line machine-readable (the
            # dashboard and log consumers parse it as the result event).
            print(json.dumps({
                "type": "result",
                "is_error": True,
                "subtype": "llm-not-configured",
                "num_turns": 0,
                "duration_ms": 0,
                "total_cost_usd": 0.0,
                "result": f"No LLM provider configured — set {what}",
            }))
            return 2

    # Read prompt from arg or stdin
    if args.prompt:
        prompt = args.prompt
    else:
        prompt = sys.stdin.read()

    if not prompt.strip():
        print("Error: no prompt provided (stdin is empty)", file=sys.stderr)
        return 1

    # Set up signal handlers for clean shutdown
    def handle_signal(signum, frame):
        print(f"\n[agent-harness] Received signal {signum}, shutting down...", file=sys.stderr)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Run the agent
    result = run_agent(
        prompt=prompt,
        mcp_url=args.mcp_url,
        no_mcp=args.no_mcp,
        max_turns=args.max_turns,
        cwd=args.cwd,
        api_base=args.api_base,
        model=args.model,
        api_key=args.api_key,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        provider=args.provider,
        api_style=args.api_style,
    )

    if result.get("is_error"):
        # stderr first: the stream log's last line stays the JSON result
        # event (machine-readable for the dashboard and log consumers).
        print(f"[agent-harness] error: {result.get('result', 'failed')}",
              file=sys.stderr)

    # Output as JSON (stream-json format for log consumers)
    print(json.dumps(result))

    return 0 if result.get("ok") or not result.get("is_error") else 1


if __name__ == "__main__":
    sys.exit(main())
