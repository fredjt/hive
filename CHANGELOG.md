## 0.21.0

0.21 starts the dashboard arc. 0.15-0.20 covered the install path, the
suite, the CLI faces, and the read path; the one face the user actually
looks at while the company runs — the dashboard — was the least solid
surface in the tree: the live-update path was broken at three
independent, measured layers (the SSE transport, the event-to-view
wiring, and the spawn data contract), the GitHub page read a file
nothing in the tree writes, and a refresh threw the user back to the
ticket board. 0.21 repairs the foundation and pins each repair with a
live-server test, so the next dashboard release builds on a floor that
holds. Every change is a dashboard module, a SPA line, or a test —
the scripts and tools the agents call are untouched, every --json
envelope stays byte-identical, and no exit code moves in this release.

### The Activity page watches what's actually happening (T1)
- The spawn data contract speaks one dialect now: `spawn_id` in the
  record, the stream pointer, and the dashboard's read side resolve it
  with a fallback chain (`spawn_id` -> legacy id -> record filename),
  so a 0.20-shaped orphaned active record survives an upgrade readable
  by the 0.21 dashboard.
- Watch opens a REAL spawn: the per-spawn event count climbs per
  second on a live spawn (0.20: the viewer never opened — the stream
  file was addressed by an id nothing wrote, 0 events forever).
- Watch Global merges every active stream: active_count and rendered
  events for every in-flight spawn, per-event updates while an
  appender runs (0.20: `{"total":0,"active_count":0}` with a 26-line
  file on an alive spawn). The Events column counts the file's real
  lines (0.20: 0).

### The SSE transport actually delivers (T2)
- The response head and the `: connected` line arrive at connect on an
  IDLE company (0.20: 0 bytes until the first file change, measured
  >2 s) — `res.flushHeaders()` + the comment at the head of the
  stream.
- A keepalive heartbeat (`: hb` every `HIVE_DASHBOARD_SSE_HB_SECS`,
  default 15 s) keeps the idle pipe warm: a byteless pipe is a dead
  pipe (proxies and browsers give up silently; EventSource fires
  onerror, not onopen).
- `setupWatchers` no longer throws `STATUSES is not defined` for any
  company with a department ticket board (the whole watcher tree never
  installed; the board's ticket dirs fired no ticket-change).
- The SPA shows what the transport is doing: `reconnecting…` is a
  state of its own with a 5 -> 10 -> 30 s backoff (0.20: a flat 5 s
  loop), and the live indicator carries the last-byte age — "live"
  means bytes are arriving, not that a socket once opened.
- spawn STARTING or STOPPING fires its own event (`logs/active` ->
  `spawn-registry-change`); the wiring gaps close: the global stream
  refreshes on spawn-stream-change, sprint-change and report-change
  reach the Releases view, ticket-change reaches the Git view, and
  gh-change refreshes the GitHub page.

### The GitHub page shows GitHub's actual state (T3)
- gh is the source of truth: every PR and issue on the remote is a
  row (`gh pr list --state all`; `gh issue list` open + closed). The
  sidecar (`.company/integrations/github-mirror.json`) — written by
  nothing in the tree — is demoted to a ticket-link overlay +
  last-good cache and is now written by the sync itself on success.
- The Hive ticket column maps `feature/<TID>` branches (sidecar
  explicit links win); deep links come from `git remote get-url
  origin` (no remote -> plain numbers, no dead anchors).
- `_diagnostics` names which check failed (gh on PATH / gh authed /
  remote configured / network) alongside `_sync_error`, and the panel
  renders the diagnostic block (named check + actionable fix) over
  the last good state — instead of a warning badge over two empty
  tables.
- A dashboard-process timer (`HIVE_DASHBOARD_GH_SYNC_SECS`, default
  60 s — no new daemon) re-syncs on a connected company and
  broadcastSSEs `gh-change` on any state delta: a PR merged outside
  the dashboard flips on the page within one interval, no
  navigation.

### Refresh keeps you where you are (T4)
- A hash route per top-level view (`#/board/<dept>`, `#/chat/<channel>`,
  `#/activity/spawn/<id>`, `#/activity/global`, `#/logs/<file>`,
  `#/rounds/<id>`, ...): every showX() writes the hash and drilldowns
  update it, so the URL a user is looking at is the URL that gets them
  back, and back/forward work (0.20: reload from any view -> All
  Departments; the back button had never done anything).
- Drilldowns rehydrate from the hash (a completed spawn re-renders in
  full from the `from=0` replay; a live spawn resumes and follows via
  the existing SSE wiring); a bad hash falls to the parent view's
  normal empty state — a bad hash never blanks the app.

Before/after — the 0.20.0 and 0.21.0 faces, side by side (R3):

| face | 0.20.0 | 0.21.0 (this host) |
|---|---|---|
| SSE on an idle company | 0 bytes until the first file change (>2 s) | head + `: connected` at connect; `: hb` every 15 s |
| Watch (live spawn) | viewer never opens (spawnId ""), 0 events | viewer opens; the per-spawn count climbs per second |
| Watch Global | `{"total":0,"active_count":0}` with a 26-line file on an alive spawn | spawn visible, events rendered, per-event updates while the appender runs |
| Events column / total | 0 | the file's line count |
| GitHub page | Issues 0 (always); ticket column "?"; dead anchors; no live refresh | real issues/PRs; mapped tickets; deep links; gh-change refresh; the diagnostic block with gh absent |
| refresh on any view | lands on All Departments | stays (hash routing + rehydration) |
- The upgrade path from v0.20.0 verifies (the 0.15 T5 contract:
  scratch-install the published 0.20.0, install the staged tree on
  top, doctor it) — including a 0.20-shaped orphaned active record
  surviving the upgrade readable by the 0.21 dashboard (the T1
  fallback chain's field case).
- PARKED (named in the release notes so the deferral is visible, not
  silent): the SDK-free in-process rung, the worker-count guidance,
  the CI 3.14 row — 0.22 candidates.

## 0.20.0

0.20 is the speed-and-hand release. 0.17/0.18/0.19 covered what the
workhorses SAY (the happy paths, the empty states, the when-it-goes-
wrong faces); the two things those releases deliberately measured and
deferred are this release: the read-path architecture (every CLI
command on a stopped company spawned a dead stdio server before its
work began) and the ticket family's agent-form inputs (4–5
positionals each, whose only consumer is a human). A third measurement
joined: the suite wall drifted +33s over 0.18 on fixture copy time.
After 0.20 the stopped company answers in about half the wall with no
dead-process tax, the ticket commands take the human's form (one
positional — the title — plus named flags; author defaults to the
running user), and the suite wall is back at the 0.18 level. No new
daemons, roles, stages, or providers; no invariant weakened; the MCP
wire contract the agents ride is byte-identical (the in-process path
runs the SAME tool functions the server exposes — one implementation,
no transport); every --json envelope stays byte-identical; no exit
code moves in this release.

