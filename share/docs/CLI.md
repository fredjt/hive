# Hive CLI Reference

Bare `hive` (no args) starts the MCP server + dashboard and opens a browser.

## Project lifecycle

```
hive init <project-dir> [--brief "..." | --brief-file FILE] [--auto] [--name NAME]
hive start [project-dir]
hive stop  [project-dir]
hive status [project-dir] [--dept DEPT] [--json]
hive dashboard [project-dir] [--port N] [--no-open]
```

`hive init` creates `.company/`, adds it to `.gitignore`, writes the manifest
and brief, initializes chat channels and meeting schedule.

`hive start` spawns all daemons — VP loops, sprint-fill, sprint-archive,
sprint-lifecycle, roster-rebuild, status-rebuild, verification-gates,
verification-runner, worktree-merger, dashboard. PIDs recorded in
`.company/processes.json`.
Repo containment: a hive company must be the top level of its own git
repo. `hive init` and `hive start` refuse to operate in a directory
nested inside another repository (worktrees would branch off the
outer repo and git operations would act on the outer project) - set
`HIVE_ALLOW_NESTED_REPO=1` to override deliberately. `hive start`
also refuses a `.company/` without the init artifacts (a missing
`manifest.json` means the directory was never initialized).


`hive start` preflights the LLM: if no provider is configured it warns on
stderr (`LLM not configured — agents will fail to spawn; ...`) with the
canonical hint (the `[llm]` config file and the one-shot env vars), but
still starts the company — the runner halts with a clear reason at the
first LLM call.

`hive status` reads the derived `status.json` files and prints per-department
health, ticket counts, velocity, and stuck-ticket count. Its empty states
are named (0.17):

* a fresh company lists its org-chart departments with zero counts, then
  `Sprint: none — no sprint state yet (run 'hive start' ...)` and
  `Daemons: none running — run 'hive start' to start the company`; if the
  LLM is unconfigured, the canonical hint is appended (the same one doctor
  prints);
* an idle sprint reads as a state: `idle — no work to pick up (runner polls
  the backlog — file a ticket or run 'hive start')`;
* a named department that does not exist is an error (`Department '<dept>'
  not found`, exit 1; `--json` carries the error envelope); a directory
  that was never `hive init`'d says `No departments found — run 'hive init'`.

## Tickets

```
hive ticket create <dept> <type> <priority> <title> <description> [--created-by NAME] [--source SRC] [--estimate EST] [--json]
hive ticket get <dept> <ticket-id> [--json]
hive ticket list [dept] [status] [--json]
hive ticket move <dept> <ticket-id> <new-status> <comment> <author> [reason] [--force] [--json]
hive ticket claim <dept> <ticket-id> <role> <agent-id> <agent-name> [--worktree-path PATH] [--json]
hive ticket comment <dept> <ticket-id> <author> <comment> [--json]
hive ticket approve <dept> <ticket-id> <verifier> <rationale> [--json]
```

