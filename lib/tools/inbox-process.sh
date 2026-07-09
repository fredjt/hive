#!/bin/bash
# Usage: inbox-process.sh
#
# Processes CEO inbox for new ticket requests.
# Implements expedited routing for HIGH/CRITICAL blocker tickets.
# Routes standard requests through normal ticketing flow.
#
# Called by CEO during work cycles (Step 1: INBOX PROCESSING)

COMPANY_DIR="${COMPANY_DIR:-.company}"
# Honor hive's SKILL_DIR/HIVE_ROOT first; fall back to legacy SKILLS_DIR for
# backwards compatibility with any caller still passing the old name.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${SKILL_DIR:-${HIVE_ROOT:-${SKILLS_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}}}"
TOOLS_DIR="$SKILL_DIR/lib/tools"
[ -d "$TOOLS_DIR" ] || TOOLS_DIR="$SKILL_DIR/tools"

INBOX_DIR="$COMPANY_DIR/inbox"
INBOX_REQUESTS="$INBOX_DIR/user-requests.md"
INBOX_TICKET_REQUESTS="$INBOX_DIR/ticket-requests"
OUTBOX_DIR="$COMPANY_DIR/outbox"
NOTIFICATIONS="$OUTBOX_DIR/notifications.md"
CYCLE_LOG="$COMPANY_DIR/logs/cycle-log.md"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
PROCESSED=0
EXPEDITED=0

# Ensure directories exist
mkdir -p "$INBOX_TICKET_REQUESTS"
mkdir -p "$INBOX_TICKET_REQUESTS/archive"
mkdir -p "$OUTBOX_DIR"
mkdir -p "$(dirname "$CYCLE_LOG")"

touch "$INBOX_REQUESTS"
touch "$NOTIFICATIONS"
touch "$CYCLE_LOG"

# Process user-requests.md for inline ticket requests
if [ -f "$INBOX_REQUESTS" ] && [ -s "$INBOX_REQUESTS" ]; then
  echo "Processing user requests from inbox..." >&2

  # Parse user-requests.md for ticket entries
  # Format: [timestamp] TYPE: {type} PRIORITY: {priority} DEPT: {dept|auto} TITLE: {title}
  # Followed by description lines

  while IFS= read -r line; do
    if [[ "$line" =~ ^\[.*\].*TYPE:.*PRIORITY:.*DEPT: ]]; then
      # Extract metadata
      TYPE=$(echo "$line" | sed -n 's/.*TYPE: *\([^ ]*\).*/\1/p')
      PRIORITY=$(echo "$line" | sed -n 's/.*PRIORITY: *\([^ ]*\).*/\1/p')
      DEPT=$(echo "$line" | sed -n 's/.*DEPT: *\([^ ]*\).*/\1/p')
      TITLE=$(echo "$line" | sed -n 's/.*TITLE: *\(.*\)$/\1/p')

      # Read description (next non-empty line)
      read -r DESC

      if [ -n "$TYPE" ] && [ -n "$PRIORITY" ] && [ -n "$DEPT" ] && [ -n "$TITLE" ]; then
        # Check if expedited routing criteria met
        PRIORITY_LOWER=$(echo "$PRIORITY" | tr '[:upper:]' '[:lower:]')
        TYPE_LOWER=$(echo "$TYPE" | tr '[:upper:]' '[:lower:]')

        if [ "$PRIORITY_LOWER" = "critical" ] || [ "$PRIORITY_LOWER" = "high" ]; then
          if [ "$TYPE_LOWER" = "bug" ] || [ "$TYPE_LOWER" = "infrastructure" ]; then
            # Use expedited routing
            echo "EXPEDITED ROUTING: $TYPE $PRIORITY - $TITLE" >&2
            TICKET_ID=$(bash "$TOOLS_DIR/ticket-route-expedited.sh" \
              "$TYPE" "$PRIORITY" "$TITLE" "$DESC" "user" "$DEPT" "medium" 2>&1)

            if [ $? -eq 0 ]; then
              ((EXPEDITED++))
              ((PROCESSED++))
              echo "[$TIMESTAMP] EXPEDITED: $TICKET_ID created and routed to $DEPT" >> "$CYCLE_LOG"
            else
              echo "ERROR: Expedited routing failed for $TITLE: $TICKET_ID" >&2
            fi
            continue
          fi
        fi

        # Standard ticket creation
        TICKET_ID=$(bash "$TOOLS_DIR/ticket-create.sh" \
          "$DEPT" "$TYPE" "$PRIORITY" "$TITLE" "$DESC" "user" "user-request" "medium" 2>&1)

        if [ $? -eq 0 ]; then
          ((PROCESSED++))
          echo "[$TIMESTAMP] STANDARD: $TICKET_ID created on $DEPT board" >> "$CYCLE_LOG"

          # Notify user
          cat >> "$NOTIFICATIONS" << EOF

