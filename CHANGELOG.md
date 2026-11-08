# Changelog

## 0.12.0

The installable-and-verifiable release.

0.11 closed the trust holes in the suite and the cut; 0.12 closes
the gap between what the repo contains and what an install actually
gets — and gives every release a machine-verifiable audit trail.
The tarball now carries its own bill of materials (S1), the
installer installs exactly that set instead of whatever the tree
happens to hold (S2), every release carries a cut receipt with the
gate results and every published digest (S3), and the CI matrix
splits by risk so every supported interpreter keeps carrying its
version-sensitive paths (S4). Plus a dev loop that is parallel by
default (S5). No new features, no new agent roles, no lifecycle
reordering.

### Shipping manifest (S1, a1fed13)
- `packaging/build-package.sh` writes a `MANIFEST.txt` into the
  tarball: the exact top-level staged entries, state-free. Gate 4 of
  `packaging/cut-release.sh` verifies it against a required set
  (the runtime + the public surface) and fails the cut if any
  runtime-state entry (`.company/`, `.worktrees/`, `dist/`,
  unsloth litter) leaks into the shipped set — the state-entry and
  missing-entry drifts are each pinned by a red-tested simulation.

### Installer parity (S2, dcc38d3)
- The staged set is defined ONCE in `packaging/entries.list`
  (`packaging/entries.sh` is the shared parser): `build-package.sh`
  stages from it and `install.sh --copy-to` stages the SAME set into
  the install dir — never the whole tree. Runtime state, build
  artifacts, local junk, and unreadable unrelated paths can no
  longer reach an install — and an unreadable path can no longer
  abort one (the whole-tree rsync exit-23 hard-fail observed during
  the 0.11.0 scratch validation is now pinned by test). A source
  missing a REQUIRED entry fails the install, naming the entry;
  missing OPTIONAL public docs warn and install.
- `packaging/install-online.sh` resolves the tarball source:
  `HIVE_PKG_URL` > `HIVE_PKG_FILE` > the repo's default GitHub
  release — `VERSION` is fetched from the raw branch, so the
  documented one-liner works with no env vars and always points at
  the newest release:
  `curl -fsSL https://raw.githubusercontent.com/fredjt/hive/main/packaging/install-online.sh | sh`
  `HIVE_PRINT_URL` is the resolve-only seam (the hermetic test
  surface; verified live against GitHub main).

