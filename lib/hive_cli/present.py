"""The CLI's human face (T1, 0.17.0).

Every workhorse command (ticket, chat, backlog, gates, agent, gh,
release) has two faces:

  * default — a human rendering produced by this module;
  * ``--json`` — the raw MCP envelope, byte-identical to what 0.16.0
    printed (``json.dumps(res, indent=2)``). The machine contract
    (scripts, tests, anything parsing the CLI) does not move.

The MCP wire contract is untouched: agents call MCP tools and receive
the same envelopes; this module is the CLI presentation layer only.
``tool``/``tools`` are deliberately excluded — raw JSON is their
correct face.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Callable

__all__ = [
    "envelope",
    "finish",
    "assignee_name",
    "ticket_table",
    "render_ticket_list",
    "render_company_wide",
    "render_ticket_card",
    "render_chat_read",
    "render_chat_channels",
    "render_chat_send",
    "render_ticket_create",
    "render_action",
    "render_backlog",
    "render_gates_check",
    "render_gates_run",
    "render_gates_status",
    "SECRET_KEYS",
    "MASKED",
    "mask_secrets",
    "render_config_show",
    "ROSTER_STATUS_ORDER",
    "render_roster",
    "render_agent_spawn",
    "render_agent_run_vp",
    "render_gh_status",
    "render_gh_pr",
    "render_release_status",
    "render_release_history",
]

# The board order the whole CLI agrees on (ticket-list.sh emits in this
# order; the renderers keep it and never re-sort against it).
STATUS_ORDER = {
    "backlog": 0,
    "sprint-current": 1,
    "in-progress": 2,
    "in-review": 3,
    "done": 4,
    "archive": 5,
}

GATE_SYM = {"pass": "✓", "fail": "✗", "skip": "–"}

# The secret family (0.18 T1): the same keys the dashboard strips from
# /api/config (the panel must render "without a key ever crossing the
# wire a second time"), case-insensitive, at any section depth. The CLI
# MASKS rather than strips — the key stays, the value becomes the
# placeholder — so a masked document keeps the shape of the unmasked
# one. This is the fix, not a presentation choice: before 0.18 the
# config CLI was the one surface in the system that printed the key.
SECRET_KEYS = frozenset({"api_key", "apikey", "authorization",
                         "token", "password", "secret"})
MASKED = "<set — masked>"


def mask_secrets(value: Any) -> Any:
    """A deep copy of `value` with every secret-family key's value
    replaced by MASKED. Empty values stay empty — there is no secret to
    mask (an unset key prints as unset, not as masked). Dicts mask by
    key name; lists map; scalars pass through."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in SECRET_KEYS \
                    and v not in (None, "", []):
                out[k] = MASKED
            else:
                out[k] = mask_secrets(v)
        return out
    if isinstance(value, list):
        return [mask_secrets(v) for v in value]
    return value


def render_config_show(rows: list[dict]) -> str:
    """The config table (0.18 T1): SECTION / KEY / VALUE / SOURCE.
    `rows` carry the value (already masked where it is a secret) and
    the layer that WON per key — the documented precedence (HIVE_* env
    > user config > install defaults) made visible.
    """
    if not rows:
        return "No config found (the install defaults are missing — run 'hive doctor')."

    def cell(v: Any) -> str:
        return v if isinstance(v, str) else json.dumps(v)

    table = _table(
        ["SECTION", "KEY", "VALUE", "SOURCE"],
        [[r["section"], r["key"], cell(r["value"]), r["source"]] for r in rows])
    return "\n".join(table)


def envelope(res: dict) -> str:
    """The machine face: today's envelope, byte-for-byte."""
    return json.dumps(res, indent=2)


