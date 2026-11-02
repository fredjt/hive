#!/usr/bin/env bash
# entrypoint.sh — run the built-in agent harness against the MCP server.
#
# The container only sees the worktree (mounted read-write at /work).
# /root is an ephemeral tmpfs; .company/ and the host filesystem are not
# visible from inside. The harness talks to:
#   • the hive MCP server over localhost HTTP  (MCP_URL)
#   • the LLM provider API                     (HIVE_API_BASE / HIVE_API_KEY
#                                               / HIVE_MODEL env vars)
# with the container on --network host, so 127.0.0.1 endpoints work.
#
# Usage (from Dockerfile run example):
#   /entrypoint.sh "<prompt>"

set -euo pipefail

PROMPT="$*"
if [ -z "$PROMPT" ] && [ ! -t 0 ]; then
    PROMPT="$(cat)"
fi
if [ -z "$PROMPT" ]; then
    echo "entrypoint: prompt required (argument or stdin)" >&2
    exit 1
fi

exec /opt/hive/venv/bin/python -u /opt/hive/lib/core/agent-harness.py \
  --mcp-url "${MCP_URL:-http://127.0.0.1:7891/mcp}" \
  --cwd /work \
  -- "$PROMPT"
