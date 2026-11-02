# Changelog

## 0.5.15

Bug-fix, hardening, and developer-experience release. Closes the round tracked in
RELEASE_GOALS.md (A.1-A.4 terminal-stage correctness, B.5-B.9 verification surface,
C.7-C.10 test coverage + audits, D.11-D.12 tooling + docs).

### Verification & release pipeline
- Release verdict parsing is robust to real-world formats (leading junk, extra
  whitespace, mixed-case); unparseable verdicts now fail explicitly instead of
  crashing or routing on empty state (B.5, A.3/A.4).
- Terminal stages: NO-GO retry cap defers cleanly; rejected releases skip
  publish; verdict state resets between attempts.
- Bounce count no longer crashes the lifecycle on malformed values (A.1);
  tester loop is bounded when no tdd-red commit exists.
- Security-audit gate SKIP is surfaced as a skip (with reason), not folded into
  a silent pass (B.6).
- Bounce writes exactly one canonical comment (the move comment), ending the
  double-comment spam (B.8).

### Dashboard
- Verification view shows the actual per-gate output; gate buttons carry dept
  context so reports resolve under the right department (B.7, B.9).
- Ticket board: the create form's acceptance criteria and labels are now
  persisted (previously collected and silently dropped); move/comment failures
  are surfaced instead of silently no-opping; missing tickets and failed board
  loads show errors instead of blank/undefined UI (C.10).

### Framework & MCP
- MCP tool audit: round_promote_next now passes the project root (it silently
  used the server's CWD); gh_pr_reject removed (its script is a deprecation
  shim); CLI 'gh pr reject' and dead issues/mirror branches removed (C.9).
- Last bash-variable interpolations in double-quoted 'python3 -c' programs
  swept from sprint-progress.sh and ticket-move.sh, with a lint guard test
  that fails if the pattern returns (C.8).
- ticket-create.sh and the ticket_create MCP tool accept optional --labels /
  --ac (labels, acceptance criteria) and store them on the ticket.

### Tests & developer workflow
- End-to-end happy-path lifecycle test (ceo-plan -> done -> release publish)
  plus terminal-path scenario coverage (C.7, A.3).
- Mechanical guards: MCP wrapper<->script contract audit, no-deprecation-shim
  targets, no python3 -c interpolation, MCP-TOOLS.md drift check.
- Test suite parallelized with pytest-xdist (~66s -> ~15s at -n 8); dev
  dependencies in packaging/requirements-dev.txt; ptw watch loop documented
  in AGENTS.md (D.11).

### Docs
- share/docs/MCP-TOOLS.md regenerated from the live server (55 tools; it had
  drifted to 49 and still listed removed company_start/company_stop)
  via packaging/gen-mcp-tools-doc.py (D.12).

### Commits