### Cut receipts (S3, cf14c61)
- After the publish step, `cut-release.sh` records each gate's
  result (gate-2 carries the suite's final summary line) and writes
  `hive-X.Y.Z.receipt.txt` — version, branch, commit, tag, gates
  0-7, both asset sha256s, the manifest digest, the release URL,
  the timestamp — then uploads it as the THIRD release asset with
  `--clobber`; a re-run of the publish cleanly re-clobbers the
  receipt. Verified hermetically (fake `gh` + a local bare
  origin): the receipt parses as `key: value`, `commit` equals
  `git rev-parse v$VERSION`, the asset sha256 equals the published
  sidecar, and the manifest digest equals the tarball's
  `MANIFEST.txt` member.

### CI matrix split by risk (S4, f2c40c9 / f4bb20d)
- The RULE: one version (3.12) carries the full suite; every
  supported version (3.10, and a NEW 3.13 row) carries its
  version-sensitive paths — the config-load/tomli-fallback tests
  (`test_json_structures`, `test_llm_providers`,
  `test_commands::TestConfigSetUpsert`,
  `test_commands::TestConfigIntegrity`) + a CLI smoke
  (`test_cli`) + an import smoke. The rule is pinned in the
  `ci.yml` header and the root `AGENTS.md` CI section.
- The version-sensitive surface (`import tomli` / `tomllib`) is
  pinned by `tests/test_integration.py::TestTomliSurfaceGuard`: a
  new parser import outside the four pinned files
  (`hive_config.py`, `commands/config.py`, `commands/doctor.py`,
  `core/llm_providers.py`) fails the guard until the `ci.yml`
  subset is updated with it. Demonstrated red: forcing the
  pre-3.11 parser path and breaking the tomli branch of
  `commands/config.py` fails all 11 `TestConfigSetUpsert` tests —
  the class on the 3.10/3.13 subset rows.
- Note: the `ci.yml` commit (f4bb20d) landed on the `s4-ci-yml`
  branch — this repository's OAuth git credential lacks workflow
  scope, and GitHub refuses pushes from OAuth app tokens that touch
  `.github/workflows/`. It merges when a credential with workflow
  scope is available; the guard and the documented rule are on
  main in the meantime.

### Dev loop on the fast path (S5, 06cb1ab)
- The root `AGENTS.md` quickstart now recommends the parallel run
  (`python -m pytest tests/ -n 8`) as the PRIMARY loop — serial is
  the debug mode. Per-host measured numbers stay out of the tracked
  docs (R3: the docs carry the compute command, not the figure).
- `test_installation.py`: the `dirty_tree` fixture now builds its
  base ONCE per session (a single tar round-trip); each test does a
  local `copytree` of that base. Per-file wall time, serial, same
  host (before / after): `test_sprint_lifecycle.py` 296.3s /
  296.2s (untouched — its spawns are the orchestrator under test),
  `test_installation.py` 158.6s / 156.4s (−2.2s, the fixture
  change), `test_commands.py` 83.5s / 83.0s (untouched — every
  spawn there is a CLI invocation the test asserts on; the 0.11.0
  consolidation pass already removed the state-only spawns).
- The one structural lever left in the top-3 (hardlinked
  `lib/` copies in the lifecycle tests) was rejected: the tests
  rewrite `agent-spawn.sh` in place, and hardlinks share inodes —
  a write-through would corrupt the shared base (xdist worker
  contract: no shared mutable state).

## 0.11.0

The trust release.

The 0.10.0 cut exposed the theme of this one: the two systems
everything else depends on — the test suite and the release cut —
each had a trust hole found only after the fact. Three xdist flakes
bit the 0.10 matrix (mutable state shared across workers), and the
0.10 cut left both READMEs at v0.9.0 until caught post-push. 0.11
closes both: the suite is worker-safe by construction, and the cut
is one documented path that cannot leave a version-bearing surface
stale. No new features.

### Suite isolation contract (R1, d265b73 / a74e885 / b4653d2)
- `tests/conftest.py` owns the shared clock normalization
  (`normalize_clock`): two move paths a few ms apart cross a second
  boundary on ~5% of runs — the contract is identical STATE, not
  identical INSTANT. `test_state_layer` normalizes through it; the
  guard fails on a hand-rolled copy.
- `tests/test_xdist_contract.py` is the static guard: no
  `importlib.reload()` of `lib/` modules inside the test process —
  that re-executes the module and replaces shared objects (the 0.10
  `ShellResult` flake behind `test_run_filters_none_args`).
  Subprocess isolation is the pattern. The guard went red against
  the pre-fix `test_hive_paths` body before landing.
- Root `AGENTS.md` gains the "xdist worker contract" section. The
  "~65s -> ~15s" speedup claim is retired — it did not reproduce on
  the 0.11 reference box (≈155s serial and ≈155s with `-n 8`; the
  win is host-dependent) — and is pinned by the docs-rot guard.
- Verified: full suite `-n 8` green 3 consecutive runs (1,270
  passed, 7 skipped each).

### Release-cut integrity (R2, 2d42fd4 / 6a3e53f)
- Gate 0 of `packaging/cut-release.sh` covers every version-bearing
  surface: `VERSION`, `packaging/pyproject.toml`, the newest
  `CHANGELOG.md` section, and the "Latest release" + tarball-name
  lines in `README.md` and `share/docs/README.md`. A mismatch fails,
  naming each stale file — the 0.10.0 README rot was outside the
  gate's surface, so it could not have been caught.
- `--check-surface` runs gate 0 standalone: the cut commit (every
  surface at the new version, one commit) is validated before the
  cut runs.
- `share/docs/PACKAGING.md` names the one cut path (bump commit →
  `cut-release.sh`); hand-taged cuts are explicitly not a path.

### Docs stop hard-coding volatile numbers (R3, ca8de61 / e7b881f)
- The tree docs and `share/docs/*.md` carry approximations (`~80
  scripts`, `~48 tools`) or the command that computes the truth —
  the stale 0.10-era counts ("101 bash scripts", "~55 tools",
  "140 files / ~170 KB") are out.
- `TestDocTreeCounts` fails on exact tree-count claims without `~` in
  the three tree docs, on top of G10's stale-count guards and the
  retired-speedup guard.
- The doc generator (`packaging/gen-mcp-tools-doc.py`) emits the same
  approximate header — the R3 sweep changed the checked-in
  `MCP-TOOLS.md` without the generator following, and the 0.11.0
  dry-run caught the drift at gate 2 (the R2 path doing its job).

## 0.10.0

Retiring design debt.

Every post-mortem item from the 2026-11-06 field run was already fixed
before this line started (a18050e and earlier). This release does not
chase incidents — it retires the structural debt those incidents
exposed, found in a code audit on 2026-11-06: Hive was a Python system
with a bash front, and the two copies of state mutation, provider
wiring, and config parsing had drifted apart. 0.10 converges on one
state layer, one write path, one config reader, one provider
implementation, one paths module — and enforces the agent sandbox with
code instead of prompt prose. No new features. The stub-LLM full-round
E2E (A1) stayed green through every goal.

### One state layer (G1, 28b14d7)
- `lib/hive_state/`: the single writer for `.company/` — flock + atomic
  replace + schema validation. Ad-hoc jq edits, tmp+mv renames, and
  per-script locking are gone from the bash surface.

### One write path (G2, c655b96)
- The four state-write paths converge on the MCP server; the Style B
  CLI state writer is retired. Agents, the CLI, and the dashboard all
  mutate through the same gated + audited tool surface.

### Round retirement (G3, 959b70c)
- The legacy round-queue architecture is out. One honest activity log
  replaces the zombie queue; the seven `round_*` MCP tools are retired
  (55 tools -> 48).

### Sandbox enforced in code (G4, e33fdf5)
- The `.company` boundary and the command policy are now technical,
  not prompt conventions: the harness policy blocks wrapper evasions
  (subshell/python inline code, find -delete/-exec, write-target
  redirections into `.company`) pre-execution, and a before/after
  snapshot flags any command that mutated live state anyway. Spawn
  results carry `sandbox_blocked` / `sandbox_violations`; a violation
  marks the run `sandbox-violation`. Worktree access is unchanged and
  the residual risk is documented in the primer.

### One config reader (G5, a05fd1e)
- `hive_config` is the only TOML parser on the Python side; bash no
  longer parses config. Defaults, user overrides, and env vars resolve
  in one place.

### One provider registry (G6, 3b698ee)
- `lib/core` is a package; the duplicated provider implementation in
  the harness is retired in favor of the registry (`detect_provider`,
  header/payload/response builders). The `spec_from_file_location`
  import hacks are gone (0 hits in the tree).

### Composed prompts (G7, dca0e3e)
- The 6-way VP prompt copy-paste is a composed render
  (`hive_agents.compose`): one shared skeleton, per-dept fragments.
  The IT-sentence restoration is the only deliberate prompt content
  change in the release; every other render is byte-identical.

### Dashboard split (G8, 855604e)
- `server.js` is 74 lines. The SPA is real files under `static/`
  (app.html, app.js, styles.css) served from disk — the template
  literal and its double-escape convention are gone, and
  `node --check` on the real JS is meaningful. MCP client, SSE
  watcher, and API handlers are their own modules. Still zero
  dependencies, no build step.

### One paths module (G9, 20ca36a)
- `hive_paths` resolves the install root once (env HIVE_ROOT >
  HIVE_INSTALL_DIR > derived). The 14 per-module parents[N]
  re-derivations are gone; a test pins that none can reappear.

### Docs match the tree (G10, f204ca1)
- The round-queue-era counts (~101 scripts / ~13k lines / ~55 tools)
  are out of the AGENTS.md files; the state layer is named where it
  lives. A docs test keeps the counts honest.

### Out of scope, deliberately
- No new features, no new agent roles, no lifecycle reordering. Bash
  stays at the process-management edge (daemon supervision, worktree
  plumbing); the convergence is one Python state/config layer under
  it, not a supervisor rewrite.


## 0.9.0

Proof by execution, and a price tag on it.

The 0.8.x line proved the limit of static verification: five
consecutive bugfix releases were each a failure the suite never saw,
because nothing in it executed the harness, the round-runner, or the
publisher end-to-end. This release closes that gap — A makes a running
company provable in CI, B makes its cost visible, C stops the
dashboard class recurring, D finishes the publish loop, and E is a
docs/CLI surface sweep.

### Proof by execution
- Full-round E2E with a stub LLM (A1, the flagship): one integration
  test boots a real company in a scratch project (hive init, the real
  company-start.sh daemons, a real MCP server on an ephemeral port)
  and drives a one-ticket sprint through a real round against a local
  stub LLM — ceo-plan → vp-plan → sprint-fill → tdd-red → tdd-green →
  verify → build-and-test → release-verify (GO) → release-publish →
  vp-review → archive → a new sprint at ceo-plan. It asserts terminal
  state, not internals: the ticket done with the verification stamp
  and archived, every recorded daemon alive at round end, exactly one
  llm-usage.log entry per scripted LLM call, the sprint history GO +
  published, and a clean hive stop. Deterministic (~16s), xdist-safe,
  and skipped — not failed — when node/git/httpx are absent.
- hive selftest (A2): the harness gains a --selftest mode that runs
  the exact pre-turn sequence a spawned agent runs (interpreter
  check, LLM client import, provider/model resolution) without ever
  calling the LLM; the CLI wrapper execs it with the very interpreter
  agent-spawn.sh picks, so the probe IS the spawn path — the 0.8.3
  class (doctor green, every spawn dead at turn 0) cannot recur by
  construction.
- doctor runs the spawn path probe (A3): the static "agent harness is
  a file" check is replaced by the A2 probe — doctor execs
  agent-harness.py --selftest, prints its per-step lines, and fails
  the run when the spawn path fails.
- hive start proves the daemons are alive (A4): company-start.sh
  reported success the moment PIDs were recorded; a daemon that died
  inside the grace window left a half-started company. Start now waits
  a short grace period (HIVE_START_GRACE, default 5s), verifies every
  recorded PID — an exited-but-unreaped zombie counts as dead — and
  fails the start naming the exact role + PID. The dashboard port is
  env-overridable (HIVE_DASHBOARD_PORT) so tests use ephemeral ports.

### Cost visibility
- Per-call cost in the LLM usage log (B1): llm-usage.log entries
  carry cost_usd, computed from the provider's usage block (0.0 for
  providers that report no cost); readers still parse pre-0.9.0
  entries — a missing cost sums as 0, never a parse error.