def finish(res: dict, args: Any, render: Callable[[dict], str] | None = None,
           error: Callable[[dict], str] | None = None,
           pass_stderr: bool = True) -> int:
    """Print the right face for a workhorse command's MCP result.

    ``--json`` → the raw envelope (byte-identical to 0.16.0). Default →
    ``render(res)``; on failure the message is ``error(res)`` when
    given, else the envelope stderr, then the client-side error field.
    The exit-code matrix is unchanged: 0 iff ``res.ok``.

    ``pass_stderr`` (T3, 0.19): default ``True`` keeps today's
    always-show passthrough of a non-empty envelope ``stderr`` on
    success — no other command moves. ``False`` is for the commands
    whose renderer IS the human face (backlog, gates run): the
    machine's notes (``No backlog for rd``, the raw gate-run log)
    stay in the --json envelope and the human path prints the summary
    only, one line per fact.
    """
    err = (res.get("stderr") or "").strip()
    as_json = bool(getattr(args, "json", False))
    if as_json:
        print(envelope(res))
    elif res.get("ok"):
        print(render(res) if render else envelope(res))
    else:
        if error is not None:
            msg = error(res)
        else:
            msg = err or res.get("error") or "unknown error"
        print(msg, file=sys.stderr)
    if pass_stderr and not as_json and err and res.get("ok"):
        print(err, file=sys.stderr)
    return 0 if res.get("ok") else 1


def assignee_name(assignee: Any) -> str:
    """The assignee the same way ticket-list.sh/ticket-get.sh read it:
    a dict's name (or agent_id), a bare string, or 'Unassigned'."""
    if isinstance(assignee, dict):
        return assignee.get("name") or assignee.get("agent_id") or "Unassigned"
    return str(assignee) if assignee else "Unassigned"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """An aligned, borderless text table (one string per line)."""
    widths = []
    for i, h in enumerate(headers):
        w = len(h)
        for r in rows:
            w = max(w, len(r[i]))
        widths.append(w)
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    for r in rows:
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip())
    return lines


def ticket_table(tickets: list[dict]) -> list[str]:
    """The ticket summary schema (ticket-list.sh's --json fields) as an
    aligned table: ID / TYPE / PRI / STATUS / TITLE (truncated) /
    ASSIGNEE, in board order (backlog → done)."""
    ordered = sorted(
        tickets,
        key=lambda t: (STATUS_ORDER.get(str(t.get("status")), 9), str(t.get("id") or "")),
    )
    rows = []
    for t in ordered:
        rows.append([
            str(t.get("id") or "?"),
            str(t.get("type") or "?"),
            str(t.get("priority") or "?"),
            str(t.get("status") or "?"),
            str(t.get("title") or "")[:60],
            assignee_name(t.get("assignee")),
        ])
    return _table(["ID", "TYPE", "PRI", "STATUS", "TITLE", "ASSIGNEE"], rows)


def render_ticket_list(data: dict) -> str:
    """One department's ticket-list document → table + count line."""
    dept = data.get("department") or "?"
    tickets = data.get("tickets") or []
    if not tickets:
        return f"0 ticket(s) in {dept} — no tickets."
    return "\n".join(ticket_table(tickets) + [f"{len(tickets)} ticket(s) in {dept}"])


def render_company_wide(data: dict) -> str:
    """The company-wide document (T2) → per-department tables under
    ``<dept>:`` headers; the count line is company-wide."""
    depts = data.get("departments") or {}
    total = data.get("total", sum(int(d.get("count") or 0) for d in depts.values()))
    blocks = []
    for dept in sorted(depts):
        doc = depts[dept] or {}
        tickets = doc.get("tickets") or []
        block = [f"{dept}:"]
        if tickets:
            block += ticket_table(tickets)
        block.append(f"{len(tickets)} ticket(s) in {dept}")
        blocks.append("\n".join(block))
    blocks.append(
        f"{total} ticket(s) company-wide across {len(depts)} department(s)"
    )
    return "\n\n".join(blocks)


def _render_list(data: dict) -> str:
    """Dispatch a ticket_list result: the company-wide document (T2) has
    a 'departments' key; the per-dept doc renders the single table."""
    if isinstance(data, dict) and data.get("departments") is not None:
        return render_company_wide(data)
    return render_ticket_list(data)