### The stopped company answers in-process (T1)
- The CLI's transport ladder loses the dead process. mcp_client now
  walks three rungs: (1) Streamable HTTP when the server is up — the
  SDK client when the SDK is importable, the shared SDK-free client
  (lib/core/mcp_http.py, httpx only) otherwise; (2) IN-PROCESS when it
  is down — build_server() once per CLI process (cached) and the
  registry's .fn called directly: the very function object FastMCP
  wraps, so the envelope is byte-identical by construction — one
  implementation, no transport, no second SDK import, no spawn;
  (3) the house error envelope when there is neither. The one-shot
  stdio spawn (and its per-spawn NOTE line) is retired from the CLI;
  the stdio SERVER transport stays for programmatic consumers.
- The mcp SDK import is LAZY: importing hive_cli.main no longer
  imports the SDK (subprocess-pinned: 'mcp' absent from sys.modules);
  it resolves once per process on the first tool call or list, so
  `hive version` / `hive --help` / stop / killall / config pay only
  the CLI import.
- Argument filtering mirrors the wire: the transport validates
  against the tool's parameter schema (pydantic, extra ignored) —
  unknown keys are DROPPED, e.g. the CLI's historical `force` key on
  ticket_move. The in-process rung filters the same way (pinned):
  without it a stopped company's `hive ticket move` died with a
  TypeError the wire never showed.
- list_tools() takes the same ladder (HTTP → the in-process registry —
  the keys the except branch already introspected; one path, no
  spawn); the SDK-free wire path and its `[]` are unchanged.
- Tests: the existing stopped-company face tests (roster/status/ticket
  list/gates/release) now ride the in-process rung and pin the faces
  byte-for-byte (their env is the dead port — the same golden, the
  new transport); new pins: transport identity (the same tool over a
  live ephemeral-port HTTP server and in-process returns EQUAL
  dicts), the wire's unknown-key drop, the lazy-import subprocess,
  the moved wording ("in-process fallback unavailable"), the retired
  stdio spawn (no StdioServerParameters / stdio_client / _stdio_session
  in mcp_client.py — house surface-guard shape) and the stdio server
  transport's survival.

### The ticket family takes the human's hand (T2)
- create: the one thing a human always has — the title — is the single
  positional; the rest are named flags, and the state layer's choice
  lists (TICKET_TYPES / TICKET_PRIORITIES from hive_state.core — one
  source of truth) sit in the `--type` / `--priority` help:
  `hive ticket create <title> --dept D --type T --priority P
  [--description DESC] [--created-by NAME] [--source SRC]
  [--estimate EST]`. `--description` is OPTIONAL (ticket-create.sh
  requires dept/type/title only); the payload passes "" and the state
  layer omits the empty field from the doc.
