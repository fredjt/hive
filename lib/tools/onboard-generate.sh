#!/bin/bash
# Automated onboarding documentation generator for new agents
# Usage: onboard-generate.sh <agent-role> <department> <ticket-id> <project-root> [options]
# Options:
#   --with-readme    Generate department README
#   --with-charter   Generate department charter
#   --full           Generate all documentation

set -euo pipefail

AGENT_ROLE="$1"
DEPARTMENT="$2"
TICKET_ID="$3"
PROJECT_ROOT="$4"
shift 4

# Parse options
GENERATE_README=false
GENERATE_CHARTER=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --with-readme)
      GENERATE_README=true
      shift
      ;;
    --with-charter)
      GENERATE_CHARTER=true
      shift
      ;;
    --full)
      GENERATE_README=true
      GENERATE_CHARTER=true
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

COMPANY_DIR="${PROJECT_ROOT}/.company"
DEPT_DIR="${COMPANY_DIR}/departments/${DEPARTMENT}"
ONBOARD_DIR="${DEPT_DIR}/onboarding"

# Ensure onboarding directory exists
mkdir -p "$ONBOARD_DIR"

# Get timestamp
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Map role to agent template
case "$AGENT_ROLE" in
  VP-*)
    AGENT_TYPE="vp"
    ;;
  IC-Engineer)
    AGENT_TYPE="ic-engineer"
    ;;
  IC-Infra)
    AGENT_TYPE="ic-infra"
    ;;
  Manager)
    AGENT_TYPE="manager"
    ;;
  Verification)
    AGENT_TYPE="verification"
    ;;
  *)
    AGENT_TYPE="generic"
    ;;
esac

# Generate agent onboarding document
ONBOARD_FILE="${ONBOARD_DIR}/${TICKET_ID}-${AGENT_TYPE}.md"

cat > "$ONBOARD_FILE" <<'EOF'
# Agent Onboarding: ${AGENT_ROLE}

**Department:** ${DEPARTMENT}
**Ticket:** ${TICKET_ID}
**Generated:** ${TIMESTAMP}
**Agent Type:** ${AGENT_TYPE}

---

## Welcome to the Team

You are joining the **${DEPARTMENT}** department as **${AGENT_ROLE}**. This document will guide you through your role, responsibilities, and how to get started.

## Your Role

### ${AGENT_ROLE}

EOF

# Add role-specific content based on agent type
if [ "$AGENT_TYPE" = "vp" ]; then
  cat >> "$ONBOARD_FILE" <<'EOF'
As a VP, you are responsible for managing your department. You delegate work but never implement yourself.

**Key Responsibilities:**
- Monitor department ticket board
- Spawn ICs to work on tickets
- Ensure tickets move through workflow
- Coordinate with other departments
- Report status to CEO

**Important Rules:**
- NEVER write code yourself
- NEVER read/write ticket files directly
- Always use ticket scripts
- Spawn ICs in their own git worktrees

### Tools You'll Use

**Ticket Management:**
```bash
export COMPANY_DIR=PROJECT_ROOT/.company
bash SKILL_DIR/lib/tools/ticket-list.sh DEPT [status]
bash SKILL_DIR/lib/tools/ic-spawn.sh DEPT TICKET_ID PROJECT_ROOT SKILL_DIR
bash SKILL_DIR/lib/tools/ticket-move.sh DEPT TICKET_ID status
```

**Chat:**
```bash
bash SKILL_DIR/lib/tools/chat-send.sh CHANNEL "ROLE" "DEPT" "message"
```

EOF
elif [ "$AGENT_TYPE" = "ic-engineer" ]; then
  cat >> "$ONBOARD_FILE" <<'EOF'
As an IC Engineer, you implement features and fix bugs in your isolated worktree.

**Key Responsibilities:**
- Read and understand your ticket
- Work in assigned git worktree
- Write clean, tested code
- Commit with format: TICKET_ID: description
- Move ticket to in-review when done

**Important Rules:**
- Work ONLY in your worktree
- Do NOT do infrastructure work
- Use ticket scripts only
- Move to in-review, not done

