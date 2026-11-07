# AGENTS.md

This file documents the `this directory` area of the Hive project — what it does, how it works, and the conventions for extending it.

## Purpose

This directory contains **agent prompt files** (`.md`) that define the behavior, rules, and procedures for each role in the Hive virtual company. These prompts are injected into spawned agents via `lib/core/agent-spawn.sh` or loaded by orchestration scripts.

## Prompt architecture

Every prompt file follows a shared structure defined by two foundation files:

- **`_FRAMEWORK_PRIMER.md`** — Universal rules for ALL agents. Loaded first. Covers the sprint-staged lifecycle, `.company/` write restrictions (PreToolUse hooks), MCP tool conventions, environment variables, worktree layout, commit message format, and escalation procedures.
- **`AGENTS-README.md`** — Agent write access policy. Specifies which operations require MCP tools vs. bash scripts vs. direct Write/Edit.

Role-specific prompts extend the primer with their own rules and stage-specific procedures.

## Role hierarchy and file conventions

### Management
| File | Role | Spawn stages |
|------|------|-------------|
| `ceo.md` | Chief Executive — sets per-dept sprint direction | Stage 1 (ceo-plan), Stage 6a (ceo-bugfix-plan) |
| `manager.md` | General manager template | — |
| `tech-lead.md` | Technical lead | — |

### VPs (department heads) — composed prompts (G7)

The fixed-six department VPs are **fragments**, not full prompts:
each `vp-<dept>.md` is the title line + the "<Dept>-specific scope"
section only (< 60 lines). The spawn path composes the real prompt
from the shared base at spawn time:

```
<fragment title line>
<vp-dept.md body up to the seam line>
<blank line + fragment scope section>
<vp-dept.md remainder after the seam>
```

- **Single implementation:** `lib/hive_agents.py` (`compose_dept_vp`).
  Callers: `lib/tools/load-agent-prompt.sh` (`load_dept_vp_prompt`,
  used by `sprint-lifecycle.sh` at the vp-plan/vp-review stages) and
  the `agent_run_vp` MCP tool. Never read a `vp-<dept>.md` fragment as
  a whole prompt — always compose.
- **`vp-dept.md` is itself a live prompt**: dynamic departments
  (HR-created depts beyond the fixed six) spawn the base verbatim, so
  it must stay complete — cross-cutting edits belong in the base.
- **Golden renders:** `tests/golden/vp-<dept>-composed.md` pin the
  composed output (test-first contract; re-capture only with an
  intentional content change).

| File | Department | Spawn stages |
|------|-----------|-------------|
| `vp-dept.md` | Base — complete standalone prompt, composed into the fixed six | Stages 2, 6b, 9 (and dynamic depts, verbatim) |
| `vp-rd.md` | R&D — research, tech debt, prototypes (fragment) | Stages 2, 6b, 9 |
| `vp-it.md` | IT — scaffold, infra, CI/CD (fragment) | Stages 2, 6b, 9 |
| `vp-hr.md` | HR (fragment) | Stages 2, 6b, 9 |
| `vp-marketing.md` | Marketing (fragment) | Stages 2, 6b, 9 |
| `vp-legal.md` | Legal (fragment) | Stages 2, 6b, 9 |
| `vp-finance.md` | Finance (fragment) | Stages 2, 6b, 9 |

### Individual Contributors (ICs)
| File | Role | Spawn stages |
|------|------|-------------|
| `ic-engineer.md` | Writes production code to satisfy failing tests | Stage 4 (tdd-green) |
| `ic-tester.md` | Writes failing tests (TDD-red) | Stage 3 (tdd-red) |
| `ic-infra.md` | Infrastructure / build / CI work | Stage 4 (tdd-green) |
| `ic-admin.md` | Admin tasks, documentation, config | Stage 4 (tdd-green) |