[$TIMESTAMP] Ticket created: $TICKET_ID
Title: $TITLE
Department: $DEPT
Priority: $PRIORITY
Status: Backlog

EOF
        else
          echo "ERROR: Ticket creation failed for $TITLE: $TICKET_ID" >&2
        fi
      fi
    fi
  done < "$INBOX_REQUESTS"

  # --- Free-form fallback via classify-ticket.sh (tiny LLM) ---
  # Anything in user-requests.md that didn't match the structured prefix gets
  # batched into paragraphs (blank-line separated) and classified by the small
  # CPU model. High-confidence classifications get filed automatically; the
  # rest stays in a leftover file for the CEO to triage.
  CLASSIFIED=0
  LEFTOVER_FILE="$INBOX_DIR/user-requests-unclassified.md"
  : > "$LEFTOVER_FILE"

  # Build a list of free-form paragraphs (skip lines matching the structured prefix)
  python3 - "$INBOX_REQUESTS" <<'PYEOF' > "$INBOX_DIR/.freeform-paragraphs.tsv"
import re, sys, json, pathlib
src = pathlib.Path(sys.argv[1]).read_text(errors="replace")
struct = re.compile(r"^\[.*\].*TYPE:.*PRIORITY:.*DEPT:")
# Drop structured lines and the description line right after them.
out_lines = []
skip_next = False
for ln in src.splitlines():
    if skip_next:
        skip_next = False
        continue
    if struct.search(ln):
        skip_next = True
        continue
    out_lines.append(ln)
text = "\n".join(out_lines)
# Group into paragraphs separated by blank lines.
paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
for p in paras:
    one_line = " ".join(p.split())
    print(one_line)
PYEOF

  if [ -s "$INBOX_DIR/.freeform-paragraphs.tsv" ]; then
    while IFS= read -r para; do
      [ -z "$para" ] && continue
      RES=$(COMPANY_DIR="$COMPANY_DIR" bash "$TOOLS_DIR/classify-ticket.sh" "$para" 2>/dev/null) || RES=""
      if [ -z "$RES" ]; then
        echo "$para" >> "$LEFTOVER_FILE"
        continue
      fi
      C_TYPE=$(echo "$RES" | jq -r '.type // "feature"' 2>/dev/null)
      C_DEPT=$(echo "$RES" | jq -r '.department // "unknown"' 2>/dev/null)
      C_CONF=$(echo "$RES" | jq -r '.confidence // "low"' 2>/dev/null)

      if [ "$C_CONF" = "high" ] && [ "$C_DEPT" != "unknown" ] && [ -d "$COMPANY_DIR/departments/$C_DEPT" ]; then
        TITLE=$(echo "$para" | cut -c1-60)
        TICKET_ID=$(bash "$TOOLS_DIR/ticket-create.sh" \
          "$C_DEPT" "$C_TYPE" "medium" "$TITLE" "$para" "user" "user-request-classified" "medium" 2>&1)
        if [ $? -eq 0 ]; then
          ((CLASSIFIED++))
          ((PROCESSED++))
          echo "[$TIMESTAMP] CLASSIFIED: $TICKET_ID -> $C_DEPT ($C_TYPE) - $TITLE" >> "$CYCLE_LOG"
        else
          echo "ERROR: classified ticket creation failed: $TICKET_ID" >&2
          echo "$para" >> "$LEFTOVER_FILE"
        fi
      else
        # Low confidence — keep for CEO review
        echo "$para" >> "$LEFTOVER_FILE"
      fi
    done < "$INBOX_DIR/.freeform-paragraphs.tsv"
  fi
  rm -f "$INBOX_DIR/.freeform-paragraphs.tsv"

  # Archive processed requests; surface anything still unclassified back as the inbox
  if [ "$PROCESSED" -gt 0 ] || [ -s "$LEFTOVER_FILE" ]; then
    mv "$INBOX_REQUESTS" "$INBOX_DIR/user-requests-$(date +%Y%m%d-%H%M%S).md"
    if [ -s "$LEFTOVER_FILE" ]; then
      mv "$LEFTOVER_FILE" "$INBOX_REQUESTS"
    else
      rm -f "$LEFTOVER_FILE"
      touch "$INBOX_REQUESTS"
    fi
  else
    rm -f "$LEFTOVER_FILE"
  fi
