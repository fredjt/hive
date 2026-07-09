#!/usr/bin/env bash
set -euo pipefail

# init-chat-channels.sh - Create chat channel files
# Usage: init-chat-channels.sh [channel1 channel2 ...]
# Defaults: general, engineering, leadership, incidents
# Returns: 0 on success

# Determine COMPANY_DIR
COMPANY_DIR="${COMPANY_DIR:-.company}"

# Default channels if none specified
if [ $# -eq 0 ]; then
  set -- general engineering leadership incidents
fi

# Create each channel
for channel in "$@"; do
  CHANNEL_FILE="$COMPANY_DIR/chat/$channel.md"

  # Skip if already exists
  if [ -f "$CHANNEL_FILE" ]; then
    echo "Channel already exists: $channel (skipping)" >&2
    continue
  fi

  # Create channel file with header
  echo "# Channel: $channel" > "$CHANNEL_FILE"
  echo "Created channel: $channel" >&2
done

echo "Chat channels initialized" >&2
exit 0
