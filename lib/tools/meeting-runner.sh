#!/bin/bash
# Meeting trigger. Under the round-runner architecture, this is no longer
# a polling daemon — round-promote.sh invokes us with `sprint-review` to
# enqueue a sprint-review entry when one is due. The standup case is gone:
# the round-runner fires standup automatically whenever a round drains.
#
# Usage: meeting-runner.sh sprint-review <project-root> [skill-dir]

MEETING_TYPE="$1"
PROJECT_ROOT="${2:-.}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
SKILL_DIR="${3:-${HIVE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}}"
COMPANY_DIR="$PROJECT_ROOT/.company"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DATE=$(date +"%Y-%m-%d")
TIME=$(date +"%H%M%S")

if [ -z "$MEETING_TYPE" ]; then
  echo "Usage: meeting-runner.sh <sprint-review> <project-root> [skill-dir]" >&2
  exit 1
fi

mkdir -p "$COMPANY_DIR/meetings/history"

case "$MEETING_TYPE" in
  sprint-review)
    MEETING_FILE="$COMPANY_DIR/meetings/history/${DATE}-${TIME}-sprint-review.md"
    PROMPT="You are the meeting facilitator for a sprint review. Project root: $PROJECT_ROOT

Read:
- .company/departments/*/tickets/sprints/current.json (current sprints)
- .company/departments/*/tickets/*/ (ticket counts)
- git -C $PROJECT_ROOT log --oneline -30 (recent commits)
- .company/roster.json (agents)

Write a sprint review to $MEETING_FILE with:

# Sprint Review - $DATE

## Sprint Metrics
For each department with an active sprint:
- Sprint name/number
- Tickets planned vs completed
- Velocity

## Completed Work
List all tickets moved to done/ or archive/ this sprint, with descriptions.

## Carried Over
Tickets that didn't complete.

## Retrospective
What went well, what could improve.

## Next Sprint Plan
For each department, list top 3 backlog tickets to promote to next sprint.

After writing, use sprint-fill to replenish sprints:
bash $SKILL_DIR/lib/tools/sprint-fill.sh --min 3

Post summary:
bash $SKILL_DIR/lib/tools/chat-send.sh leadership Meeting-Facilitator company 'Sprint review complete. Summary at meetings/history/${DATE}-${TIME}-sprint-review.md'

Update .company/meetings/schedule.json: set last_sprint_review to $TIMESTAMP"

    COMPANY_DIR="$COMPANY_DIR" SKILL_DIR="$SKILL_DIR" \
      bash "$SKILL_DIR/lib/tools/round-enqueue.sh" \
        --kind sprint-review --agent meeting-facilitator --dept meetings --role sprint-review \
        --queued-by sprint-review-promoter \
        --log-to "$COMPANY_DIR/logs/meetings.log" \
        --prompt-inline-stdin <<<"$PROMPT" \
        >> "$COMPANY_DIR/logs/meetings.log" 2>&1
    echo "Sprint review enqueued."
    ;;

  standup)
    echo "Standup is no longer triggered via meeting-runner.sh. The round-runner fires standup automatically when a round drains." >&2
    exit 1
    ;;

  *)
    echo "Unknown meeting type: $MEETING_TYPE (only 'sprint-review' is accepted now)" >&2
    exit 1
    ;;
esac
