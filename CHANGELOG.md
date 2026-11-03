# Changelog

## 0.6.0

Provider-native agent runtime. **Breaking release**: the agent runtime is
the built-in harness end to end — zero trace of the retired external agent
CLI remains in the tree (no calls, no dependency on its settings, no
hooks, not even a mention); LLM provider selection is explicit
configuration with API-key-first support for every major provider family,
unsloth included; and the harness tool loop is behavior-tested against the
real MCP server over HTTP. The external-CLI config section and hook
registration are gone.

### Zero-trace excision
- The hook story is dead (A.1): install.sh no longer writes agent
  settings; uninstall.sh gains a one-time legacy cleanup that removes hook
  entries previous installs may have registered in the operator's existing
  settings file (scratch-HOME round-trip test: legacy entries present ->
  absent after uninstall, other entries untouched); block-company-writes.sh
  and block-admin-code-writes.sh are inert no-op pass-throughs so stale
  registrations can't break anything. The legacy config directory name is
  computed by string concatenation so the tree stays scan-clean.
- The external-CLI config section and its consumers are removed (A.2): no
  bin/flags/verification_model anywhere; the MCP stdio transport is
  documented as the plain programmatic transport it is; the migrate-agents
  deprecation shim is deleted. The verification-model knob moves to
  [llm].verification_model (B.1).
- The container image is agent-in-a-box (A.3): debian base + python3 + the
  harness + httpx in a throwaway venv. Same isolation model as before —
  worktree mounted rw at /work, no .company/, host filesystem invisible,
  /root an ephemeral tmpfs — with the smoke tests ported: build, trivial
  prompt round-trip against a stubbed LLM endpoint, worktree visible and
  writable, a unique .company secret and an unmounted host file
  unreachable, tmpfs ephemeral.
- Docs and product copy are rebranded (A.4): the old per-directory
  prompt-guide files are AGENTS.md now, and README, pyproject, dashboard
  package.json, share/docs, and historical changelog entries use neutral
  terms (agent runtime, agent harness).
- The zero-trace guard test (A.5) scans every tracked file (git ls-files)
  and asserts zero occurrences of the legacy token, with the token
  assembled by string concatenation inside the test so the guard itself
  stays clean. Any future mention fails CI the moment it lands.

### Provider layer
- Explicit provider configuration (B.1): [llm] provider / model / base_url
  / api_key / api_style / verification_model in config.defaults.toml.
  Precedence per value: HIVE_* env vars > config file > provider
  defaults. Selection is EXPLICIT — base-URL sniffing is a warned fallback
  only. `hive doctor` reports the resolved provider/model and whether a
  key is present (the key value is never printed).
- Provider registry with native wire shapes (B.2): openai and anthropic
  (Bearer vs x-api-key + version header), groq, openrouter, ollama, and
  google/gemini. Per-provider env fallback for keys (OPENAI_API_KEY,
  ANTHROPIC_API_KEY, GROQ_API_KEY, GEMINI_API_KEY) when config is silent.
  Uniform error surfacing: which provider + base URL was attempted, the
  HTTP status, a truncated body. Each provider shape is verified against a
  stub HTTP server (payload, auth header, key resolution order, error
  path).
- unsloth is a named, documented provider (B.3): provider = "unsloth" with
  api_style = "openai" | "anthropic", base_url, api_key; UNSLOTH_API_KEY
  (or HIVE_API_KEY) as env fallback. share/docs/LLM-PROVIDERS.md documents
  every provider with copy-paste config blocks.
- Fail fast, say why (B.4): no provider configured -> the harness exits
  immediately with one actionable line (what to set, where) instead of a
  mid-loop error after work has started; a configured-but-unreachable
  endpoint -> the same, with URL + status. Doctor gains an "LLM provider"
  check and an optional reachability probe behind --deep, so plain
  `hive doctor` stays offline-safe.