def render_ticket_card(t: dict) -> str:
    """A ticket's full document → a card: header line with
    status/type/priority/assignee, description, acceptance criteria,
    comments as ``author (ts): body``, the verification block."""
    lines = [f"{t.get('id', '?')} — {t.get('title', '')}"]
    lines.append(f"  status:    {t.get('status', '?')}")
    lines.append(f"  type:      {t.get('type', '?')}")
    lines.append(f"  priority:  {t.get('priority', '?')}")
    lines.append(f"  assignee:  {assignee_name(t.get('assignee'))}")
    if t.get("created_at"):
        lines.append(f"  created:   {t['created_at']}")
    if t.get("updated_at"):
        lines.append(f"  updated:   {t['updated_at']}")
    desc = (t.get("description") or "").strip()
    if desc:
        lines += [""] + ["Description:"] + [f"  {l}" for l in desc.splitlines()]
    ac = t.get("acceptance_criteria") or []
    if ac:
        lines += [""] + ["Acceptance criteria:"] + [
            f"  {i}. {c}" for i, c in enumerate(ac, 1)
        ]
    comments = t.get("comments") or []
    if comments:
        lines += [""] + [f"Comments ({len(comments)}):"]
        for c in comments:
            lines.append(
                f"  {c.get('author', '?')} ({c.get('timestamp', '?')}): {c.get('body', '')}"
            )
    if t.get("gates_passed") or t.get("verified_by") or t.get("verification_rationale"):
        lines += [""] + ["Verification:"]
        if t.get("gates_passed"):
            at = f" ({t['gates_passed_at']})" if t.get("gates_passed_at") else ""
            lines.append(f"  gates_passed=true{at}")
        if t.get("verified_by"):
            at = f" ({t['verified_at']})" if t.get("verified_at") else ""
            lines.append(f"  verified_by={t['verified_by']}{at}")
        if t.get("verification_rationale"):
            lines.append(f"  rationale: {t['verification_rationale']}")
    return "\n".join(lines)


def _hhmm(ts: str) -> str:
    """HH:MM out of an ISO 8601 timestamp; the raw string if unparseable."""
    m = re.search(r"T(\d{2}:\d{2})", ts or "")
    return m.group(1) if m else (ts or "")


def render_chat_read(data: dict) -> str:
    """A chat-read document → ``HH:MM author: message`` lines."""
    channel = data.get("channel") or "?"
    messages = data.get("messages") or []
    if not messages:
        return f"#{channel} — no messages."
    lines = [f"#{channel} — {len(messages)} message(s)"]
    for m in messages:
        lines.append(f"{_hhmm(m.get('timestamp', ''))} {m.get('role', '?')}: {m.get('message', '')}")
    return "\n".join(lines)


def render_chat_channels(data: dict) -> str:
    """chat_channels → the channel table (name, message count, last
    message time); an empty board names itself (T3)."""
    channels = data.get("channels") or []
    if not channels:
        return ("No channels yet — the first 'hive chat send' creates one.")
    rows = [
        [c.get("channel", "?"),
         str(c.get("messages", 0)),
         str(c.get("last_at") or "—")]
        for c in channels
    ]
    return "\n".join(_table(["CHANNEL", "MESSAGES", "LAST"], rows))


def render_chat_send(channel: str) -> str:
    return f"✓ sent to {channel}"


# The roster's status order (0.18 T2): active rows first, then claimed
# (a paper assignment with no live process), then inactive.
ROSTER_STATUS_ORDER = {"active": 0, "claimed": 1, "inactive": 2}


def render_roster(agents: list[dict]) -> str:
    """A roster document's `.agents` array → the NAME / ROLE / DEPT /
    STATUS / TICKET / LAST ACTIVE table (the roster document's own
    fields), active rows first, then claimed, then inactive, with the
    count line. On a never-run workspace the listing IS the state:
    inactive VPs + the count line say 'company not running' without a
    dead-end branch; a derived-but-empty roster is a count-0 listing
    (visible, not silent — the 0.17 T2 contract)."""
    ordered = sorted(
        agents,
        key=lambda a: (ROSTER_STATUS_ORDER.get(str(a.get("status")), 9),
                       str(a.get("department") or ""),
                       str(a.get("name") or "")),
    )
    rows = [
        [str(a.get("name") or "?"),
         str(a.get("role") or "?"),
         str(a.get("department") or "?"),
         str(a.get("status") or "?"),
         str(a.get("assigned_ticket") or "—"),
         str(a.get("last_active") or "—")]
        for a in ordered
    ]
    lines = _table(["NAME", "ROLE", "DEPT", "STATUS", "TICKET", "LAST ACTIVE"], rows)
    active = sum(1 for a in agents if a.get("status") == "active")
    lines.append(f"{len(agents)} agent(s) — {active} active")
    return "\n".join(lines)