fi

# Process ticket-requests/ directory for cross-department requests
if [ -d "$INBOX_TICKET_REQUESTS" ]; then
  for request_file in "$INBOX_TICKET_REQUESTS"/*.json; do
    [ -f "$request_file" ] || continue

    echo "Processing ticket request: $(basename "$request_file")" >&2

    # Parse JSON request
    REQUEST_DATA=$(cat "$request_file")

    TYPE=$(echo "$REQUEST_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('type','feature'))")
    TITLE=$(echo "$REQUEST_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('title','Untitled'))")
    DESC=$(echo "$REQUEST_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('description','No description'))")
    TARGET_DEPT=$(echo "$REQUEST_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('target_department',''))")
    PRIORITY=$(echo "$REQUEST_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('priority_suggestion','medium'))")
    CREATED_BY=$(echo "$REQUEST_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('requesting_agent','unknown'))")
    REQUESTING_DEPT=$(echo "$REQUEST_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('requesting_department','unknown'))")

    if [ -z "$TARGET_DEPT" ]; then
      echo "ERROR: No target_department in request file: $(basename "$request_file")" >&2
      mv "$request_file" "$INBOX_TICKET_REQUESTS/archive/"
      continue
    fi

    # Check expedited routing criteria
    PRIORITY_LOWER=$(echo "$PRIORITY" | tr '[:upper:]' '[:lower:]')
    TYPE_LOWER=$(echo "$TYPE" | tr '[:upper:]' '[:lower:]')

    if [ "$PRIORITY_LOWER" = "critical" ] || [ "$PRIORITY_LOWER" = "high" ]; then
      if [ "$TYPE_LOWER" = "bug" ] || [ "$TYPE_LOWER" = "infrastructure" ]; then
        # Use expedited routing
        echo "EXPEDITED ROUTING: $TYPE $PRIORITY - $TITLE" >&2
        TICKET_ID=$(bash "$TOOLS_DIR/ticket-route-expedited.sh" \
          "$TYPE" "$PRIORITY" "$TITLE" "$DESC" "$CREATED_BY" "$TARGET_DEPT" "medium" 2>&1)

        if [ $? -eq 0 ]; then
          ((EXPEDITED++))
          ((PROCESSED++))
          echo "[$TIMESTAMP] EXPEDITED: $TICKET_ID created and routed to $TARGET_DEPT (from $REQUESTING_DEPT)" >> "$CYCLE_LOG"
        else
          echo "ERROR: Expedited routing failed: $TICKET_ID" >&2
        fi

        mv "$request_file" "$INBOX_TICKET_REQUESTS/archive/"
        continue
      fi
    fi

    # Standard ticket creation
    TICKET_ID=$(bash "$TOOLS_DIR/ticket-create.sh" \
      "$TARGET_DEPT" "$TYPE" "$PRIORITY" "$TITLE" "$DESC" "$CREATED_BY" "$REQUESTING_DEPT" "medium" 2>&1)

    if [ $? -eq 0 ]; then
      ((PROCESSED++))
      echo "[$TIMESTAMP] CROSS-DEPT: $TICKET_ID created on $TARGET_DEPT board (filed by $REQUESTING_DEPT)" >> "$CYCLE_LOG"
    else
      echo "ERROR: Cross-department ticket creation failed: $TICKET_ID" >&2
    fi

    # Archive processed request
    mv "$request_file" "$INBOX_TICKET_REQUESTS/archive/"
  done
fi

# Summary output
CLASSIFIED="${CLASSIFIED:-0}"
echo "INBOX_PROCESSING_COMPLETE" >&2
echo "Total processed: $PROCESSED" >&2
echo "Expedited routing: $EXPEDITED" >&2
echo "Tiny-LLM classified: $CLASSIFIED" >&2
echo "Standard routing: $((PROCESSED - EXPEDITED - CLASSIFIED))" >&2

# Return summary as JSON
cat << EOF
{
  "status": "complete",
  "timestamp": "$TIMESTAMP",
  "total_processed": $PROCESSED,
  "expedited_count": $EXPEDITED,
  "classified_count": $CLASSIFIED,
  "standard_count": $((PROCESSED - EXPEDITED - CLASSIFIED))
}
EOF
