#!/usr/bin/env bash
set -euo pipefail

# pulse-daemon.sh — high-frequency small-model usage. Every 5 min:
#  1. For each dept: narrate-status → post one-line health summary to dept-<n> chat
#  2. summarize-thread leadership → post digest to general channel
# Skips posting if narration unchanged from previous cycle (avoids spam).
#
# Usage: pulse-daemon.sh <project-root> [--once]

if [ $# -lt 1 ]; then
  echo "Usage: pulse-daemon.sh <project-root> [--once]" >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$1" && pwd)"
ONCE=false
[ "${2:-}" = "--once" ] && ONCE=true

HIVE_ROOT="${HIVE_ROOT:-$HOME/.local/share/hive}"
TOOLS="$HIVE_ROOT/lib/tools"
export COMPANY_DIR="$PROJECT_ROOT/.company"
export LLM_TINY_CALLER="pulse-daemon"

mkdir -p "$COMPANY_DIR/logs"
LAST_DIR="$COMPANY_DIR/logs/pulse-last"
mkdir -p "$LAST_DIR"

post_if_changed() {
  local channel="$1" dept="$2" content="$3"
  local last_file="$LAST_DIR/${channel}.txt"
  if [ -f "$last_file" ] && [ "$(cat "$last_file")" = "$content" ]; then
    return 0
  fi
  printf '%s' "$content" > "$last_file"
  bash "$TOOLS/chat-send.sh" "$channel" "Pulse" "$dept" "$content" 2>/dev/null || true
}

run_one_cycle() {
  if [ -d "$COMPANY_DIR/departments" ]; then
    for dept_dir in "$COMPANY_DIR/departments"/*/; do
      [ -d "$dept_dir" ] || continue
      local dept; dept="$(basename "$dept_dir")"
      [ -f "$dept_dir/status.json" ] || continue
      local narrative
      narrative=$(LLM_TINY_CALLER="pulse-daemon:narrate-status" \
        bash "$TOOLS/narrate-status.sh" "$dept" 2>/dev/null) || continue
      [ -z "$narrative" ] && continue
      post_if_changed "dept-$dept" "$dept" "🐝 $narrative"
    done
  fi

  if [ -f "$COMPANY_DIR/chat/leadership.md" ]; then
    local digest
    digest=$(LLM_TINY_CALLER="pulse-daemon:summarize-thread" \
      bash "$TOOLS/summarize-thread.sh" leadership --last 30 2>/dev/null) || digest=""
    if [ -n "$digest" ]; then
      post_if_changed "general" "company" "📋 Leadership pulse: $digest"
    fi
  fi
}

[ "$ONCE" = true ] && { run_one_cycle; exit 0; }

while true; do
  run_one_cycle
  sleep 300
done
