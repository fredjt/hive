#!/bin/bash
# Usage: chat-read.sh <channel> [--last N] [--since TIMESTAMP] [--json]
# Example: chat-read.sh general --last 10
# Example: chat-read.sh engineering --since 2026-04-10T12:00:00Z
# Example: chat-read.sh dept-backend-api-services --json

COMPANY_DIR="${COMPANY_DIR:-.company}"
CHANNEL="$1"
shift

LAST=""
SINCE=""
JSON=false

while [ $# -gt 0 ]; do
  case "$1" in
    --last) LAST="$2"; shift 2 ;;
    --since) SINCE="$2"; shift 2 ;;
    --json) JSON=true; shift ;;
    *) shift ;;
  esac
done

if [ -z "$CHANNEL" ]; then
  echo "Usage: chat-read.sh <channel> [--last N] [--since TIMESTAMP] [--json]" >&2
  echo "" >&2
  echo "Available channels:" >&2
  ls "$COMPANY_DIR/chat/"*.md 2>/dev/null | xargs -I{} basename {} .md
  exit 1
fi

CHAT_FILE="$COMPANY_DIR/chat/$CHANNEL.md"

if [ ! -f "$CHAT_FILE" ]; then
  echo "Channel #$CHANNEL not found" >&2
  exit 1
fi

if [ "$JSON" = true ]; then
  # Parse messages into JSON (env-var inputs; path/channel/last/since all
  # come from the environment so quotes or spaces in any of them are safe)
  CR_CHAT_FILE="$CHAT_FILE" CR_CHANNEL="$CHANNEL" CR_LAST="$LAST" CR_SINCE="$SINCE" python3 - <<'PYEOF'
import sys, json, re, os

content = open(os.environ['CR_CHAT_FILE']).read()
blocks = content.split('\n---\n')
messages = []
for block in blocks:
    block = block.strip()
    if not block or block.startswith('# Channel'):
        continue
    match = re.match(r'\*\*\[([^\]]*)\]\s*([^(]*)\(([^)]*)\)\*\*\s*(.*)', block, re.DOTALL)
    if match:
        messages.append({
            'timestamp': match.group(1).strip(),
            'role': match.group(2).strip(),
            'department': match.group(3).strip(),
            'message': match.group(4).strip()
        })
    else:
        messages.append({'timestamp': '', 'role': 'system', 'department': '', 'message': block})

last_raw = os.environ.get('CR_LAST', '')
since_raw = os.environ.get('CR_SINCE', '')
last = int(last_raw) if last_raw else None
since = since_raw if since_raw else None

if since:
    messages = [m for m in messages if m['timestamp'] >= since]
if last:
    messages = messages[-last:]

json.dump({'channel': os.environ.get('CR_CHANNEL', ''), 'count': len(messages), 'messages': messages}, sys.stdout, indent=2)
print()
PYEOF
else
  # Plain text output
  if [ -n "$LAST" ]; then
    # Split by --- and get last N messages
    awk 'BEGIN{RS="---"; ORS="---"} {a[NR]=$0} END{start=NR-'$LAST'; if(start<1)start=1; for(i=start;i<=NR;i++) print a[i]}' "$CHAT_FILE"
  elif [ -n "$SINCE" ]; then
    # Show messages since timestamp (env-var inputs)
    CR_CHAT_FILE="$CHAT_FILE" CR_SINCE="$SINCE" python3 - <<'PYEOF'
import os
content = open(os.environ['CR_CHAT_FILE']).read()
since = os.environ.get('CR_SINCE', '')
blocks = content.split('\n---\n')
for block in blocks:
    if since in block or any(line for line in block.split('\n') if '**[' in line and line.split('[')[1].split(']')[0] >= since):
        print('---')
        print(block.strip())
PYEOF
  else
    cat "$CHAT_FILE"
  fi
fi