### Your Worktree

All your work happens in an isolated directory on its own branch.

### Tools You'll Use

**Ticket Updates:**
```bash
export COMPANY_DIR=PROJECT_ROOT/.company
bash SKILL_DIR/lib/tools/ticket-move.sh DEPT TICKET_ID in-review "summary" "IC-Engineer"
```

**Chat:**
```bash
bash SKILL_DIR/lib/tools/chat-send.sh dept-DEPT "IC-Engineer" "DEPT" "message"
```

EOF
elif [ "$AGENT_TYPE" = "ic-infra" ]; then
  cat >> "$ONBOARD_FILE" <<'EOF'
As an IC Infrastructure Engineer, you handle build systems, CI/CD, and tooling.

**Key Responsibilities:**
- Build system configuration
- CI/CD pipeline setup
- Dependency management
- Development tooling
- Environment configuration

**Your Scope:**
- package.json, tsconfig.json, etc.
- Build scripts and tooling
- CI/CD configuration
- Dependency versions

You do NOT write application code.

### Tools You'll Use

**Ticket Management:**
```bash
export COMPANY_DIR=PROJECT_ROOT/.company
bash SKILL_DIR/lib/tools/ticket-move.sh it TICKET_ID in-review "summary" "IC-Infra"
```

EOF
elif [ "$AGENT_TYPE" = "verification" ]; then
  cat >> "$ONBOARD_FILE" <<'EOF'
As a Verification Engineer, you review and test code. You NEVER fix bugs yourself.

**Key Responsibilities:**
- Review code in in-review tickets
- Test implementations
- File defect tickets for bugs
- Approve or reject work

**Critical Rule:**
NEVER fix bugs yourself. TEST and FILE TICKETS only.

### Tools You'll Use

**Review:**
```bash
export COMPANY_DIR=PROJECT_ROOT/.company
bash SKILL_DIR/lib/tools/ticket-list.sh DEPT in-review
```

**Approve:**
```bash
bash SKILL_DIR/lib/tools/ticket-move.sh DEPT TICKET_ID done "verified" "Verification"
```

**Reject:**
```bash
bash SKILL_DIR/lib/tools/ticket-create.sh DEPT bug high "Bug: desc" "details" "Verification" "defect"
bash SKILL_DIR/lib/tools/ticket-move.sh DEPT TICKET_ID backlog "needs rework" "Verification" rejected
```

EOF
fi

# Add common sections
cat >> "$ONBOARD_FILE" <<'EOF'

## Best Practices

### Git Workflow
- Work in your worktree, never main project
- Commit early and often
- Descriptive commit messages
- Never force push

### Ticket Workflow
- Read ticket thoroughly
- Update status as you progress
- Add comments for decisions
- Let verification handle approval

### Communication
- Post to relevant chat channels
- Keep messages concise
- Report blockers immediately

## Common Pitfalls

1. DO NOT write code in main project
2. DO NOT edit ticket files directly
3. DO NOT skip verification
4. DO NOT do infra work outside IT

## Getting Help

- Check department plan.md
- Review completed tickets
- Ask in department chat
- Escalate blockers to VP

---

Generated by automated onboarding system
EOF

# Substitute variables
sed -i "s|\${AGENT_ROLE}|${AGENT_ROLE}|g" "$ONBOARD_FILE"
sed -i "s|\${DEPARTMENT}|${DEPARTMENT}|g" "$ONBOARD_FILE"
sed -i "s|\${TICKET_ID}|${TICKET_ID}|g" "$ONBOARD_FILE"
sed -i "s|\${TIMESTAMP}|${TIMESTAMP}|g" "$ONBOARD_FILE"
sed -i "s|\${AGENT_TYPE}|${AGENT_TYPE}|g" "$ONBOARD_FILE"

echo "Generated agent onboarding: $ONBOARD_FILE"

# Generate department README if requested
if [ "$GENERATE_README" = true ]; then
  README_FILE="${DEPT_DIR}/README.md"
  
  cat > "$README_FILE" <<'EOF'
# ${DEPARTMENT} Department