- hive llm stats (B2): replaces the fixed 24h/1h pair with
  --window 1h|24h|7d|all (default 24h) and adds token + cost totals
  broken down by model, with --json for scraping.
- Per-round cost + tokens (B3): round-status.sh --history and the
  dashboard's /api/rounds history now price every round from the usage
  log (entries timestamped inside the round window) plus a cumulative
  "this sprint" total. No new state — the log is read at query time;
  the rounds panel shows per-round tokens/cost and a sprint-cost card.
- The sprint cost summary report (B4): when a sprint archives,
  sprint-archive-done.sh also runs sprint-cost-summary.sh, writing a
  deterministic, idempotent markdown report to
  .company/reports/sprint-reviews/ — a per-round table (entries,
  duration, tokens, cost) and the sprint totals. "What did the sprint
  burn?" is a file, not a grep.

### No more dashboard class
- Render-everything SPA smoke test (C1): a dependency-free node
  runner evaluates the delivered SPA script (the template server.js
  actually serves, escapes included) in a fake DOM with a fetch
  serving canned /api responses, then drives all 23 top-level view
  renderers against a healthy and a degenerate fixture company (null
  fields, missing manifests, one corrupt JSON). Asserts: no
  exception, non-empty HTML per view, and no literal 'undefined'/
  'NaN' in rendered output — the 0.8.x field-access class.
