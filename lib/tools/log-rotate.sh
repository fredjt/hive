#!/usr/bin/env bash
set -euo pipefail

# log-rotate.sh — compress oversized log files in a company's logs directory.
#
# Called by the health supervisor (company-health.sh). Only does work if files
# exceed size thresholds — designed to be fast (<1s when nothing needs rotation).
#
# Usage: log-rotate.sh <company-dir> [--dry-run]
#
# When a log exceeds the size cap:
#   1. Copy the current log to a temp file
#   2. Gzip the temp file to <log>.<YYYYMMDDTHHMMSSZ>.gz
#   3. Truncate the active log to 0 bytes (keeps the same inode, daemon fd stays valid)
#
# Compressed backups are never deleted by this script — they are readable via
# zcat/zgrep for debugging. If disk space is a concern, use df + manual cleanup
# or the disk usage alerts from company-health.sh.
#
# Configuration (env vars, all optional):
#   HIVE_LOG_MAX_DAEMON_MB  — daemon log size cap (default: 50)
#   HIVE_LOG_MAX_SPAWN_MB   — spawn/VP log size cap (default: 20)

COMPANY_DIR="${1:?Usage: log-rotate.sh <company-dir> [--dry-run]}"
[ -d "$COMPANY_DIR" ] || { echo "Not a directory: $COMPANY_DIR" >&2; exit 1; }

DRY_RUN=false
[ "${2:-}" = "--dry-run" ] && DRY_RUN=true

# Configuration
MAX_DAEMON_MB="${HIVE_LOG_MAX_DAEMON_MB:-50}"
MAX_SPAWN_MB="${HIVE_LOG_MAX_SPAWN_MB:-20}"

BYTES_DAEMON=$(( MAX_DAEMON_MB * 1024 * 1024 ))
BYTES_SPAWN=$(( MAX_SPAWN_MB * 1024 * 1024 ))

LOG_DIR="$COMPANY_DIR/logs"
[ -d "$LOG_DIR" ] || exit 0

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] log-rotate: scanning $LOG_DIR"

# ── Compress and truncate a single log file ──────────────────────────────────
#
# Safe for files that daemons are actively appending to:
# - We copy first (never move or replace the original)
# - We gzip the copy (original stays intact on disk)
# - We truncate the original (keeps the same inode, daemon's fd stays valid)

compress_and_truncate() {
  local log_file="$1"
  local max_bytes="$2"

  local size
  size=$(stat -c%s "$log_file" 2>/dev/null || echo 0)
  [ "$size" -gt "$max_bytes" ] || return 0

  local timestamp
  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  local gz_file="${log_file}.${timestamp}.gz"

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] log-rotate: compressing $log_file (${size} bytes) → $gz_file"

  if [ "$DRY_RUN" = true ]; then
    echo "[dry-run] Would compress $log_file → $gz_file"
    return 0
  fi

  # Copy first — the original must never be moved or replaced
  local tmp_file
  tmp_file=$(mktemp "${LOG_DIR}/log-rotate-tmp.XXXXXX")
  cp "$log_file" "$tmp_file"

  # Gzip the copy to the timestamped backup
  gzip -c "$tmp_file" > "$gz_file"
  rm -f "$tmp_file"

  # Truncate the original (keeps the same inode, daemon's fd stays valid)
  : > "$log_file"
}

# ── Daemon log compression ───────────────────────────────────────────────────
#
# Daemon logs: sprint-lifecycle.log, roster-rebuild.log, status-rebuild.log, etc.
# These are the background service logs written by company-start.sh daemon loops.

for log_file in "$LOG_DIR"/*.log; do
  [ -f "$log_file" ] || continue
  local_basename="$(basename "$log_file")"

  # Skip VP logs and IC logs — handled by spawn log compression below
  case "$local_basename" in
    vp-*.log|ic-*.log) continue ;;
  esac

  compress_and_truncate "$log_file" "$BYTES_DAEMON"
done

# ── Spawn/VP log compression ─────────────────────────────────────────────────
#
# Spawn logs: ic-<TID>.log, vp-<dept>.log
# These tend to grow large per-agent session but are fewer in number.

for log_file in "$LOG_DIR"/ic-*.log "$LOG_DIR"/vp-*.log; do
  [ -f "$log_file" ] || continue
  compress_and_truncate "$log_file" "$BYTES_SPAWN"
done

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] log-rotate: done"
