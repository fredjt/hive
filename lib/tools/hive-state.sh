#!/usr/bin/env bash
# hive-state.sh — sourced helper: the single door from bash into the
# .company/ state layer (lib/hive_state).
#
# G1: no bash script carries JSON mutation of .company/ of its own. Every
# read-modify-write goes through `hs` (the hive_state CLI): per-file flock,
# one atomic tmp+os.replace convention, schema validation on write, typed
# accessors. Sourcing this file does not change shell state beyond defining
# hs_* names.
#
#   source "$(dirname "$0")/hive-state.sh"
#   hs ticket-move --dept backend --ticket TICK-B-0001 --status in-review
#
# Exit codes from hs: 0 ok · 1 error · 3 policy denial · 4 schema · 5 not
# found. Policy-denial messages are written to stderr by the CLI.

HS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -n "${HIVE_STATE_PYTHON:-}" ]; then
  HS_PYTHON="$HIVE_STATE_PYTHON"
elif [ -x "$HS_LIB_DIR/../venv/bin/python" ]; then
  HS_PYTHON="$HS_LIB_DIR/../venv/bin/python"
else
  HS_PYTHON="$(command -v python3 2>/dev/null || echo python3)"
fi

# hs <hive_state op...>  — run a state-layer op. COMPANY_DIR must be set
# (exported) by the caller.
hs() {
  PYTHONPATH="$HS_LIB_DIR${PYTHONPATH:+:$PYTHONPATH}" "$HS_PYTHON" -m hive_state "$@"
}

# hs_json <jq filter> — run an op and extract a field from its JSON result.
hs_json() {
  local filter="$1"; shift
  hs "$@" | jq -r "$filter" 2>/dev/null
}