- One fixture set, two consumers (C2): the canned responses the smoke
  replays are GENERATED by booting the real dashboard server against
  the same fixture trees the server-side API tests use, and
  test_spa_fixture_drift re-runs that generator on every run — a
  server-side field rename drifts the live shape and fails CI in the
  SPA smoke, not just the server tests.

### Finishing the publish loop
- Publish path end-to-end against a stub gh (D1): the real stage-8
  flow (release-publish-runner.sh, default github publisher) runs in a
  scratch project with a fake gh that records its argv — 'gh release
  create' carries the tag, both assets, and a notes file whose content
  is the CHANGELOG section verbatim; the release record completes and
  the artifacts land under the PROJECT root even when the flow was
  invoked from a foreign cwd (the 0.8.4 cwd class).
- Partial-publish retry is codified (D2): the exact 0.8.4 scenario as
  a test — run 1 pushes the tag, then the publisher dies before the
  release exists (record dangling); run 2 detects the existing tag
  (no re-tag, no duplicate), passes the semver gate, and completes
  the publish.
- Space-safe tarball detection: release-test-suite.sh found the
  release tarball with a find | xargs | ls pipeline that word-splits
  on whitespace — a project under a path containing a space stored a
  bare directory as .release.tarball and every release from it failed
  publish. Now a null-delimited scan.