**Last Updated:** ${TIMESTAMP}

## Overview

This is the ${DEPARTMENT} department's workspace. All coordination, planning, and ticket tracking happens here.

## Directory Structure

```
${DEPARTMENT}/
  README.md           # This file
  plan.md             # Department strategy
  status.json         # Current status
  charter.md          # Department charter
  tickets/            # Ticket board
    backlog/
    sprint-current/
    in-progress/
    in-review/
    done/
  onboarding/         # Agent onboarding docs
```

## Ticket Workflow

1. **backlog**: Created, not scheduled
2. **sprint-current**: Scheduled for sprint
3. **in-progress**: Being worked on
4. **in-review**: Under verification
5. **done**: Verified and complete

## Working with Tickets

NEVER edit ticket files directly. Use ticket scripts:

```bash
export COMPANY_DIR=PROJECT_ROOT/.company

# List tickets
bash /path/to/tools/ticket-list.sh ${DEPARTMENT} [status]

# Create ticket
bash /path/to/tools/ticket-create.sh ${DEPARTMENT} type priority "title" "desc" "creator" "source"

# Move ticket
bash /path/to/tools/ticket-move.sh ${DEPARTMENT} TICKET_ID status "comment" "updater"
```

## Team Roles

- **VP**: Manages ticket board, spawns ICs
- **IC Engineers**: Implement features/fixes in worktrees
- **Verification**: Review, test, approve/reject

## Communication

- **general**: Company-wide
- **engineering**: Technical discussions
- **dept-${DEPARTMENT}**: Department-specific
- **leadership**: VP-level

## Best Practices

1. Keep tickets small and focused
2. Update status promptly
3. Communicate blockers early
4. Use verification for all reviews

---

For assistance: Post in dept-${DEPARTMENT} chat
EOF

  sed -i "s|\${DEPARTMENT}|${DEPARTMENT}|g" "$README_FILE"
  sed -i "s|\${TIMESTAMP}|${TIMESTAMP}|g" "$README_FILE"
  
  echo "Generated department README: $README_FILE"
fi

# Generate department charter if requested
if [ "$GENERATE_CHARTER" = true ]; then
  CHARTER_FILE="${DEPT_DIR}/charter.md"
  
  cat > "$CHARTER_FILE" <<'EOF'
# ${DEPARTMENT} Department Charter

**Established:** ${TIMESTAMP}
**Version:** 1.0

## Mission Statement

The ${DEPARTMENT} department delivers high-quality code that meets requirements while following engineering best practices.

## Responsibilities

### Primary
- [Define technical responsibilities]
- [List key deliverables]
- [Specify ownership areas]

### Boundaries
- **We DO**: [Department scope]
- **We DO NOT**: [Other departments]

### Interfaces
- **IT**: Infrastructure, build, CI/CD
- **Verification**: Code review and testing
- **Other departments**: [Cross-department interfaces]

## Workflow

1. Created → backlog
2. Scheduled → sprint-current
3. Claimed → in-progress (IC assigned, worktree created)
4. Implemented → in-review (verification needed)
5. Verified → done

## Standards

### Code Quality
- Clean, maintainable code
- Test coverage
- Documentation for APIs

### Git
- Work in isolated worktrees
- Descriptive commits: TICKET-ID: description
- No direct commits to main

### Communication
- Timely updates
- Report blockers immediately
- Document decisions

## Success Metrics

- Velocity: Tickets per sprint
- Quality: Defect rate
- Timeliness: Sprint commitments
- Collaboration: Cross-team coordination

## Values

1. Quality over speed
2. Collaboration
3. Communication
4. Continuous improvement
5. Ownership

---

**Approved by:** CEO
**Department VP:** [To be assigned]
EOF

  sed -i "s|\${DEPARTMENT}|${DEPARTMENT}|g" "$CHARTER_FILE"
  sed -i "s|\${TIMESTAMP}|${TIMESTAMP}|g" "$CHARTER_FILE"
  
  echo "Generated department charter: $CHARTER_FILE"
fi

echo ""
echo "✓ Onboarding documentation generated successfully"
