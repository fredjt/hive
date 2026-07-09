#!/usr/bin/env bash
# entrypoint.sh — prepare Claude Code config from host .claude/ mount, then exec claude.
#
# At runtime the project's .claude/ is mounted read-only at /host-claude.
# The container's /root/.claude is a tmpfs (ephemeral cache). This script copies
# config files from the read-only mount into the tmpfs so Claude Code picks them up.
#
# Usage (from Dockerfile run example):
#   /entrypoint.sh "<prompt>"

set -euo pipefail

# --- 1. Seed config from /host-claude (read-only bind mount) ---
if [ -d /host-claude ]; then
    mkdir -p /root/.claude
    # Copy all files except cache/ (tmpfs is fresh, no stale cache to worry about)
    for f in /host-claude/*; do
        [ -e "$f" ] || continue
        base="$(basename "$f")"
        # Skip cache directories — the tmpfs provides a fresh cache
        if [ "$base" = "cache" ] || [ "$base" = "logs" ]; then
            continue
        fi
        cp -a "$f" /root/.claude/
    done
fi

# --- 2. Ensure cache directory exists ---
mkdir -p /root/.claude/cache

# --- 3. Execute the claude command ---
exec "$@"