- move: comment/author leave the positional line —
  `hive ticket move <dept> <id> <status> [--comment C] [--author A]
  [--reason R] [--force]` (ticket-move.sh takes them as optional
  positionals; the CLI passes them positionally, the flags are the
  human's interface).
- comment: the body is the third positional, `--author` a flag —
  `hive ticket comment <dept> <id> <comment> [--author A]`.
- author defaults to getpass.getuser() (the 0.18 T3 precedent — a
  human does not re-type who they are); the MCP payload shape is
  unchanged and every --json envelope is byte-identical (same tool
  call, same keys).
- The 0.19 forms are the named usage error (rc 1, the 0.19 T1
  contract): the 5-positional create is pinned to create NO ticket.
  claim/approve are unchanged (their consumers carry machine
  identities).
- Tests: the lifecycle class's mutations take the new forms; the
  comment golden shows the default author (the running OS user); the
  retirement pin and the omitted-description create are new; the
  parser tests and the in-process-fallback e2e moved with the forms.
  CLI.md's ticket section moved in the same commit (the docs test's
  checked lines; the stale type/priority lists now read the state
  layer's real lists).

### The suite wall is back at the 0.18 level (T3)
- The 0.19 duration profile measured the +33s drift as fixture COPY
  time, not test work. The biggest copy: TestInstallScaffoldsUserConfig's
  install_tree — a per-test copytree of the session install base
  (venv + runtime core) paid five times for five tests that only
  differ in their scratch HOME.
- install_tree is class-scoped: ONE copy per class, the 5 install runs
  share the tree (the session base stays the read-only copy source —
  the S5 contract unchanged at the session level). Safety, verified:
  the user-config branch never writes into the tree (only the venv
  branch does, idempotently) and ALL FIVE tests pass on the one
  shared tree.
- The class is @pytest.mark.xdist_group-pinned (the worker contract
  for a class-scoped mutable fixture).

### The cut (T4)
Before/after — the 0.19.0 and 0.20.0 faces, side by side (R3):

| command | 0.19.0 | 0.20.0 (this host) |
|---|---|---|
| `hive roster` (stopped company) | 2.38s wall + the spawn NOTE line | 1.29s wall, no NOTE |
| `hive status` (stopped company) | 2.13s wall + the spawn NOTE line | 1.10s wall, no NOTE |
| `hive version` / `hive --help` | 0.99s each | ~0.25s each (no SDK import) |
| `hive ticket create` (new ticket) | `rd bug high "Fix login" "It 500s"` | `"Fix login" --dept rd --type bug --priority high` |
| `hive ticket move` | 5 positionals (+reason) | 3 positionals + `--comment`/`--author`/`--reason` |
| `hive ticket comment` | 4 positionals | 3 positionals + `--author` (defaults to the running user) |
| suite wall, this host, `-n 8` | 249.42s (1552 passed, 6 skipped — the cut's gate-2) | 206.35s (1558 passed, 6 skipped) |

Price, against the 0.18.0 baseline (R3) — the wall RETURNS: the
read path's dead-process tax and the per-test fixture copy are both
gone, and +24 tests did not grow it.

| metric (0.18.0 → 0.20.0) | before | after |
|---|---|---|
| suite wall, this host, `-n 8` | 216.41s (1534 passed, 6 skipped) | 206.35s (1558 passed, 6 skipped) |
| CI test-job wall (3.12 / 3.13 rows) | 245s / 328s (run 43173304337) | 205s / 266s (run 43279872920 — both green) — both rows faster than 0.19's, on the same rows |

## 0.19.0

0.19 is the when-it-goes-wrong release. 0.17 and 0.18 covered the
workhorses' happy paths and the empty states they name; the second-
order surface — the commands a human runs when something is wrong (the
LLM misbehaves, a ticket is stuck, the backlog is empty, there is no
release record yet) and the meta-command (help) — still spoke wire
format: a fact reported twice, a stutter, a conflation, a silence,
and help commands that exit as if they had failed. 0.19 closes that
surface. Every fix is an in-process CLI face and exit code: the
scripts and tools the agents call are untouched, and every --json
envelope stays byte-identical. No new daemons, roles, stages, or
providers.

### `hive --help` exits 0: help is not a failure (T1)
- `main()` now honors argparse's SystemExit code — 0 (help) → 0,
  non-zero (usage error) → 1 — instead of collapsing both into rc 1.
  The help text was correct all along; the code lied about the
  outcome. The suite that named the contract but never checked it
  (a try/except around a call that no longer raised) is now a real
  walk of EVERY registered subcommand (top-level and nested:
  ticket/chat/agent/gates/backlog/config, gh + gh pr + gh push,
  release, llm) asserting `--help` exits 0, plus one usage error
  pinned at rc 1.
- Before/after: `hive --help` — 0.18.0: correct help, rc 1 → 0.19.0:
  the same help, rc 0. `hive --help && next` stops failing under
  `set -e`.

### `hive llm` names its states (T2)
- `llm test` unconfigured: `llm test: LLM unconfigured — unconfigured
  — edit the [llm] section of ...` → `llm test: unconfigured — edit
  the [llm] section of ...` — exactly one "unconfigured". The call
  site drops the prefix; the shared hint is byte-identical for the
  doctor/harness/start consumers (doctor's line did not move —
  pinned).
- `llm stats`/`tail` with no usage log: the conflated `No
  llm-usage.log at ... Either no calls have happened yet, or you're
  not in a hive project root.` (rc 1) splits in two, both actions,
  both faces: a workspace with no calls yet — healthy, expected —
  says `No LLM calls recorded yet — the usage log
  (.company/logs/llm-usage.log) appears after the company's first
  call.` and exits 0 (`stats --json` prints the same zero document
  the empty log already produces — one contract: no calls = the zero
  document, whether or not the file exists); a directory that is not
  a hive project says `not a hive project root — no .company here;
  run 'hive init'` and exits 1 (`stats --json` prints the house
  error envelope).
- `llm tail` on a log that exists but is empty: silence (rc 0) → the
  visible zero line `No LLM calls recorded — the usage log is
  empty.` (rc 0) — the silent-empty hole the 0.17/0.18 T3 family
  closed for `status` and `roster`; `stats` on the same empty log
  already printed the zero face, so the two actions of one command
  agree.
- Everything else is byte-identical: populated-log stats/tail
  (goldens), the llm-test.json state file (C2), and the key-label
  discipline (B1 — a label, never the key).

### One face per fact: backlog and gates (T3)
- The human face stops double-reporting: `present.finish` gains a
  `pass_stderr` opt-out (default is today's always-show — no other
  command moves); the backlog and gates-run handlers opt out,
  because the renderer IS the face and the raw notes stay where the
  machine reads them (the --json envelopes are byte-identical,
  including `{"stderr": "No backlog for rd"}`).
- `hive backlog prioritize rd` on a fresh workspace: two lines for
  one fact (the script's `No backlog for rd` note passed through on
  top of `Backlog for rd is empty.`) → exactly one line, exit 0.
- `hive backlog prioritize <dept>` naming a department that does not
  exist: rc 0 + the empty-answer face → rc 1 + the roster's face
  verbatim, `ERROR: Department '<dept>' not found (available: ...)`
  — the CLI-side known-dept check (the org chart, the source
  ticket-list.sh uses; the script is untouched). Three commands, one
  face for a named-missing department.
- `hive gates run <dept>` with nothing to gate: two raw log lines
  (an ISO timestamp, `(strict=...)`) + the derived sentence → the
  one sentence, `Nothing to gate — no in-review tickets in
  <dept>.`; a PASS/BOUNCE run renders as in 0.18.0 (goldens);
  exit codes and the --json envelope unchanged.
- `hive gates check <dept> <id>` with no report — the common case,
  every ticket before its first gate run: the lowercase raw
  `no gates report found` (rc 1) → `No gates report for <id> — the
  ticket is not in-review yet, or its gates have not run; run
  'hive gates run <dept>'.` (the rc did not move; only the face
  did).
- `gates status` config block: Python `True`/`False` → `true`/
  `false` (the config table's spelling, 0.18 T1).

### `hive release status` gets one sentence and the right code (T4)
- `hive release status v0.17.0`: `No release record found (no
  release record found)` with rc 0 → `No release record for
  v0.17.0 — 'hive release list' shows what is recorded.` with
  **rc 1** — the one rc move of this release (the house matrix: 0
  iff the call succeeded; every other not-found query in the CLI
  exits 1).
- `hive release status` (no version, no sprint record) — the NORMAL
  state of a fresh workspace, no sprint has reached stage 6 — wore
  the same stuttered sentence as a lookup miss: → `No release in
  flight — a release record appears when a sprint reaches stage 6
  (build-and-test); 'hive release list' shows what is published.`,
  exit 0 (named absence, the 0.17 T3 family).
- The tool's envelope and the --json face are byte-identical
  (`{"error": "no release record found"}` — the machine contract
  does not move); `release list` is untouched (golden).

### The xdist pin was dead config (test infrastructure)
- The 0.18 worker contract pinned the suite to `--dist=loadgroup` in
  `pytest.ini` — under a section name pytest does not read. pytest
  reads the `[pytest]` section of a pytest.ini file; the
  `[tool:pytest]` spelling (setup.cfg's section) in a pytest.ini is
  SILENTLY ignored — every option in it reads back as the default
  (verified on pytest 8.4 and 9.1: addopts reads back as `[]`). The
  pin never ran: --dist fell back to per-test load, and the ordered
  end-to-end classes — the ones whose class-scoped fixture carries
  ordered state — could split across two workers, each worker's
  fixture in its own temp dir, the final read-path test seeing the
  unmutated state. Every green gate since 0.18 (gate-2, the CI
  rows, the 0.19 T1/T2 suite runs) was per-test luck; the T2 run
  drew the split (TestTicketHumanFace: three of the class's tests
  failed — the mutations had landed on another worker).
- The section is now `[pytest]`, and the pin is provably live:
  `test_pytest_ini_uses_the_section_pytest_reads` (the file parses
  as `[pytest]` with the option in it — the section name IS the
  contract) and `test_loadgroup_pin_is_live` (a nested `pytest -n 4`
  — subprocess, contract-safe — of a group-marked probe class, 7
  bumps + a final read, that passes only if all eight tests land on
  one worker, i.e. only if the ini is read and the group pinned).
- The live pin immediately caught the NEXT upstream regression: the
  cut's CI row failed the way the contract was supposed to make
  impossible — both full rows split TestTicketHumanFace ('RD-0104'
  where 'RD-0105' was expected) and the probe failed. Locally green,
  red on CI: CI installs the latest pytest, which since 2026-10-27 is
  9.2.0 — and pytest 9.2.0 broke xdist's loadgroup distribution
  (upstream pytest-dev/pytest#15303: xdist_group-marked tests scatter
  across workers, silently, no error; reproduced in a clean full-dep
  venv — the class passes on 9.1.1 and splits on 9.2.0 with the same
  xdist 3.8.0, pin from addopts or explicit on the CLI). The xdist
  fix (pytest-dev/pytest-xdist#1402) is unmerged, so the release
  carries the ecosystem workaround: `pytest<9.2` in ci.yml and
  requirements-dev.txt (holoviz/panel#9006 capped the same way).
  Revisit the cap when a fixed xdist release ships.

### The cut (T5)
Before/after — the 0.18.0 and 0.19.0 faces, side by side (R3):

| command | 0.18.0 | 0.19.0 |
|---|---|---|
| `hive --help` | the correct help, **rc 1** | the same help, **rc 0** |
| `hive llm test` (unconfigured) | `LLM unconfigured — unconfigured — edit ...` | `llm test: unconfigured — edit ...` (one word) |
| `hive llm tail` (empty log) | **silence**, rc 0 | `No LLM calls recorded — the usage log is empty.`, rc 0 |
| `hive llm stats` (no log, in a workspace) | the conflated message, **rc 1** | `No LLM calls recorded yet — ...`, rc 0 |
| `hive backlog prioritize rd` (fresh) | two lines | one line |
| `hive backlog prioritize nosuchdept` | rc 0 + the empty face | **rc 1** + `ERROR: ... (available: ...)` |
| `hive gates run rd` (nothing to gate) | two raw lines + the sentence | the one sentence |
| `hive release status v0.17.0` | the stutter, rc 0 | the one sentence, **rc 1** |

Price, against the 0.18.0 baseline (R3) — expected flat: every
target is an in-process CLI face over files that do not move.

| metric (0.18.0 → 0.19.0) | before | after |
|---|---|---|
| suite wall, this host, `-n 8` | 216.41s (1534 passed, 6 skipped) | 258.34s (1552 passed, 6 skipped) — the +42s carries the +18 tests and the scheduling change the infra fix activated: 0.18's wall ran under the DEAD pin (plain per-test load); 0.19's runs under the real loadgroup, where the 7 ordered e2e classes are single-unit work on one worker instead of split chunks |
| CI test-job wall (3.12 / 3.13 rows) | 373s / 248s (run 43127286924) | 245s / 328s (run 43173304337, the cap commit's row — both green) — the per-row slow side flipped between releases; the row set is runner-variance, the code is the same both ways |


## 0.18.0

Three questions, answered.
0.17 made the tool speak English; 0.18 closes the loop on what a
human actually asks the terminal: where is the key configured (and
can I see it), who is working on what, and can I file a note to the
board in one line. All three are in-process CLI faces over scripts
and wire that do not move: `config` never prints the key on any
face, `roster` rides the existing `roster_get` path, and `chat send`
takes the one-arg form the agents already use. The cut itself
surfaced a latent 0.17 xdist scheduling defect; the fix is pinned by
the worker contract.

### The config command never prints the key (T1)
- `hive config show` is now a SECTION / KEY / VALUE / SOURCE table —
  the documented precedence (HIVE_* env > user config > install
  default) made visible per key: SOURCE is `env <HIVE_*>` (the env
  value is shown) when the override is set, else `user config`, else
  `default`. `hive config show --json` is the merged document with
  the secrets masked; the unknown-key warnings stay on stderr and the
  `_unknown_keys` marking stays top-level in the JSON.
- Secret keys (api_key, apikey, authorization, token, password,
  secret — case-insensitive, at any section depth — the same family
  the dashboard strips from `/api/config`) never print their value:
  `get` and the table read `<set — masked>` for a set key (a
  secret's env value is masked like every other layer's), an empty
  value prints as empty, and an unset key stays unset (exit 1,
  `(unset: <key>)`). The config CLI was the one surface in the
  system that printed the key in plaintext — doctor, `llm test`, and
  the dashboard already named only the source. Non-secret `get`
  output is byte-identical to 0.17.0.
- `hive config path` resolves the file the same way the rest of the
  command does (the HIVE_CONFIG env var wins) — a HIVE_CONFIG
  override is no longer a path that exists while every other action
  read the default.
- LLM-PROVIDERS.md's standard now covers every surface.

### `hive roster` — who is working on what, in the terminal (T2)
- A new `hive roster [dept] [--active] [--json]` — read-only,
  CLI-only: it rides `roster_get` (the same script path, the same
  rebuild-before-read), so no script and no wire contract moves.
  The default renders NAME / ROLE / DEPT / STATUS / TICKET / LAST
  ACTIVE — active rows first, then claimed, then inactive — ending in
  the count line (`N agent(s) — M active`). On a never-run workspace
  the listing IS the state: the six inactive VPs + `0 active` say
  "company not running" without a dead-end branch.
- The read path's silent-empty hole is closed: roster-update.sh's
  `list` prints `[]` with exit 0 when roster.json is absent —
  reachable only when the rebuild it triggers fails (or there is no
  .company at all) — so "rebuild broken" and "no agents" were
  indistinguishable to the reader. The CLI now names the states: no
  .company → `No .company here — run 'hive init'` (exit 1);
  roster.json still absent after the forced rebuild → `roster
  unavailable — the rebuild failed; run 'hive doctor'` (exit 1); a
  present roster with zero agents → a count-0 listing (exit 0); an
  unknown department → the error with the known departments (exit
  1). `--json` prints the roster document's `.agents` array —
  exactly what roster_get returns: one byte-identical contract for
  the CLI and the MCP side.

### `hive chat send <channel> <message>` (T3)
- The one-arg form the agents already use: author defaults to the OS
  user running the command and department to the channel's own
  department (`dept-rd` → `rd`; a named channel → itself) — the same
  defaults the agents carry inline (the harness and the agent
  prompts), now in one place. `--author` / `--dept` override; the
  MCP payload shape is unchanged (chat_send gets the same four
  keys). A send without a message names its usage error (exit 1)
  instead of argparse's positional complaint; the `--json` envelope
  is unchanged (stdout face).
- The 0.17 positional form's test pins moved with the change.

### The cut, and the xdist contract fix it surfaced (T4)
- gate-2's first run on the 0.18 tree caught a latent 0.17 defect:
  under the default `--dist=load`, xdist sends the pending queue in
  consecutive chunks (up to `--maxschedchunk`, 8 by default), so an
  ordered end-to-end class with more tests than a chunk can be split
  across two workers — each worker then instantiates the class-
  scoped fixture in its OWN temp dir, and the test that reads state
  mutated by an earlier same-class test sees the unmutated fixture
  (TestTicketHumanFace: "5 ticket(s) in rd" read as 3, the mutations
  having landed on another worker). 0.17's CI passed by luck of the
  chunk draw.
- The fix: pytest.ini runs `--dist=loadgroup` (per-test scheduling
  for unmarked tests — the suite's load profile is unchanged —
  group-unit scheduling for marked classes), and every class whose
  class-scoped fixture carries ordered state carries
  `@pytest.mark.xdist_group` (the seven ordered e2e classes in
  test_commands.py + TestEmbeddedSpaScript in test_dashboard.py).
  The new static guard (`test_xdist_contract.py::
  TestXdistGroupContract`) fails a class-scoped fixture without the
  mark, or a pytest.ini without the strategy; the root AGENTS.md
  xdist worker contract gains the rule.
- Suite wall, this host, `-n 8` (runs sequential, same machine):

| metric | 0.17.0 (fac347d) | 0.18.0 (v0.18.0, 3637810) |
|---|---|---|
| suite wall | 210.36s (1503 passed, 7 skipped) | 216.41s (the cut's own gate-2; 1534 passed, 6 skipped) |
| CI test-job wall (3.12 / 3.13 rows) | 247s / 284s (run 43090010431) | 373s / 248s (run 43127286924) |

  The local suite delta (+6-28s across green runs) is the 30 new
  e2e tests (the roster forced-rebuild matrix and the chat-send
  defaults matrix spawn a stdio MCP server per call); no existing
  test grew — the targets are in-process CLI faces over one
  read-only script path, and the loadgroup change leaves the
  unmarked tests' per-test scheduling untouched. Per-row CI wall
  (checkout + install + suite) swings ±~100s with runner
  assignment — the pre-cut run (43121558741) read 264s / 301s; the
  released-tree run is the standing one.

### Notes
- The T1 mask tests seed fake LLM keys; their spellings had to
  satisfy the repo's own secret scanners (the history-scrub guard's
  `sk-` pattern and gitleaks' generic-api-key rule), so they are
  short, dash-broken values that match neither. The scrub guard
  doing its job on the first (over-eager) spellings is exactly the
  point — no real credential ever entered the tree.

## 0.17.0

The readable release.
0.16 restructured the suite's cost; 0.17 makes the tool answer in
English. Every workhorse command (ticket, chat, backlog, gates, agent,
gh, release) now prints the answer in human form by default — a
ticket table or card, a chat transcript, one-line confirmations, gate
summaries, release state — while `--json` prints the raw MCP envelope
byte-for-byte the way 0.16.0 printed it: the machine contract
(scripts, tests, anything parsing the CLI) does not move, and exit
codes are unchanged. The CLI also learned to see the whole company
and to name its empty states.

### The two faces: human by default, `--json` is the machine contract (T1)
- `hive ticket list` renders ID / TYPE / PRI / STATUS / TITLE /
  ASSIGNEE ordered backlog→sprint-current→in-progress→in-review→done,
  with a count line; `hive ticket get` renders the card (header,
  description, comments as `author (ts): body`, the verification
  block when present).
- `hive chat read` prints the transcript (`HH:MM author: message`);
  `chat send`, `ticket create/move/claim/comment/approve` print
  one-line confirmations (the ticket id, the new status, the
  channel).
- `hive backlog prioritize` renders the top-N table (priority, type,
  title, id); an empty backlog says so.
- `hive gates check` renders the per-gate lines; `gates run` the
  per-ticket summary; `gates status` keeps its stuck-ticket table and
  gains `--json`.
- `hive agent spawn-ceo` / `run-vp` confirm in one line (agent id +
  the spawns log to tail); the stream-json conversation stays behind
  `--json`. A failed spawn surfaces the harness's real error (it is
  reported on the envelope's stdout, not the spawn plumbing).
- `hive gh status` renders the gh/auth/remote/branch lines; `gh pr
  open/merge` render the PR number/URL confirmations.
- `hive release status/list` render the release state and the
  version/publish/sprint table; a missing record is named.
- `--json` on every one of them is the 0.16.0 output
  byte-for-byte; a non-empty envelope stderr is always shown.
  `hive tool` / `hive tools` keep raw JSON — that IS their face.

### The company-wide ticket list (T2)
- The department argument is optional everywhere: `hive ticket list`
  (and the `ticket_list` MCP tool) with no department lists every
  department, grouped — one table per department under `<dept>:`
  headers, per-dept counts, and the company total. `--json` is the
  company-wide doc `{"departments": {...}, "total": N, "mode": ...}`.
- The "no tickets" vs "doesn't exist" distinction: a named department
  that does not exist is an error (`Department '<dept>' not found`,
  exit 1); an existing department with zero tickets is a count-0
  listing (exit 0). A department "exists" when it has a runtime dir
  or an org-chart entry — a fresh company lists its org-chart depts.

### `hive status` names its empty states (T3)
- A fresh company (just `hive init`'d) shows its org-chart
  departments with zero counts, `Sprint: none — no sprint state yet
  (run 'hive start' ...)`, `Daemons: none running — run 'hive start'
  to start the company`, and — when the LLM is unconfigured — the
  canonical hint (the same one doctor prints).
- An idle sprint reads as a state: `idle — no work to pick up (runner
  polls the backlog — file a ticket or run 'hive start')`.
- A named department that does not exist is an error (exit 1, named);
  `--json` carries the error. A never-init'd directory says `No
  departments found — run 'hive init'`.

### `hive start` preflights the LLM (T3)
- Starting without a configured LLM is valid — the company comes up,
  the runner halts with a clear reason at the first LLM call — but
  the operator now hears it immediately: one warning on stderr
  (`LLM not configured — agents will fail to spawn; ...`) built from
  the canonical message. Exit 0; the daemons still start.

### `hive chat channels` (T3)
- Lists every channel in `.company/chat/` with its message count and
  the timestamp of its last message (the MCP tool reads the channel
  files directly, like `status_get`). An empty board names itself:
  `No channels yet — the first 'hive chat send' creates one.`

### The dashboard sees the fresh company (T3)
- `/api/departments` returns the org-chart union the runtime
  departments/ dirs: a fresh company lists its departments on the
  board with zero counts instead of "No departments found".
- The app footer shows the running hive version (from the tree's
  VERSION file, served on `/api/status`).

### The docs move with the faces (T1/T2/T3)
- `share/docs/CLI.md`: every affected subcommand documents `--json`;
  the two-faces contract (byte-identical to 0.16.0, exit codes
  unchanged, stderr passthrough) is stated once; the company-wide
  list, the empty states, the start preflight, and `chat channels`
  are documented where the commands live. Pinned by a docs test.
- `share/docs/MCP-TOOLS.md` regenerated (`ticket_list`'s optional
  `dept` and its company-wide contract, the new `chat_channels`
  tool); the drift test pins it.

### The release cut (T4)
- Every version-bearing surface moves together (VERSION, pyproject,
  CHANGELOG, README + share/docs/README), validated by gate 0 of
  `packaging/cut-release.sh` before the cut runs.

# Changelog

## 0.16.0

The price release.
0.12 built the trust surface, 0.13 wired its consumers, 0.14 made
the release reproducible, and 0.15 proved the move — and the
invariants those releases bought now cost wall clock on every run:
a 103-second hammer that is 38.7% of the suite wall, an install
cluster that pays a fresh venv and a lock install per test, and a CI
matrix that installs a toolchain that installs nothing. 0.16
restructures the cost without weakening a single invariant: the pin
keeps its N and its poll (T1), the fresh-install path keeps its
dedicated test (T2), the scheduled row keeps its job (T3). No
breaking changes; no new commands, roles, stages, or providers.

### The hammer's 103 seconds — the pin's cost, not its doubt (T1)
- The hammer's parent now reaps the prior runner on every window
  iteration (the real supervisor model — company-health does the
  same): a reaped prior frees the pid file in ~0.14s per takeover,
  not 5.3s (zombie). The pin (N=20, polling for empty pid reads) is
  unchanged.
- The production death check is 0.1s x 10 (a 1s cap; was 0.5s x 10):
  a reaping supervisor sees the takeover commit in ~0.2s; the
  unreaped worst case pays the full cap once, per takeover, and is
  pinned by its dedicated test.
- The takeover now waits (bounded) for the runner lock to actually
  clear: the killed prior's orphaned children (the state-layer
  python) hold the inherited lock fd for ~100ms — the 0.15 5s cap
  masked it (the flock always landed seconds after the TERM, when
  the orphans were gone; observed 20% of forced takeovers refused).
  A third LIVE runner still wins the refusal; the non-forced path is
  untouched.
- The static tick guard pins the 0.1s granularity (red while a
  coarser literal exists).
- Result: the hammer is 14.45s (was 103.03s) — 7.2% of the suite
  wall, not 38.7%.

### The install cluster — the venv is built once per session (T2)
- Session-scoped `_installed_base`: one staged tree (the same surface
  the per-test versions staged) plus ONE real install.sh run that
  builds the venv from the lock. The five venv-tax tests copy the
  base per test (the S5 contract) and run install.sh against the
  copy — the venv is REUSED (the 0.15 T1 upgrade branch: idempotent
  lock re-install, exact-set prune); the user-config assertions run
  under a fresh scratch HOME exactly as before.
- The dirty-tree copy-to test seeds its destination venv from the
  base (the same upgrade branch); the tree prune it asserts is
  venv-independent, so the assertions are unchanged.
- The fresh-install branch (venv CREATION + the first lock install)
  keeps its dedicated per-session test (the TestBuildLockVerify
  pattern): one full `--copy-to` install on a clean staged tree
  asserts the creation branch actually runs and the venv imports the
  locked set.
- The receipt tests share a session cut-tree base (one full-repo tar
  + git init + base commit per session); each test copies it, resets
  it, and gets a fresh bare origin.
- Result: the venv-tax tests are ~3s each (was 35.54–45.92s); the
  dirty-tree test is 5.4s (was 45.14s); the receipt class is
  16.4s/31.0s (was 26–37s).

### The wheel-only contract — in every push, on the schedule, pinned (T3)
- `TestLockWheelOnly`: the lock's full closure resolves with
  `--only-binary :all:` (session-scoped, one download round-trip) —
  the wheel-only property that makes `install.sh` install without
  compiling anything (no gcc, no Rust on a stranger's machine) is
  now pinned; red on any line that needs source, naming it.
- The push rows install the lock with `--only-binary :all:` — the
  contract lives in every push: a lock line that loses wheel
  coverage fails the row, naming the line. The inert rustup step
  (a `--default-toolchain none` install ships NO compiler) is gone
  from both workflows; a static guard pins its absence and the
  contract's presence on every row (the snowball pattern).
- The push rows share a pip wheel cache keyed on sha256 of the lock
  + interpreter + OS — the install step drops from 24s/12s to
  8s/14s (run 42934413101 vs 42983142965).
- The scheduled row is the explicit exception: NO cache (its job is
  the weeks-later PyPI re-resolution — a hit would turn "does PyPI
  still serve it" into "does our cache still hold it") — and the
  same `--only-binary` contract, so a wheel that rots by then
  surfaces there, on the schedule, naming the line. PACKAGING.md
  records the split.

### The price, recorded (T4)
| metric | 0.15 | 0.16 |
|---|---|---|
| suite wall, -n 8, this box (Python 3.13.18, 88 cores) | 266.72-276.71s (3 runs) | 201.72-218.95s (3 green back-to-back runs) |
| suite CPU total (user+sys, /usr/bin/time) | 864.1s | 851.3-865.4s |
| hammer (test_runner_singleton, N=20) | 103.03s (38.7% of suite wall) | 14.45s (~7% of suite wall) |
| venv-tax tests (install cluster, per test) | 35.54-45.92s | ~3.1-3.3s (user-config), 5.4s (dirty-tree) |
| receipt tests (full cut per test) | 26-37s | 16.4s / 31.0s |
| CI test-job wall (run 42934413101 / 42983142965) | 307s (3.12) / 264s (3.13) | 204s (3.12) / 260s (3.13) |
| CI install step (the lock install) | 24s / 12s | 8s / 14s (shared wheel cache, keyed on the lock sha256) |

The 3.13 CI row is suite-bound (its suite wall barely moves; the
suite itself is T4 of a later release). The 3.12 row's ~100s drop is
the rustup step going (an inert toolchain install) plus the cache
hitting the install step. One local -n 8 run during the measurement
window hit a load flake in the heavyweight LLM-outage recovery test
(isolated rerun green; both CI rows green; the non-forced runner
start path is untouched by T1) — recorded for the record, not a
regression of this change set.


## 0.15.0

The upgrade release.
The trust surface so far verified a release against ITSELF; 0.15
verifies a release against its PREDECESSOR: install-on-install leaves
EXACTLY what the new release defines (T1), a marked tree can no longer
hold the runtime state its teardown would delete (T2), the race class
0.14's CI flake named is audited, hardened, and pinned (T3), and the
EOL interpreter row retires with the version-sensitive surface that
exists only for it (T4). The cut itself is the first to verify the
live upgrade (T5). Feature-light: no new commands, roles, stages, or
providers. One breaking change: Python 3.10 support ends with this
release (EOL — T4).

### The upgrade path — install-on-install leaves exactly what the new release defines (T1)
- `install.sh` prunes the installed tree: a top-level install-root
  entry outside the new entry set (other than `venv`/`.hive-install`)
  is residue of a superseded release and is removed, each entry
  named.
- A shared module (`lib/hive_cli/commands/venv_prune.py`) reduces the
  installed venv to the exact released environment (the lock closure
  + the venv tooling `pip`/`setuptools`/`wheel`/`uv`): a name outside
  the set is uninstalled, each removal named. `install.sh` runs it at
  install/upgrade time; `hive doctor`'s "venv exact set" check
  verifies the result and names any drift with the fix.
- `verify-release.sh --from PREV-TAG [install-dir]` verifies a
  completed upgrade: the tree is exactly the new entry set, the
  required set is present, the venv is in the new exact set, and the
  previous release's receipt is valid (the chain holds).
- PACKAGING.md documents the upgrade story (the Upgrade section).

### Teardown safety — a marked tree never holds runtime state (T2)
- Staging (install or upgrade) into a directory that holds runtime
  state (`.company/`, `.worktrees/`) is refused before anything is
  touched, the conflicting entry named.
- Uninstall of a marked tree removes the whole tree (mirroring the
  full stage); the symlink+venv-only path stays reserved for
  unmarked (pre-0.14) trees.
- Follow-up (caught by the scheduled row's first dispatch): the
  online installer delegated to `install.sh` via `sh` — on dash
  systems a syntax error before the refusal could run. It now
  delegates with bash; the contract is pinned statically.

### Commit points — the race class is audited, hardened, and pinned (T3)
- The runner's singleton moves its mutex to a dedicated lock file
  (flock needs a stable inode); the pid, heartbeat, and LLM-outage
  counter commit atomically (tmp + rename): an observer reads the old
  value or the new value — never empty, never partial. The halt
  marker's atomic commit (post-0.14) ships alongside.
- The audit (`docs/commit-point-audit.md`) classifies every state
  write in lib/tools + lib/core + the harness: cross-process
  observed writes commit at a single commit point; self-contained
  sites stay, each with a one-line justification.
- The static guard (the lib-side sibling of the xdist worker
  contract): a new direct write to a state path fails the suite;
  registered sites carry their justification. The forced-takeover
  test hammers the poll window (N=20) — no empty pid reads.

### Python 3.10 is EOL (T4)
- The floor is 3.11: `pyproject.toml` requires `>=3.11`, the tomli
  fallback and the conditional lock line retire — plain `tomllib`
  everywhere (the version-sensitive surface existed only for 3.10,
  which is EOL).
- The CI matrix retires the 3.10 row: one version (3.12) carries the
  full suite, the supported versions (3.11, 3.13) carry the
  version-sensitive subset.
- The scheduled reproducibility row is repurposed from the 3.10
  subset to the full 3.13 run.

### The cut verifies the live upgrade (T5)
- `cut-release.sh --upgrade-from PREV-TAG`: gate 8 scratch-installs
  the published previous release, installs the staged tree on top,
  doctor's the result, and asserts the atomic commit points in the
  delivered tree. The receipt gains the upgrade line (from-tag,
  verified) — the first receipt to carry one.
- `verify-release.sh --from` verifies that line: a receipt claiming a
  different predecessor is a named chain mismatch.

## 0.14.0

The reproducibility release.
0.12 built the trust surface and 0.13 wired its consumers; 0.14
makes the surface REPRODUCIBLE and the confidence RECURRING: the
install's Python environment stops being "whatever PyPI serves today"
(T1), the one-off 3.13 confidence datum becomes a scheduled row (T2),
teardown mirrors setup so an uninstall removes what an install
created (T3), the workspace reclaims its own scratch instead of
compounding it (T4), and the docs stop telling operators to place LLM
config where doctor's D1 check says it was retired (T5).

### The venv lock — the Python environment is a reproducible artifact (T1, bfa94ac)
- `packaging/requirements.lock.txt` pins EXACT versions for every
  name in `requirements.txt` (marker-aware: the `tomli` line is
  conditional on `python_version < "3.11"`). requirements.txt keeps
  its floors; the lock is the released environment.
- `install.sh` installs FROM the lock when it ships with the release
  (pre-0.14 releases carry no lock and fall back to the floor pins
  with a note).
- `build-package.sh` self-verifies the lock BEFORE staging anything
  — the producer looks at its own output: the lock exists, every
  name in requirements.txt has a pin that satisfies the floor, and a
  venv built from the lock imports the runtime set (a miss fails the
  build, naming the entry). `HIVE_LOCK_VENV` lets the test suite
  point at a session-built venv instead of paying the network
  install per build.
- `hive doctor` gains the "venv lock" check: it cross-checks the
  installed venv's resolved versions against the installed lock — a
  drifted package is named (locked version vs installed version); an
  install without a lock (pre-0.14) reports the resolved versions
  WITHOUT a FAIL.
- The refresh path: `bash packaging/refresh-venv-lock.sh` re-resolves
  requirements.txt into a throwaway venv and writes the exact pins
  back. The lock joins the required-set constant (it ships in every
  install and every tarball).
- No new cut gate — the receipt schema and the gate-0..7 keys stay
  untouched.
- While T1 was landing, a latent xdist worker leak was fixed: an
  in-process `doctor.handle(json=True)` left `_RUN["json"]` set and
  suppressed the pretty output of later in-process check calls on the
  same worker; `handle()` now resets `_RUN` in a `finally`.

### The scheduled 3.13 full row (T2, 316a54f, b8246b2)
- `.github/workflows/scheduled.yml` — a `full-313` job running the
  FULL suite on Python 3.13, triggered weekly (cron) and manually
  (`gh workflow run scheduled.yml`). The push matrix (ci.yml) is
  unchanged.
- First dispatch run (recorded in PACKAGING.md): run 42566985025,
  2026-11-09, 1,346 passed / 3 skipped in 272.78s. Skip list
  re-audited against the 0.13 skip-reason contract — every skip
  names its condition.

### Teardown mirrors setup (T3, d4d90f7)
- `install.sh --copy-to` writes the `.hive-install` marker (version,
  timestamp, mode) into the install root.
- `uninstall.sh` removes the WHOLE install tree only on that marker:
  no tree, no venv, no symlink — a scratch install is fully
  re-created by a fresh install, and the stale launcher symlink that
  pointed at nothing after a manual tree removal is history.
- In-place installs (git checkout / the operator's own tree) write
  no marker and keep their tree, as before — the discriminator is
  the marker, not `.git`. `--keep-tree` is the escape hatch
  (symlink + venv only).
- The marker is neutral to the doctor installed-set check (it is a
  root file written at install time — outside the entry set, never in
  the tarball, never in the manifest); a regression test pins it.

### Scratch hygiene — the workspace reclaims its own scratch (T4, 776608e)
- `packaging/clean-scratch.sh`: the operator tool. Basetemp
  generations (pytest-of-<user>/pytest-<N>, xdist workers' dirs ride
  with their generation) kept back to the newest 3; mktemp-shaped
  litter (tmp.*/hive-*/pip-unpack-* direct children of $TMPDIR)
  reaped past a min age (3 days default), open-file gated (lsof,
  best-effort). --dry-run names the targets first; fresh/live paths
  are never touched, and the workspace root is out of scope by
  construction even when $TMPDIR sits inside it.
- The suite reaps itself: a session hook in tests/conftest.py
  (`_basetemp_retention`) applies the same basetemp policy per
  session (fresh-mtime gated, so a running suite — including its
  concurrent xdist workers — is never touched).
- `tests/test_xdist_contract.py::TestScratchSnowballGuard` pins the
  module-level mktemp call sites in the suite: a new per-test mktemp
  call fails the guard until deliberately pinned — the snowball is
  bounded both ways (the reaper reaps the old, the guard stops the
  new).

### One canonical LLM layer — the docs and doctor agree (T5, 82a4bb4)
- The root AGENTS.md Configuration section and
  `share/docs/LLM-PROVIDERS.md` were rewritten on one line:
  config.toml [llm] is canonical (every resolver reads it); HIVE_*
  env vars are the one-shot layer (shell export); env.sh is for
  shell-level daemon env only (PATH, GITHUB_TOKEN) and must NOT
  carry LLM settings (D1's retired vars — doctor warns on them).
- `hive doctor`'s unconfigured-provider diagnostic now
  cross-references D1: when the provider is unconfigured AND env.sh
  still exports retired LLM vars, the diagnostic says so in one line
  ("env.sh still exports … (retired) — move them to [llm] in
  config.toml") — the actual remedy for the actual situation.
- A static guard (TestLlmDocEnvShConsistency) reads D1's retired-var
  list from doctor.py and fails on any doc (AGENTS.md,
  share/docs/**) that directs a retired var into env.sh — including
  code blocks introduced by an env.sh line.

### The 0.14.0 cut (T6)
- The cut's gate-2 default (the S5 primary loop) verifies T1's lock
  self-verify and T3's marker/uninstall parity against the live
  release; the scheduled 3.13 row (T2) re-runs the full suite on
  3.13 against the cut.

## 0.13.0

The loop-closer release.
0.12 built the trust surface — manifest, entry set, receipt — and
0.13 wires its consumers: the CI matrix that was designed but could
not be pushed (T1), the structural pass 0.12 could only start (T2),
a doctor check that re-verifies the installed set at runtime (T3),
one-command verification of any published release (T4), and 3.13
confidence plus the skip audit (T5). No new features, no new agent
roles, no lifecycle reordering.

### CI matrix landed (T1, 432c2b8, 8480c47)
- The 0.12 S4 matrix — full suite on 3.12, version-sensitive subset
  on 3.10 and 3.13 — is on main and green: three test rows per push.
  The subset rows carry the config-load/tomli-fallback surface, the
  CLI smoke, and an import smoke; the tomli-fallback break was
demonstrated ON CI in a throwaway PR (all three rows red, closed
  unmerged), proving a subset row catches a version-sensitive break
  without the full suite.
- Per-push wall time vs the pre-matrix 2x-full runs: the 3.12 full
  row is 165.5s (1,311 tests) against the old slower row's 210.7s,
  and the two subset rows add ~1m of parallel wall. The figure lives
  in the commit (R3), not in prose.
- Two CI-flake fixes landed as T1 follow-ups: `build-package.sh`
  self-verifies the `MANIFEST.txt` member from its own tarball
  before declaring success (e0a43a0), and the harness G4 sandbox no
  longer flags the spawn's own stream file when a telemetry line
  lands inside a command's snapshot window (4fcd11b).

### The structural pass, finished (T2, 287ae69)
- `test_sprint_lifecycle` now copies `lib/` from a session-scoped
  HARDLINK base (once per xdist worker); the one file a test
  rewrites in place (`lib/core/agent-spawn.sh`) comes from a
  write-target denylist as a real copy, so a write can never go
  through a shared inode into the base. The denylist is pinned by a
  static contract test.
- `test_commands`' doctor-probe family builds its venv ONCE per
  worker (session base) instead of per test; probes are read-only
  against the install root. Pinned by a static contract test.
- No test's intent changed: setup cost moved, assertions did not.

### Installed-set integrity (T3, c905915)
- `hive doctor` (and `--json`) re-verifies the INSTALLED set at
  runtime — not just at install time: the installed `MANIFEST.txt`
  (S1's bill of materials) is present, every entry of the required
  set is present at the install root, and no runtime-state entry is
  present (the state-free rule of gate 4). Failures name each
  missing or foreign entry. The required set and the state-free rule
  come from ONE constant shared by cut gate 4, `install.sh`'s
  post-stage check, and this check — "one list, four consumers".

### Receipt consumer (T4, 5a5bc58)
- `packaging/verify-release.sh <tag>` verifies a PUBLISHED release
  in one command: it fetches the receipt, tarball, and sha256
  sidecar from the release; checks the sidecar against the
  downloaded tarball; the receipt's tarball sha256 line; the
  receipt's manifest digest against the tarball's `MANIFEST.txt`
  member; and that the receipt parses as `key: value` lines carrying
  every required key. Exit 0/1, naming each failure, and it prints
  the commit + release URL for the human. It stays in `packaging/`
  (not a CLI command — 0.13 is feature-light).

### 3.13 confidence + skip audit (T5, ceeede2)
- One full-suite run on Python 3.13 on the 0.13 reference box:
  1334 passed, 6 skipped (-n 8). A confidence datum, not a CI row —
  the subset row stays the 3.13 CI design.
- Skip-reason contract guard: every `pytest.skip` /
  `@pytest.mark.skip` / `@pytest.mark.skipif` in `tests/` must carry
  a static, non-trivial reason naming the condition, so the skip
  list stays auditable from the tree.
- The audit found and fixed one silent hole: the "every shipped JSON
  is valid" test had been auto-skipping every run (its parametrize
  globbed the pre-flat-repo nested layout — zero matches). It now
  parametrizes over the shipped set from `packaging/entries.list`.

### The cut's gate-2 default (T6, efbb807)
- `cut-release.sh`'s gate-2 suite default is now the S5 primary loop
  (`python -m pytest tests/ -q -n 8`); the 0.12 cut had to pass the
  flag explicitly or pay ~11 min of serial gate-2 wall. Serial stays
  available via `--suite`.

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