def dashboard_hint() -> str:
    """The one-line dashboard pointer (hive init's polish, reused)."""
    port = os.environ.get("DASHBOARD_PORT", "3456")
    return f"watch it at http://localhost:{port}/"


def render_ticket_create(res: dict, args: Any) -> str:
    """"✓ T-<n> (type, priority) in <dept> — backlog" + the dashboard hint."""
    out = (res.get("stdout") or "").strip()
    tid = out.splitlines()[0] if out else "?"
    return f"✓ {tid} ({args.type}, {args.priority}) in {args.dept} — backlog\n  {dashboard_hint()}"


def render_action(res: dict) -> str:
    """move/claim/comment/approve → the script's resulting-state line(s),
    with the first line checked off."""
    lines = [l for l in (res.get("stdout") or "").strip().splitlines() if l.strip()]
    if not lines:
        return "✓ done"
    lines[0] = "✓ " + lines[0]
    return "\n".join(lines)


def render_backlog(res: dict, args: Any, summaries: dict[str, dict]) -> str:
    """The prioritize envelope (stdout = ranked ticket ids) → a top-N
    table (priority, type, title, id). ``summaries`` maps id → the
    ticket-list summary for that id (the second local MCP call)."""
    ids = [l.strip() for l in (res.get("stdout") or "").strip().splitlines() if l.strip()]
    if not ids:
        return f"Backlog for {args.dept} is empty."
    rows = []
    for tid in ids:
        s = summaries.get(tid, {})
        rows.append([
            str(s.get("priority") or "?"),
            str(s.get("type") or "?"),
            str(s.get("title") or "")[:60],
            tid,
        ])
    header = f"Top {len(rows)} ticket(s) from {args.dept}'s backlog:"
    return "\n".join([header] + _table(["PRI", "TYPE", "TITLE", "ID"], rows))


def render_gates_check(res: dict) -> str:
    """A gates report → one ✓/✗/– line per gate with its detail."""
    data = res.get("data") or {}
    tid = data.get("ticket_id", "?")
    gates = data.get("gates") or {}
    if not gates:
        return f"No gates recorded for {tid}."
    lines = [f"Gates for {tid}:"]
    for name, g in gates.items():
        sym = GATE_SYM.get(g.get("status", ""), "?")
        detail = (g.get("detail") or "").strip()
        lines.append(f"  {sym} {name} — {detail}" if detail else f"  {sym} {name}")
    return "\n".join(lines)


def render_gates_run(res: dict, dept: str) -> str:
    """A gate run → per-ticket summary lines, parsed from the run's log
    (the script is --quiet: its log lines are the machine's summary)."""
    err = (res.get("stderr") or "").strip()
    summary = []
    for line in err.splitlines():
        m = re.search(r"PASS\s+(\S+): all gates passed", line)
        if m:
            summary.append(f"✓ {m.group(1)} ({dept}) — all gates passed, awaiting verification IC")
            continue
        m = re.search(r"BOUNCE\s+(\S+): (.*)", line)
        if m:
            summary.append(f"✗ {m.group(1)} ({dept}) — bounced to in-progress: {m.group(2).strip()}")
            continue
        if re.search(r"no ticket in in-review for dept=\S+", line):
            summary.append(f"Nothing to gate — no in-review tickets in {dept}.")
    if summary:
        return "\n".join(summary)
    if res.get("ok"):
        return f"Gates ran for {dept} (no ticket summary in the run log)."
    return err or res.get("error") or "gate run failed"