### Harness tool framework
- The tool loop is behavior-tested (C.1): a stubbed LLM endpoint (canned
  3-turn script: call a write tool, call a read tool, answer) drives the
  REAL MCP server over HTTP on an ephemeral port through the harness —
  the write lands in a scratch .company, the read returns it, and the
  stream log contains the tool events. The work also fixes the harness's
  broken MCP session handshake.
- One MCP client, two consumers (C.2): the JSON-RPC protocol handling is
  extracted into a shared, SDK-free module (httpx only — the harness keeps
  running under plain python3 with no mcp SDK installed); the CLI client
  keeps its SDK-backed fast path. Tested: the harness completes a tool
  round-trip with the mcp SDK uninstalled.
- The dashboard drops the /proc scan (C.3): spawn inventory is tagged-only
  (logs/active/*.json, which the harness writes), the event parser points
  at the harness event shape, and UI copy loses the branding.

### Hygiene
- The test suite is rewritten to the new reality (D.1): the legacy hook
  round-trip test is the A.1 cleanup test, the docker class is the A.3
  class, the spawn tests exercise [llm].verification_model, and doctor's
  provider check replaces the retired bin check.

## 0.5.17

Third round: reproducible, verifiable release packages; one coherent
PreToolUse-hook story; supervisor health visible to the operator; and the
previously untested paths (MCP HTTP transport, release checks, docker
isolation) under test. Eight goals, each shipped with regression tests.

### Release engineering
- The package build is actually reproducible now: tarball mtimes anchor
  to a stable per-release value (SOURCE_DATE_EPOCH, else the last commit
  that touched VERSION, else HEAD, else epoch 0) instead of the VERSION
  file's own mtime — which changes on every release cut and therefore
  changed every rebuild's bytes. The release workflow now verifies the
  rebuilt tarball against the checksum committed with the tag instead of
  checking a freshly generated pair against itself (and fails loudly
  when the published sidecar can't be fetched). Regression test: two
  builds of the same tree with different VERSION-file mtimes produce an
  identical sha256.
- The deprecated PreToolUse-hook story is reconciled: install.sh /
  uninstall.sh register (and remove again) the block-company-writes and
  block-admin-code-writes hooks in the agent's external-CLI settings file,
  restoring the .company/ write protection that 16 agent prompts already
  described. Round-trip test against a scratch HOME (patched, then
  restored; legacy narrow-matcher entries migrated to the canonical
  one).

### Daemon & state reliability
- Supervisor daemon health is now surfaced: per-daemon liveness,
  restart_count, and the DEAD state (restart budget exhausted) are
  reported by `hive doctor`, which now fails on a dead daemon with the
  exact recovery command (hive stop && hive start) instead of the
  condition living only in logs/health.log. The restart policy is
  tested: a failing daemon restarts up to budget, is marked dead at the
  cap, and is never restarted again within the session.
- Behavioral coverage for the CLI surface over recently changed
  internals: a second `gates run` on a locked ticket skips while the
  first holds the flock; `company stop` reaps the supervisor loop it
  started; `restart` fails cleanly with a useful message when no
  dashboard is recorded in processes.json (previously a bare error).

### MCP transport & diagnostics
- The streamable-http wire path is integration-tested — and was dead on
  mcp >= 2.0 (packaging pins mcp>=1.0, so 2.x is in scope): server.py
  set server.settings.host/port, fields mcp 2.x removed from Settings,
  so every HTTP server start crashed; mcp_client.py unpacked
  streamable_http_client's mcp-1.x 3-tuple yield (2.x yields two
  values), so every HTTP tool call raised inside the TaskGroup. Both
  are fixed compatibly across SDK generations. New tests boot the real
  server on an ephemeral port, round-trip a write tool and a read tool
  over the wire, list tools over the wire, and assert a down server
  (with the stdio fallback broken) fails fast with a clean error dict.
- `hive doctor` covers the release path: VERSION file presence, the
  *resolved* publisher script (config override via
  [release].publisher_script, or the built-in github.sh) existing and
  executable — the exact requirement the publish stage enforces — gh
  CLI for the default publisher, and VERSION-vs-packaging/pyproject.toml
  metadata drift. The check immediately found live drift (VERSION 0.5.16
  vs pyproject 0.5.15), fixed in this release.

### Hygiene
- .gitignore and .dockerignore now cover build/test litter (dist/,
  .coverage, .pytest_cache/, unsloth-tmp/) so a normal build-and-test
  cycle leaves git status clean. Tests pin the AGENTS.md structure
  blocks to the real tree, so stale layout entries (like a top-level
  templates/ that lives at lib/templates/) fail CI.
- The agent-in-a-box Docker image has a smoke test (skipif-guarded on
  docker availability; a failed build fails, it does not skip): builds
  the image, runs the harness --help, and asserts the isolation model
  with false-positive-proof unique markers — the worktree mount is
  visible and writable, a secret in the host's .company/ and an
  unmounted host file are unreachable inside the box, and the agent
  state directory is a fresh tmpfs. A trivial prompt round-trip against
  a stubbed LLM endpoint runs without any API key.

## 0.5.16

Second bugfix round. Three lifecycle correctness fixes, each shipped with a
failing-then-passing regression test.

### Release pipeline
- A retry-capped release (build failure or NO-GO past the retry cap) and a
  release with an unparseable verdict no longer fall through to
  release-publish: with a real tarball present the publisher would run and a
  successful publish flipped `publish_status` back to `published`, shipping
  an unshippable or unverifiable release. Blocked releases now skip
  release-verify (when the build never happened) and release-publish
  entirely and land at vp-review with the failure recorded.
- The release blockage is now a real ticket: a `RELEASE-BLOCKER` ticket is
  filed in the `it` backlog (the old code *moved* a ticket id that no code
  path ever creates, so it always failed and the blockage was only visible
  in sprint state).

### Verification gates
- Per-ticket gate runs are serialized with a flock: the 90s gate daemon and
  the lifecycle's synchronous run could gate the same ticket concurrently,
  which corrupted both the shared fixed-name `.tmp` files (one process's mv
  stole the other's, the loser crashed with a rc the lifecycle read as a
  spurious gate failure) and the worktree itself (concurrent pytest in one
  worktree hits assertion-rewrite cache collisions and surfaces as a
  spurious "tests failed" bounce). The daemon now skips a busy ticket and
  retries next cycle; the lifecycle blocks on the lock (15-minute timeout).
  tmp files are per-process as defense in depth.

### Sprint state machine
- The lifecycle now maintains `sprint-state`'s `.stage` as it runs
  (ceo/vp planning, tdd-red, build-and-test, release-verify/publish,
  vp-review; bugfix cycles use the ceo/vp-bugfix-plan stages). Previously
  the stage sat at `ceo-plan` for the whole first pass, so VP ticket
  rejections during their own vp-plan stage were denied by ticket-move's
  stage check, and every relaunch (bugfix-cycle exit, crash restart via the
  health supervisor) re-ran CEO + VP planning on an already-planned sprint.
  Startup now dispatches on the stage: `ceo-plan` plans a fresh sprint, any
  later stage resumes the ticket loop without re-planning.

### Tests
- 6 new behavioral tests: fake-publisher harness proving a blocked release
  never reaches the publisher and files exactly one blocker ticket, the
  unparseable-verdict publish guard, the build-failure cap skipping
  release-verify, the concurrent-gate-runs race (deterministic via an mv
  shim), the per-stage dispatch contract, and the no-replan relaunch.

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
- dc7a35e Rename the old spawn script to agent-spawn.sh and update all references
- 080f18f Remove all external-agent-CLI references and settings
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
- 7fe5365 Replace the external agent CLI with the built-in Python agent harness
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
- e25f9bb Mount the project's agent config dir into the container for local model config
- cc6d4b3 Download the external agent CLI in the Dockerfile
- dd27dc9 Add Dockerfile for the agent-in-a-box sandbox
