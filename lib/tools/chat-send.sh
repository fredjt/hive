#!/bin/bash
# Usage: chat-send.sh <channel> <role> <department> <message>
# Example: chat-send.sh general "VP-BackendAPI" "backend-api-services" "Sprint-003 starting."
#
# Channels: general, engineering, leadership, incidents, dept-{name}

COMPANY_DIR="${COMPANY_DIR:-.company}"
CHANNEL="$1"
ROLE="$2"
DEPT="$3"
shift 3
MESSAGE="$*"

if [ -z "$CHANNEL" ] || [ -z "$ROLE" ] || [ -z "$MESSAGE" ]; then
  echo "Usage: chat-send.sh <channel> <role> <department> <message>" >&2
  exit 1
fi

CHAT_FILE="$COMPANY_DIR/chat/$CHANNEL.md"
mkdir -p "$(dirname "$CHAT_FILE")"

# Create channel file with header if it doesn't exist
if [ ! -f "$CHAT_FILE" ]; then
  echo "# Channel: $CHANNEL" > "$CHAT_FILE"
fi

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat >> "$CHAT_FILE" << MSGEOF

---
**[$TIMESTAMP] $ROLE ($DEPT)**
$MESSAGE
MSGEOF

echo "Message sent to #$CHANNEL by $ROLE at $TIMESTAMP"