def render_gates_status(cfg: dict, dept_rows: list[dict]) -> str:
    """The gates-status view (0.16's human layout, unchanged) rendered
    from the structured rows the handler collects."""
    lines = [
        f"Gates config ({cfg.get('_path', '?')}):",
        f"  mode:             {cfg.get('mode', '(unset = relaxed)')}",
    ]
    for k in ("require_tests", "require_lint", "require_security", "require_commits"):
        # T3 (0.19): the config table's spelling (0.18 T1) — true/false,
        # not Python's True/False.
        lines.append(f"  {k:<17} {'true' if cfg.get(k, False) else 'false'}")
    lines.append("")
    if not dept_rows:
        lines.append("  No in-review tickets across any department.")
        return "\n".join(lines)
    for row in dept_rows:
        lines.append(f"  {row['dept']}: {row['count']} in-review")
        for t in row["tickets"]:
            lines.append(f"    {t['id']:<25} {t['state']}{t['gate_summary']}")
        lines.append("")
    return "\n".join(lines)


def render_agent_spawn(agent: str, mode: str, company_dir: str | None) -> str:
    lines = [f"✓ {agent} spawned (mode={mode})"]
    if company_dir:
        lines.append(f"  log: {company_dir}/logs/spawns.log")
    return "\n".join(lines)


def render_agent_run_vp(dept: str, company_dir: str | None) -> str:
    lines = [f"✓ VP cycle started for {dept} (agent vp-{dept})"]
    if company_dir:
        lines.append(f"  log: {company_dir}/logs/spawns.log")
    return "\n".join(lines)


def render_gh_status(data: dict) -> str:
    """gh_status → aligned gh/auth/remote/branch lines."""
    def line(label: str, key: str, yes: str, no: str) -> str:
        v = bool(data.get(key))
        return f"{label:<9} {'✓ ' if v else '✗ '}{yes if v else no}"
    lines = [
        line("gh CLI:", "installed", "installed", "not installed"),
        line("auth:", "authed", "authenticated", "not authenticated"),
        f"remote:   {data.get('remote') or '(none)'}",
        f"branch:   {data.get('default_branch') or '(unknown)'}",
    ]
    return "\n".join(lines)


def render_gh_pr(res: dict) -> str:
    """gh pr open/merge → one-line confirmations with the PR number/URL."""
    out = (res.get("stdout") or "").strip()
    m = re.search(r"opened PR #(\d+)(?:\s+\((\S+)\))?", out)
    if m:
        url = m.group(2) or f"PR #{m.group(1)}"
        return f"✓ opened PR #{m.group(1)} — {url}"
    merged = re.search(r"merged", out, re.IGNORECASE)
    if merged:
        return "✓ " + out.splitlines()[-1]
    if out:
        return "✓ " + out.splitlines()[-1]
    return "✓ PR operation completed"


def render_release_status(data: dict, version: str | None = None) -> str:
    if data.get("error"):
        # T4 (0.19): one sentence per state — the doubled
        # `No release record found (no release record found)` wrapped
        # the wire text inside its own sentence, and the no-version,
        # no-record state (the NORMAL fresh-workspace state) wore the
        # same stuttered sentence as a lookup miss.
        if version:
            return (f"No release record for {version} — "
                    "'hive release list' shows what is recorded.")
        return ("No release in flight — a release record appears when a "
                "sprint reaches stage 6 (build-and-test); 'hive release "
                "list' shows what is published.")
    rel = data.get("release") or {}
    lines = [f"sprint={data.get('sprint_id') or '?'}  stage={data.get('stage') or '?'}"]
    for k in ("version", "publish_status", "test_log", "verify_log", "tarball", "sha256"):
        if rel.get(k):
            lines.append(f"  {k}: {rel[k]}")
    return "\n".join(lines)


def render_release_history(data: dict) -> str:
    releases = data.get("releases") or []
    if not releases:
        return "No releases in sprints/history."
    rows = [
        [
            str(r.get("version") or "?"),
            str(r.get("publish_status") or "?"),
            str(r.get("sprint_id") or "?"),
            str(r.get("ended_at") or ""),
        ]
        for r in releases
    ]
    return "\n".join(_table(["VERSION", "PUBLISH", "SPRINT", "ENDED"], rows))