**Two faces (0.17).** Every workhorse command in this reference has a
*human* default and a *machine* face: the default prints the answer in
English — a ticket table or card, a chat transcript, one-line
confirmations, gate summaries, release state — and `--json` prints the
raw MCP envelope exactly as 0.16.0 printed it. The machine contract
(scripts, tests, anything parsing the CLI) does not move; exit codes are
unchanged (0 iff the MCP call ok'd), and a non-empty envelope stderr is
always shown. Affected: `ticket *`, `chat *`, `backlog prioritize`,
`gates *`, `agent *`, `gh status` / `gh pr *`, `release status` / `list`.
`hive tool` / `hive tools` keep raw JSON — that IS their correct face.

The `ticket list` table: ID / TYPE / PRI / STATUS / TITLE (truncated) /
ASSIGNEE, ordered `backlog → sprint-current → in-progress → in-review →
done`, with a count line. `dept` is optional (0.17): omitted, the whole company is listed, grouped — one table per department under `<dept>:` headers, per-dept count lines, and the company total; existing-but-empty departments stay visible (count 0). A named department that does not exist is an error (`Department '<dept>' not found`, exit 1); an existing one with zero tickets is a count-0 listing (exit 0) — the "no tickets" vs "doesn't exist" distinction. `--json` is the per-dept doc or the company-wide doc `{"departments": {...}, "total": N, "mode": ...}`. `ticket get` prints the card: header line
(status, type, priority, assignee), the description, the comments as
`author (timestamp): body`, and a verification block (gates_passed /
verified_by + rationale) when present.

`type`: `bug | feature | task | research | infrastructure | documentation`
`priority`: `LOW | MEDIUM | HIGH | CRITICAL`
`status`: `backlog | sprint-current | in-progress | in-review | done | archive`
`reason` (for `done`): `implemented | rejected | done`

Moving to `done` is gated — see `ARCHITECTURE.md`. The IC must call
`ticket approve` first with a rationale ≥ 30 chars.

## Roster

```
hive roster [dept] [--active] [--json]
```

Who is working on what: the derived roster (`.company/roster.json` —
the in-progress tickets' assignees, the VP logs, and the worktrees;
rebuilt by the roster-rebuild daemon every 60s while the company runs,
and rebuilt again before every read). The command rides the same
`roster_get` path the MCP tool uses — the script and the wire contract
do not move; this is the terminal face of the dashboard's Agents panel.

The default is a NAME / ROLE / DEPT / STATUS / TICKET / LAST ACTIVE
table — active rows first, then claimed (a paper assignment with no
live process), then inactive — ending in the count line (`N agent(s)
— M active`). On a never-run workspace the table IS the state: the
inactive VPs and `0 active` say the company is not running. `--active`
and `dept` filter exactly as the MCP tool does. `--json` is the roster
document's `.agents` array exactly as `roster_get` returns it — the
CLI and the MCP side share one byte-identical contract.

The empty states are named (the 0.17 family): a directory that was
never `hive init`'d says `No .company here — run 'hive init'` (exit 1);
a workspace whose rebuild failed (roster.json still absent after the
read) says `roster unavailable — the rebuild failed; run 'hive doctor'`
(exit 1); a present roster with zero agents is a count-0 listing
(exit 0); a named department that does not exist is an error (exit 1)
with the known departments.

## Chat

```
hive chat send <channel> <message> [--author NAME] [--dept DEPT] [--json]
hive chat read <channel> [--last N] [--since ISO_TIMESTAMP] [--json]
hive chat channels [--json]                    # every channel: message count + last message time
```

`chat read` prints the transcript by default (`HH:MM author: message`
lines, honoring `--last`/`--since`); `--json` is the envelope — the flag
is no longer a no-op (in 0.16 the default output already WAS the
envelope, so the flag was wired but never consulted). `chat send`
prints `✓ sent to <channel>`. `chat send` takes the channel and the
message — author defaults to the OS user running the command
(`--author` overrides) and department to the channel's own department
(`--dept` overrides: `dept-rd` → `rd`, a named channel → itself). A
send without a message names its usage error (exit 1). `chat channels`
lists every channel in `.company/chat/` with its message count and
last-message time (the empty board says "No channels yet — the first
'hive chat send' creates one").

Channels: `general | engineering | leadership | incidents | dept-<name>`

## Agents

```
hive agent spawn-ceo [project-dir] [--mode FRESH|RESUME] [--json]
hive agent run-vp <dept> [project-dir] [--json]
```

The default prints the one-line confirmation (agent id + the spawns
log to tail); `--json` is the raw result dict — agent-spawn's stdout is
stream-json, and that raw face is the machine contract the dashboard
replays.

(There is no `agent verify` subcommand: verification ICs are spawned by
sprint-lifecycle during the verify stage, not on demand.)

All agent spawns route through `lib/core/agent-spawn.sh`, which auto-injects
the MCP config so the spawned agent can call hive tools.

## Quality gates

```
hive gates run <dept> [project-dir] [--json]
hive gates check <dept> <ticket-id> [--json]
hive gates status [--json]     # gate state for every in-review ticket (debug stuck ones)
```

`gates check` renders the per-gate lines (`✓`/`✗`/`– name — detail`);
`gates run` renders the per-ticket summary line (all-gates-passed /
awaiting IC / bounced with the reason); `gates status` keeps its human
stuck-ticket table and gains `--json`, the structured envelope over the
same data. The human faces are summaries only (0.19): `gates run`
prints the per-ticket lines (or the nothing-to-gate line) — the raw
log lines (ISO timestamps, the `strict=...` line) live in the `--json`
envelope, not on top of the summary; `gates check` on a ticket with no
report yet — the common case, every ticket before its first gate run —
says `No gates report for <id> — the ticket is not in-review yet, or
its gates have not run; run 'hive gates run <dept>'.` (exit 1); the
config booleans in `gates status` render as `true`/`false` (the config
table's spelling).

`gates run` runs deterministic gates against in-review tickets in `dept`. Failed
gates bounce tickets back to in-progress with a comment.

`gates check` reads the gate report for a single ticket.

## Backlog

```
hive backlog prioritize <dept> [--limit N] [--json]
```

Returns the top N backlog tickets — a table (priority, type, title, id)
in the deterministic priority+age rank order (ties broken by age; no
model involved). `--json` is the 0.16 envelope: the ranked ticket ids on
stdout, the machine contract the sprint fill rides.

Two named faces (0.19): a known department with an empty backlog is
exactly one line — `Backlog for <dept> is empty.` (exit 0; the script's
`No backlog for <dept>` note stays in the `--json` envelope, it is not
printed on top of the line); a named department that does not exist is
the roster's error — `ERROR: Department '<dept>' not found
(available: ...)` (exit 1, CLI-side against the org chart).

## Direct MCP tool invocation

```
hive tool <tool-name> [--arg key=value ...]
hive tools [--json]
```

`hive tool` calls a single MCP tool with the given args. Values that look
like JSON (numbers, bools, lists) are parsed; everything else is a string.

`hive tools` lists all ~50 registered tools.

## Config

```
hive config show                  # SECTION / KEY / VALUE / SOURCE table (secrets masked)
hive config show --json           # the merged config as JSON, secrets masked
hive config get <key>             # e.g. mcp.port (a set secret prints '<set — masked>')
hive config set <key> <value>
hive config edit                  # opens ~/.config/hive/config.toml in $EDITOR
hive config path                  # print the effective user-config path and its state
```

Config layers: `etc/config.defaults.toml` (shipped) → `~/.config/hive/config.toml`
(your overrides) → the `HIVE_*` env vars, which win per key over both
file layers.

`show` answers the question the JSON dump couldn't: which layer WON
for each key. The table is SECTION / KEY / VALUE / SOURCE, where
SOURCE is `env <HIVE_*>` (the env value is shown) when the override is
set, else `user config`, else `default`. `show --json` is the merged
document with the secrets masked — the key's shape is unchanged (the
value is replaced, the key stays), and unknown-key warnings stay on
stderr.

Secret keys (api_key, apikey, authorization, token, password, secret —
case-insensitive, at any section depth — the same family the dashboard
strips from `/api/config`) never print their value on any face: `get`
and the table read `<set — masked>` for a set key, and an unset key
prints as unset, not as masked (there is no secret to mask). The
standard is the one LLM-PROVIDERS.md pins — never the key itself, on
every surface. Exit codes: `show` is 0 (unknown keys included);
`get` is 0 when set, 1 with `(unset: <key>)` on stderr when unset.

`set` types the value from the key's type in the defaults schema: ints and
bools go bare, strings are quoted, and a **list-typed** key (e.g.
`tdd.applies_to_agents`) takes a JSON array or a comma-separated value and is
written as a real TOML array — so those keys stop being edit-only:

```bash
hive config set tdd.applies_to_agents '["ic-engineer", "ic-infra"]'
hive config set tdd.applies_to_agents 'ic-engineer, ic-infra'   # same result
```

## LLM endpoint

```
hive llm stats                   # windowed call/token/cost totals (--window 24h|1h|7d|all)
hive llm tail [N]                # tail recent LLM calls
hive llm test                    # one-shot round trip through the configured endpoint
```

The command a human runs when the company misbehaves names its states
(0.19):

* `llm test` unconfigured prints `llm test: <the canonical hint>` —
  one "unconfigured" (the hint the doctor and start preflight share,
  byte-identical; the 0.18.0 call-site prefix that stuttered it is
  gone);
* `stats` / `tail` in a workspace with no usage log yet print
  `No LLM calls recorded yet — the usage log (.company/logs/
  llm-usage.log) appears after the company's first call.` (exit 0 — a
  fresh company that has never called the LLM is a healthy, expected
  state, not an error); `stats --json` prints the same zero document
  an empty log produces (one contract: no calls = the zero document,
  whether or not the file exists);
* a directory with no `.company` at all is the error `not a hive
  project root — no .company here; run 'hive init'` (exit 1; `stats
  --json` prints the error envelope);
* an EXISTING empty log: `tail` prints the visible zero line `No LLM
  calls recorded — the usage log is empty.` (exit 0 — visible, not
  silent), and `stats` prints its zero face (calls: 0, exit 0).

## GitHub

```
hive gh status [--json]           # gh CLI / auth / remote / default branch
hive gh pr open <branch> <ticket> [--json]   # open a PR for a branch
hive gh pr merge <branch> [--method merge|squash|rebase] [--json]  # merge an open PR
```

`gh status` prints the aligned gh/auth/remote/branch lines; `gh pr
open/merge` print the one-line confirmations with the PR number/URL.
`--json` is the 0.16 raw output byte-for-byte.
hive gh push enable               # per-company opt-in: allow pushes / PRs / GitHub releases
hive gh push disable              # revoke the opt-in (pushes become soft no-ops)
```

## Releases

```
hive release start               # drive the release pipeline through enqueue
hive release status [version] [--json]   # print a release record
hive release list [--limit N] [--json]   # list recent releases
hive release publish GO|NO-GO v<ver> <rationale>   # the release-ic commit
hive release abort               # mark a release failed and file an IT ticket
```

`release status` prints the sprint/stage line plus the release fields
(version, publish status, tarball, sha256 when present), and names a
missing record; `release list` prints the version / publish-status /
sprint / ended table. `--json` is the 0.16 raw dict byte-for-byte.

## Operations

```
hive restart [<project-dir>]      # stop daemons, then start them again
hive killall                       # SIGKILL every process the hive start tree owns
```

The `hive round` subtree (round queue: status/next/history/get/enqueue/
advance/promote) was retired in 0.10 — the round queue it served is gone.
`rounds/current-round.json` is now an append-only activity log written by
the sprint lifecycle, and the dashboard Rounds panel reads it directly.

## Diagnostics

```
hive doctor                       # check agent harness, jq, git, node, venv, MCP server, LLM provider
hive doctor --deep                # + probe LLM endpoint reachability (uses network)
hive doctor --json                # machine-readable dump of every check
hive selftest [<project-dir>]     # probe the agent spawn path for real (no LLM call)
hive version
```

`hive doctor` also verifies the **installed set** (T3, 0.13): it reads
the installed `MANIFEST.txt` (the tarball's own bill of materials —
shipped to the install since 0.13) against the shared required-set
constant `packaging/required-entries.list`, and fails naming each
missing required entry or foreign runtime-state entry. A dev install
(from a git checkout, where the build generates the manifest) gets a
WARN, not a FAIL.

…and the **venv lock** (T1, 0.14): the installed venv must resolve to
the versions the release locked (`packaging/requirements.lock.txt` — the
reproducible Python environment). A drifted package is named with the
locked and the installed version; an install without a lock (pre-0.14)
reports the resolved versions without a FAIL. Refresh the lock when
`requirements.txt` changes: `bash packaging/refresh-venv-lock.sh`
(see PACKAGING.md, "The venv lock").