### Verification & Release
| File | Role | Spawn stages |
|------|------|-------------|
| `verification-ic.md` | Adversarial code review — approve or bounce tickets | Stage 5 (verify) |
| `verification-ic-admin.md` | Verification IC for admin/planning tickets | Stage 5 (verify) |
| `release-ic.md` | Adversarial release review before publish | Stage 7 (release-verify) |

### Communication & Reporting
| File | Role | Spawn stages |
|------|------|-------------|
| `qa.md` | QA role | — |
| `verification-manager.md` | Verification management | — |
| `marketing-liaison.md` | Marketing cross-dept communication | — |
| `marketing-reporter.md` | Marketing reporting | — |
| `marketing-researcher.md` | Marketing research | — |

## Prompt structure

Each role-specific prompt typically contains:

1. **Role identity** — Who you are, what you own, what you don't
2. **Spawn stages** — When you're activated and what each mode does
3. **Read-first inputs** — Files/paths to inspect before acting
4. **Action procedures** — Exact MCP tool calls or bash scripts to use
5. **Hard rules** — Script-enforced restrictions (what you must NOT do)
6. **Allowed side actions** — Optional communications (chat, meeting records)
7. **Exit behavior** — What happens when your turn ends

## Adding a new agent prompt

1. Create a new `.md` file in `lib/agents/` following the naming convention: `<role-type>-<name>.md`
   - `ceo.md` — C-suite
   - `vp-<dept>.md` — Department heads
   - `ic-<role>.md` — Individual contributors
   - `verification-<role>.md` — Verification roles
   - `release-<role>.md` — Release roles
   - `<role>.md` — Generic/specialized roles
2. Reference `_FRAMEWORK_PRIMER.md` — do not duplicate its content. The primer is loaded separately.
3. Define which sprint stage(s) the agent participates in.
4. List MCP tools the agent should use (prefer MCP over raw bash scripts).
5. Define hard rules the agent must follow (some are enforced by PreToolUse hooks).
6. If the role needs a new tool script, add it to `lib/tools/` and register it in `lib/hive_mcp/tools/`.

## How prompts are loaded

Prompts are injected into agents in this order:

1. `_FRAMEWORK_PRIMER.md` (universal rules)
2. Role-specific prompt (e.g., `ic-engineer.md`)
3. Stage-specific prompt section (injected dynamically by `load-agent-prompt.sh` or the sprint-lifecycle)

The `load-agent-prompt.sh` script in `lib/tools/` orchestrates this assembly.

## Key interactions between roles

```
ceo.md          →  sets direction for all VPs
vp-*.md         →  files backlog tickets from CEO direction
ic-tester.md    →  writes failing tests for tickets in sprint-current
ic-engineer.md  →  implements code to make tester's tests pass
ic-infra.md     →  handles infra/build tickets
ic-admin.md     →  handles admin/doc/config tickets
verification-ic.md  →  adversarially reviews PRs before merge
release-ic.md       →  reviews release readiness before publish
```

## Important conventions

- **MCP over bash**: All prompts prefer MCP tools (`ticket_*`, `chat_send`, etc.) over raw bash scripts. MCP provides input validation, structured output, and token savings.
- **PreToolUse hooks**: Two hooks block direct writes to `.company/` and admin code writes. Prompts enforce these rules — agents that try to bypass them will get errors.
- **Atomic operations**: Ticket approve/bounce/reject are atomic MCP calls that stamp state + move status + trigger side-effects (PR merge, issue comment). Never call `ticket_move` directly for these transitions.
- **Exit behavior**: Agents exit 0 when done. The sprint-lifecycle reads the exit code and advances the ticket to its next step. Non-zero exit = retry on next iteration.
- **`skip_tdd_red` flag**: Some roles (verification IC) can set this to skip the test-writing stage on bounce. Use sparingly — only when adding tests genuinely wouldn't help.

## See also

- Parent `AGENTS.md` — Framework-level architecture, sprint lifecycle
- `lib/core/AGENTS.md` — Agent spawning via agent-spawn.sh
- `lib/tools/AGENTS.md` — Agent spawning scripts (ic-spawn.sh, load-agent-prompt.sh)
