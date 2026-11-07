#!/bin/bash
# config-get.sh <section> <key>  (G5: the ONLY way bash reads hive config)
#
# Resolves the config chain exactly like the Python hive_config module
# (HIVE_CONFIG > ~/.config/hive/config.toml > etc/config.defaults.toml)
# and prints the value. Prints nothing (exit 0) when the key is unset.
#
# No awk/sed TOML parsing anywhere in lib/tools/ — the TOML grammar
# lives in one place (lib/hive_config.py).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HS_LIB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HS_PYTHON="${HIVE_CONFIG_PYTHON:-}"
if [ -z "$HS_PYTHON" ] && [ -x "$HS_LIB_DIR/venv/bin/python" ]; then
  HS_PYTHON="$HS_LIB_DIR/venv/bin/python"
fi
[ -z "$HS_PYTHON" ] && HS_PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$HS_PYTHON" ] || { echo "config-get: no python found" >&2; exit 1; }
PYTHONPATH="$HS_LIB_DIR" "$HS_PYTHON" -m hive_config get "${1:-}" "${2:-}"