- b11eff3 perf+docs: parallel test suite (xdist) + document watch loop (goal 11)
- 8c1560e fix: dashboard ticket board — stop dropping create-form data, surface write failures (goal 10)
- 39f3f54 fix: MCP tool audit — fix + remove two dead-on-arrival tools (goal 9)
- 95b456b fix: sweep last python3 -c interpolations (goal 8) + add lint guard
- 0ea3a5a test: end-to-end happy path through the full lifecycle (goal 7)
- 9f0ceaa fix: make project_root optional on gates_run (C.10)
- 38e5fdd fix: pass dept+ticket context to the dashboard gate buttons (B.9)
- 2e193d0 fix: one comment per bounce (B.8)
- d33e2e7 feat: parse + highlight gate output lines in the dashboard (B.7)
- fb47661 feat: make security-audit skips visible (B.6)
- e47d850 fix: fail loudly on unparseable release-IC verdict (B.5)
- 9147358 fix: clear skip_tdd_red when a ticket reaches done (A.4)
- e6cb952 test: scenario coverage for release terminal paths (A.3) + verdict-state fixes
- 9a9a122 fix: enforce MAX_VERIFICATION_BOUNCES on gate-failure bounces (release goal A.2)
- 885c213 fix: persist bounce_count on verifier-crash bounce (release goal A.1)
- 07989f6 fix: git-worktree.sh create destroyed existing worktree commits
- 0966ba6 chore: ignore local RELEASE_GOALS.md planning file
- 92f75ee fix: stop skip_tdd_red from zombie-ing tickets at gate 0
- 7d300a5 fix: claim the ticket into in-progress before running its lifecycle
- 477a969 fix: TDD-RED tester loop ran unbounded when the tester never committed a test
- 43f4caf fix: verification-gates gated the wrong ticket and treated idle depts as errors
- beb93f5 fix: shell-quote worktree/project paths in generated gate and release test commands
- 4d4fdc4 fix: verification-gates hard-failed every ticket when no security audit tool was installed
- a81cdc3 fix: finish the python3 -c interpolation cleanup in company-*.sh
- d75d6d6 fix: dashboard crashed the whole process on one malformed JSON body
- 32abd1b fix: make ticket_route_expedited MCP tool actually work
- 53029dd fix: remove dead 'hive agent verify' subcommand
- ef5b742 test: run MCP tool tests under mcp 2.x and fix stale expectations
- e407289 fix: honor --dry-run in release-gates.sh
- 3519b19 fix: stop interpolating bash variables into python3 -c string literals
- 18ef1b6 Remove meetings and inbox/outbox buttons and UI from dashboard
- 659a2f3 Fix dashboard server.js: properly remove inbox/outbox API endpoints without breaking structure
- cb54fde Fix dashboard server.js: remove orphaned closing brace from inbox/outbox removal
- 0341161 Fix main.py imports: restore config, doctor, tool, killall, llm, round
- 03b5c16 Fix CI: remove tests for deleted meetings/inbox code and fix round-enqueue.sh syntax
- 04f5ab7 Remove meetings system and inbox/outbox system from dashboard, scripts, and references
- 50dee54 Fix init-workspace.sh: create missing subdirs even when .company/ exists
- 41d81db Fix outdated 'company skill' references in dashboard and company-start
- dc7a35e Rename claude-spawn.sh to agent-spawn.sh and update all references
- 080f18f Remove all Claude Code references and settings
- 99fd2d3 Remove PreToolUse hooks installation from install.sh
- d2e3b44 Clean up remaining scaffold terminology in comments
- 4402a65 Remove deprecated scaffold code entirely
- 3705054 Remove per-project .hive/ scaffolds — projects use installed scripts directly
- 185132d Lock .hive scaffold files read-only after extraction to prevent agent corruption
- ff4f813 Remove project-local .hive directory — no longer editable to avoid risking company integrity
- 931c0c8 Fix mcp v2.x API breakage — FastMCP renamed to MCPServer
- fce247c Fix CI: correct path references for bash-check job and tests
- 5ae6217 Fix CI: mock mcp module if not available for tests
- 3916fa8 Fix CI: improve Rust installation for mcp package build
- 3df8ec2 Fix CI: install Rust toolchain for building mcp dependencies
- f9560b8 Fix CI: use Python 3.10 and 3.11 for tests
- d43633e Fix CI: install build dependencies for mcp package
- 3b6e9e3 Add comprehensive tests for installation and CLI commands
- 9994338 Fix doctor.py NameError for _hive_root variable
- 263f2bc Fix install.sh to exclude .git when copying to install directory
- 261cf06 Fix project root detection to only check CWD, not parent directories
- e1049a9 Fix agent harness auth header handling for local APIs
- 50cee74 Add OpenAI-compatible API support to agent harness
- 7fe5365 Replace Claude Code with built-in Python agent harness
- 4f6951b fix: make tomllib import work on Python 3.10 via tomli backport
- 4c0d728 fix: update tests for removed gh issues integration and missing company dir fixtures
- 8df8406 fix: remove 'cd hive' — checkout is already at repo root
- 1a87f12 ci: add GitHub Actions CI pipeline with tests, bash lint, node check, ruff lint
- c509d2f ci: create workflows dir
- 686c0b6 ci: remove empty directory placeholder
- 269fcc3 ci: create workflows directory
- c185c75 refactor: remove GitHub issues integration system
- b9cfbff refactor: replace llm-tiny.sh small model with local keyword heuristics
- 3b8c0a7 fix: company-stop.sh must not kill the dashboard or MCP server
- 142ab9f fix: prevent company-start.sh from killing existing dashboard
- c4328c8 docs: add AGENTS.md with development workflow and testing guidelines
- 2df2935 test: add comprehensive test suite and fix discovered bugs
- 5f12149 fix: typo 'erif' -> 'verif' in roster verification manager check (company-start.sh)
- 1d09d4c Replace ASCII architecture diagram with DOT-generated image
- 6a82c01 Replace ASCII architecture diagram with DOT-generated image
- 42ba44f dashboard: fix live indicator to show uptime, not last-event time
- 3cee1db Dashboard: replace verification stream with gate output viewer
- e7c127e sprint-lifecycle: replace round-runner/stage-advance with single ticket-lifecycle driver
- e25f9bb Mount project .claude/ into container for local model config
- cc6d4b3 Download claude CLI from official installer in Dockerfile
- dd27dc9 Add Dockerfile for claude-in-a-box agent sandbox
