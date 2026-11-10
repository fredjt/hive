# Commit-point audit — state writes in lib/ (T3, 0.15)

R3: the table lives in the tree. The audit runs against the tree as it
stands after the hardening commits (pid/heartbeat/counter, the file's
siblings); every remaining direct write is registered below with a
one-line justification — the same registered-site pattern the scratch
snowball guard uses. A new direct write to a state path is a FAIL in
`tests/test_xdist_contract.py::TestLibCommitPointGuard` until it is
registered here and converted (or justified).

**The class.** A state write is *cross-process-observed* when another
process (round runner, health, dashboard, MCP, another agent, the test
suite) can read the file while the write is in flight. The dangerous
idiom is TRUNCATE-THEN-FILL (`cat >`, `echo >`, `printf >`,
`exec N>`): the reader lands between create-truncate and fill and sees
an empty or partial file. The named equivalent is **tmp + rename**
(write a tmp sibling, `mv -f` — the rename is THE commit point; same
filesystem). The second class is APPEND (`>>`, O_APPEND): a line-sized
write lands whole; the file is never truncated. Appends are the log
class and are retained — they do not create the empty window.

## Class A — converted to tmp + rename (0.15)

| Site | File | Why it was in the class |
|---|---|---|
| `rounds/runner.pid` | lib/tools/sprint-lifecycle.sh | the LIVE member of the flake 42570231285 class: `exec 9>` truncated the pidfile, the pid landed only after the flock; the mutex now sits on a dedicated `runner.lock` (flock needs a stable inode) and the pid commits via `atomic_commit` |
| `rounds/runner-heartbeat` | lib/tools/sprint-lifecycle.sh | written every loop iteration; ops/health read the JSON — a partial line is a false crash signal |
| `status/llm-outage-count` | lib/tools/sprint-lifecycle.sh | the runner's own read-modify-write; a partial count line would miscount the outage threshold |
| `rounds/halt` | lib/tools/sprint-lifecycle.sh | the CI flake 42570231285 itself (0.14 post-cut, ships in 0.15): `cat > halt` exposed the empty marker to the existence-poll |
| `logs/active/<spawn>.json` | lib/core/agent-spawn.sh | company-health's stale-spawn cleanup reads these cross-process; partial JSON mid-write |
| reports (`$DEST_DIR/$FILENAME`) | lib/tools/report-write.sh | reports are read cross-process (other agents, dashboard); a truncated report is a lie |
| `components/*/test-results.*` | lib/tools/test-results.sh | verification and the dashboard read the results while the writer fills it |
| `research/ongoing/*-findings.md` (fresh write) | lib/tools/research-findings.sh | other agents read findings; the append branch keeps O_APPEND (class B) |
| the release verdict (`$VERIFY_LOG`) | lib/tools/release-verdict-write.sh | the round runner (a different process) parses the first `VERDICT:` line; a partial verdict misroutes the sprint |
| `$COMPANY_DIR/releases/<ver>-gates.json` | lib/tools/release-gates.sh | release-ic and the dashboard read the gates report |

## Class B — retained: the named equivalent / single commit point

Direct truncate writes that are NOT cross-process-observed — each keeps
a single commit point by construction (one-shot, or the reader only ever
sees the file after it is complete). Registered with their
justification; the guard exempts exactly these.