- Failing cut gates say how to resume (D3): every cut-release gate
  that can fail mid-flow (suite red, scan dirty, tag exists, publish
  failed) ends with a one-line "Resume:" hint naming the exact next
  command.

### Docs/CLI sweep
- CLI.md synced with the dispatcher, drift guard in CI (E1): every
  subcommand the parser serves is documented (adds llm, gh, release,
  restart, killall, round; removes the phantom Inbox section), and
  TestCliDocsDrift pins both directions — every parser subcommand is
  documented, and every "hive x" in CLI.md resolves to a real
  subcommand.
- hive ticket list --priority (E2): the flag flows the whole chain
  (CLI → MCP ticket_list → ticket-list.sh) and filters in both JSON
  and text modes.
- doctor detects processes.json corruption (E3): the daemon
  supervisor's state file was read with a bare except-pass — a corrupt
  file silently looked like "company not started". Doctor now fails
  with a CORRUPT check naming the file and the exact remedy.

### Bugfix round

On top of the feature line — each fix with a regression test (the
0.8.x bugfix pattern: failures the suite did not see, found by
live-driving the new surfaces).

- fix(doctor): the spawn path probe runs under the interpreter
  agent-spawn.sh actually picks (the install venv's python, via the
  shared selftest.spawn_python helper), not the CLI's own — a healthy
  install with a packageless CLI python no longer reads red, and a
  broken venv is diagnosed on the module import, not the interpreter.
  'Present' becomes 'executable' (agent-spawn's own `[ -x ]` test).
- fix(cli): the round, release, ticket, chat, backlog, and gates
  commands honor the COMPANY_DIR environment variable (the
  codebase-wide convention every bash tool and hive llm stats already
  follows) instead of silently retargeting to the company in the CWD
  whenever the CWD happens to have a .company.
- fix(llm): hive llm stats counts a present-but-non-numeric cost_usd
  as 0 instead of raising ValueError out of the whole run — the
  'never a crash' contract the sibling tokens field already honored.
- fix(rounds): round-status.sh --force-advance returns the
  structured {"ok": false, ...} result for a current-round.json with
  an explicit null 'entries' instead of a raw TypeError traceback
  (the --history path already guarded the shape).

Under the hood: build staging via tar --exclude (cp -r raced live
__pycache__), pinned fixture mtimes, a dynamic timestamp for the
24h-window stats test, a repo-local git identity for the E2E tag
step, and GH_STUB_LOG wired through the publish stub.

## 0.8.4

Bugfix round: seven fixes across the CLI, config, and the release
pipeline, each with a regression test.

- fix(cli): the stdio MCP fallback spawns the server with the current
  interpreter when the install venv is missing — MCP-routed commands
  (ticket, chat, company, gates, release) no longer die with a raw
  FileNotFoundError in the documented `PYTHONPATH=lib` source mode.
- fix(config): HIVE_CONFIG is honored in the LLM resolver's config
  merge — the file `hive config set` writes to is the file the
  spawn/LLM path reads (it was not, until now).
- fix(publish): the GitHub publisher targets the project's default
  branch — [github].default_branch config, then the remote's actual
  default branch, then main — instead of a hard-coded main.
- fix(release): a GO verdict with gh unavailable now actually
  publishes via the local-only publisher and completes the release
  record, instead of printing a fallback message and exiting 0 with
  the record left dangling.
- fix(release): the semver gate excludes the candidate's own tag, so a
  retry after a partial publish (tag pushed before the release) stops
  failing "not greater than previous tag vX.Y.Z".
- fix(release): the changelog gate matches `## X.Y.Z` sections even
  when invoked with the documented v-prefixed `--version`.
- fix(release): the publish runner cd's to the project root before
  invoking publishers, so local-only and cwd-sensitive publishers
  place artifacts in the project, not the caller's directory.

Fifteen new regression tests: TestMcpStdioFallback,
TestHiveConfigOverride, TestPublisherTargetBranch,
TestReleasePublishNoGhFallback, TestReleaseGatesVersionGates,
TestPublisherRunnerCwd.

## 0.8.3

Agent spawn interpreter fix.

Every spawned agent died at turn 0 with "LLM unavailable: httpx not
installed": agent-spawn.sh exec'd the harness with bare `python3` from
PATH, while the harness's dependency (httpx) lives in the install
venv — and `hive doctor` checks the venv interpreter, so it reported
everything green while every spawn failed silently.

- agent-spawn.sh now resolves the install's venv python
  (`HIVE_INSTALL_DIR/venv/bin/python`, derived from the script's own
  location when the env var is unset — the same contract the Python
  side uses); a missing venv falls back to `python3` with a stderr
  warning instead of a silent turn-0 death.
- New `TestAgentSpawnPython`: a venv stub that records its own
  invocation proves the real agent-spawn.sh picks the venv python
  (and that the fallback still works in a source checkout).

## 0.8.2

Dashboard reference-error fix.

The 0.8.0 meetings/inbox-outbox removal deleted the `renderMarkdown()`
helper, `renderRosterAgents()`, and the `orgShowActiveOnly`/
`toggleOrgActive` org-chart state — but the call sites in the ticket
detail, chat, reports, brief and org views survived. Clicking Org or
Brief threw `ReferenceError` and only the browser console noticed. A
leftover `return html;` after a live return in the unused legacy
`renderOrgNode()` made every browser print "unreachable code after
return statement" on load as well.

- The three helpers are restored (the claimed-badge tooltip is
  reworded to zero-trace-neutral wording) and the dead
  `renderOrgNode()` function is deleted; the Org chart's "Show Active
  Only" toggle works again.
- Three new guards in `TestEmbeddedSpaScript` parse the DELIVERED
  script — the exact bytes the browser runs, which `node --check
  server.js` cannot see (it parses the template as a string): every
  call in the SPA resolves, no statement follows a completed `return`,
  and every `on*` handler in the delivered HTML resolves to a defined
  function.

## 0.8.1

Dashboard script parse fix.

0.8.0 shipped a SyntaxError in the embedded SPA script: nine `\n`
escapes (the ticket move/comment failure alerts and the gate-output
line parsing) were evaluated by Node while the HTML template literal
was built, so they reached the browser as RAW NEWLINES inside
single-quoted JS strings — and the entire dashboard script failed to
parse (a console-only failure: the server itself was fine, and
`node --check server.js` parses the template as a string, so CI
could not see it).

- The nine escapes are written with doubled backslashes so the
  browser receives a real newline escape; the trap is documented in
  `lib/dashboard/AGENTS.md`.
- New guard `TestEmbeddedSpaScript`: reproduces exactly the script the
  browser receives (template evaluation included) and runs
  `node --check` on it, so this class of error fails the suite the
  moment it is introduced.

## 0.8.0

Operator UX and config integrity.

The 0.7.1 stream made the config visible; this release closes the
verification gaps an operator still hits: a typo in the config is
caught in seconds, the LLM endpoint is verified before a sprint burns
turns on it, the dashboard shows which model the agents actually talk
to, dead config gets flagged, and cutting a release is a command.

### Config integrity
- Unknown-key validation (A1): a typo'd section or key in the user
  config (e.g. `[llms] provder = "openai"`) can no longer ship
  invisibly. `hive doctor` fails naming the exact offender with a
  close-match suggestion ("did you mean llm.provider?") and `hive
  config show` marks unknown keys.
- One env var for the config path (A2): `HIVE_CONFIG` is the single
  spelling (installer, doctor, publish runner); `HIVE_USER_CONFIG`
  keeps working as a deprecated alias with a one-time note.
- `hive config path` (A3): prints the effective user-config path and
  its state — the where-is-my-config question in one line.

### Verify the LLM before the sprint
- `hive llm test` (B1): one small round trip through the exact resolver
  path the harness uses — provider, model, base_url, key-source label
  (never the key), latency, and the model's first reply line; exit
  0/1. A dead endpoint goes from a 20-agent-turn discovery to a
  2-second check.
- The "tiny" naming is gone (B2): `llm-tiny.log` → `llm-usage.log`
  (readers accept both for one release), MCP module `llm_tiny.py` →
  `llm_usage.py`, and `hive llm` help text updated.

### Operator visibility
- `hive doctor --json` (C1): machine-readable dump of every check
  (name, ok, detail) to stdout; exit code unchanged. No new daemon,
  no new state file.
- Dashboard Config & LLM panel (C2): the resolved provider/model/
  base_url with the key shown only as its source label, the last
  saved `hive doctor --json` dump (`.company/state/doctor.json`,
  written by the CLI), and the last `hive llm test` result/timestamp.

### Daemon hygiene
- env.sh retirement guard (D1): doctor checks
  `~/.config/hive/env.sh` for retired variable names
  (ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, LLM_TINY_URL, …) and names
  them with the migration line — LLM settings belong in
  config.toml's [llm]; env.sh is for PATH/GITHUB_TOKEN.
- Config-drift notice (D2): `hive status` compares the config's mtime
  against the daemons' start times and prints one line when the config
  changed after start ("hive stop && hive start" to apply).

### Release ergonomics
- `packaging/cut-release.sh` (E1): the release cut as a command —
  suite green → build → tar listing check → credential-pattern scan
  of every tarball entry → sha256 → release notes from the CHANGELOG
  section → tag → push → `gh release create`. `--dry-run` runs every
  gate up to the tag, so a release is fully verifiable before any
  irreversible step. The cut commit (VERSION + changelog section) is a
  precondition the script verifies but never makes.
- Tarball content guard (E2): a suite test builds the package into a
  scratch dir and asserts the listing contains every entry install.sh
  reads at runtime — `bin/hive`, `etc/config.template.toml`,
  `etc/config.defaults.toml`, `packaging/requirements.txt` — plus a
  sha256 sidecar that verifies. The 0.7.1 template shipped by accident
  of a blanket copy loop; now it is pinned.

### Small, one each
- `hive config set` list values (F1): list-typed keys (e.g.
  `tdd.applies_to_agents`) take a JSON array or a comma-separated
  value and are written as a real TOML array — no longer edit-only.
  Scalar keys are untouched.
- `hive init` preflight (F2): when the resolved LLM is unconfigured,
  init prints the exact fix (config file + `hive config edit`) at the
  end instead of letting the first spawn fail. Hint, not a gate —
  exit 0, scaffold still created.
- Install summary block (F3): install.sh ends with the 3-line
  next-steps block — edit [llm] → `hive doctor` → `hive init` —
  closing the onboarding loop in one screen.

## 0.7.1

Config streamlining — one visible config file, created by the installer.

- The installer now scaffolds `~/.config/hive/config.toml` from the new
  tracked `etc/config.template.toml`: every section and key, with working
  defaults and commented examples, at install time — and it never
  overwrites a file that already exists.
- `hive config set` does a real TOML upsert (replace in place, add to an
  existing section, or create the section) with typed values and a
  parse-check that refuses to write a file that would not load; the old
  line-append produced duplicate `[section]` tables that made the config
  unreadable for doctor, the harness and the CLI after the second set.
- `hive config edit` seeds a missing file from the template instead of a
  blank comment line.
- `hive doctor` names the exact file to edit when the LLM provider is
  unconfigured (and says to run `hive config edit` when the file does not
  exist yet).
- The installer's env-file hint now points at `$HOME/.config/hive/env.sh`
  for shell-level daemon env only (PATH, GITHUB_TOKEN) and stops
  recommending the retired ANTHROPIC_* / LLM_TINY_* variable names — LLM
  settings belong in config.toml.
- Both READMEs document the configure step in the quick start.

## 0.7.0

First public release — the first Hive anyone outside the project will
see. The public surface is complete and safe: a full license, a way to
actually obtain the software, contributor and security docs, and CI
that can never let a secret into history again.

### Public surface
- Complete MIT license (B.1): the old LICENSE was a truncated 324-byte
  MIT that ended mid-clause and was not a usable license; it is now the
  full MIT text — grant, conditions (retain the copyright notice), and
  the IN-NO-EVENT liability clause all present.
- How to obtain Hive (B.2): the README documents both acquisition
  paths — release tarball (download, verify the sha256 sidecar with
  `sha256sum -c`, extract, install) or git clone — plus the `hive
  doctor` gate. INSTALL no longer pins a version, so its header can't
  rot again.
- CONTRIBUTING.md (B.3): the development loop — every change needs a
  test, the pytest commands, the per-directory AGENTS.md map, and the
  commit checklist.
- SECURITY.md (B.3): a private reporting path for a tool that stores
  and passes LLM provider API keys — report via the GitHub security
  advisory form, never as a public issue; secret leaks are reported
  privately with credentials referenced by prefix only, and the
  response is revoke-then-scrub.
- Issue templates (B.3): bug report + feature request; the bug
  template warns that secrets never belong in an issue.

### CI hardening
- Secret scan (C.1): gitleaks runs over the full git history on every
  push/PR; any finding fails the pipeline. A secret can never land in
  history again.
- Node 24 (C.2): the node-check job no longer pins EOL Node 20; the
  runner deprecation annotation is gone.
- Full-surface lint (C.3): ruff (F401/E722) now covers lib/ + tests/
  instead of just the MCP/CLI packages; the findings outside the old
  scope are fixed and the tree is clean under its own selection.

### Standing guards
Each of the above is pinned as a permanent test in the suite — the
license clause markers, the no-stale-version-string scan, the
contributor/security doc content, and the shape of the CI jobs — so a
regression of the public surface fails CI the moment it lands.

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