| Site | File | Justification |
|---|---|---|
| `brief.md` (fresh) | lib/tools/init-brief.sh | init-time one-shot: no agent, dashboard, or runner exists before init completes, so no concurrent reader |
| `chat/<channel>.md` header (create-only) | lib/tools/chat-send.sh | guarded by `[ ! -f ]`; the content is the identical one-line header, so a create race cannot expose anything but the complete line |
| channel files (create-only) | lib/tools/init-chat-channels.sh | init-time one-shot; guarded skip if the channel exists |
| `finance/.../cash-position.json` | lib/tools/init-finance-scaffold.sh | init-time one-shot scaffold write of a PROJECT file (finance/ under the project root, not `.company/`); no reader exists yet |
| `legal/.../LICENSE.proposed` | lib/tools/init-legal-scaffold.sh | init-time one-shot scaffold write of a PROJECT file (legal/ under the project root, not `.company/`); no reader exists yet |
| onboarding README/CHARTER/ONBOARD | lib/tools/onboard-generate.sh | onboarding runs in the init phase; no concurrent readers |
| `$PROJECT_ROOT/README.md` (if absent) | lib/core/init.sh | init-time one-shot, and it is a project file, not `.company/` state |
| `logs/ceo-initial-build.log` | lib/core/orchestrator.sh | one-shot startup log line written once at orchestrator boot; readers tail afterwards |
| heartbeat-dedup stamps (`_last_file`, `last-disk-crit`, `last-disk-warn`, `_wt_last_file`) | lib/tools/company-health.sh | self-contained: the health daemon is the sole reader of its own dedup stamps; a partial stamp only re-logs a line early |
| run stamps (`logs/vp-last-run.<dept>` + `.reason`) | lib/tools/vp-idle-check.sh | self-contained: the idle-check is the sole reader of its own run stamps (its dedup logic reads the ts it writes); one-line content |
| `rounds/runner.lock` / `gate-runs/<dept>-<tid>.lock` | lib/tools/sprint-lifecycle.sh, verification-gates.sh | DEDICATED lock files: `exec N>` creates the file the flock needs (a stable inode); the content is never read by anyone — flock only |
| `releases/<ver>-test.log` (header line) | lib/tools/release-test-suite.sh | per-version log: the first write CREATES this version's log and a suite re-run deliberately resets it (new-run semantics); single header line, appends resume |
| log rotation (`: > "$log_file"`) | lib/tools/log-rotate.sh | the named equivalent: in-place truncate to PRESERVE the inode so the daemon's open fd stays valid (copy-gzip the backup first); the observable state is exactly 'empty, then appends resume' |
| `/tmp/...` scratch (`hive-changelog.*`, `hive-publish-notes.*`, `gh-*.preflight`, `*.wt.$$`) | lib/tools/release-publish*.sh, gh-pr-*.sh, gh-push-branch.sh | not state: mktemp scratch outside `.company/`, single consumer, removed by the consumer |

## Class C — appended (O_APPEND): the log class, retained

`>>` never truncates; a line-sized write lands whole. Every append site
below is a log or a shared human-readable feed; the reader tolerates a
still-growing tail.

| Feed | Writers |
|---|---|
| `logs/health.log` | company-health.sh (all event lines) |
| `logs/sprint-lifecycle.log` | sprint-lifecycle.sh, sprint-state.sh, company-start/health (daemon stdout) |
| `logs/roster-rebuild.log`, `status-rebuild.log`, `org-chart-rebuild.log`, `worktree-merger.log`, `dashboard-generator.log`, `mcp-server.log`, `company-board.log`, `website.log`, `verification-gates.log` | company-start.sh daemon loops + health restarts (stdout capture) |
| `logs/spawns.log`, `logs/cycles.jsonl` | lib/core/agent-spawn.sh (one line per spawn start/end) |
| `chat/incidents.md`, `chat/<channel>.md` (messages) | sprint-lifecycle.sh (halt incident), chat-send.sh, ticket-route-expedited.sh |
| `logs/cycle-log.md` | ticket-route-expedited.sh |
| `research/ongoing/*-findings.md` (append) | lib/tools/research-findings.sh `--append` |
| `logs/llm-usage.log` | lib/core/agent-harness.py (`open(..., "a")`) |
| `releases/<ver>-test.log` | release-test-suite.sh (test output) |
| `brief.md` (append) | lib/tools/init-brief.sh |

## Out of scope (not state, or a different contract)

- **lib/hive_state** (the `.company/` state layer): every mutation is
  per-file flock + atomic tmp + `os.replace` + schema validation (G1) —
  a single commit point BY CONSTRUCTION; `sprint-state.sh` mutates only
  through it (`ss__atomic_jq`).
- **lib/hive_mcp** (the MCP server): writes land through the state
  layer / the tool scripts above; the server itself holds no direct
  state writes.
- **agent-harness.py** stream-json events: appended per-event to the
  spawn's own stream file (`logs/streams/<spawn>.jsonl`) — the class C
  feed the dashboard replays.
- **doctor.json** (the doctor's own report): written by `hive doctor`
  through the state layer's atomic write; single consumer (the
  dashboard).
- **`.worktrees/`, `dist/`, the install venv**: not `.company/` state —
  git-managed, build-managed, and pip-managed trees with their own
  (single-process) writers.

## Guard

`tests/test_xdist_contract.py::TestLibCommitPointGuard` scans
`lib/tools/*.sh` + `lib/core/*.sh` for direct TRUNCATE writes whose
target is a state path (`$COMPANY_DIR/...` or a variable assigned from
it) and are not tmp+rename targets (`.tmp.$$`), and fails on any site
that is not in the Class B registered set (matched by file + line
content). A planted `cat > "$COMPANY_DIR/..."` in a copy of a script
fails the guard — the audit is pinned, not prose.