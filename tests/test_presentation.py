"""T1 (0.17): lib/hive_cli/present.py — the CLI's human face.

The renderers are pure functions over the MCP payloads (the documented
ticket-list.sh --json schema, the chat-read document, the gates report),
and finish() pins the two-face contract: --json is the raw envelope,
the default is the human rendering, the exit-code matrix is 0 iff
res.ok, and a non-empty envelope stderr is always shown."""
import argparse
import json
import re
import sys
from pathlib import Path

_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)

from hive_cli import present  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — the documented payload shapes
# ---------------------------------------------------------------------------

def _ticket(tid, title, status, pri, typ, assignee=None, **kw):
    t = {
        "id": tid, "title": title, "status": status, "type": typ,
        "priority": pri,
        "gates_passed": False, "verified_by": "",
        "skip_tdd_red": False, "comment_count": 0, "last_comment": None,
    }
    if assignee is not None:
        t["assignee"] = assignee
    t.update(kw)
    return t


def _dept_doc(dept, tickets):
    return {"department": dept, "count": len(tickets),
            "tickets": tickets, "mode": "summary"}


class TestTicketTable:
    def test_columns_and_board_order(self):
        tickets = [
            _ticket("T-3", "Third", "backlog", "low", "chore"),
            _ticket("T-1", "First — a very long title that goes on and on past sixty chars", "in-progress", "high", "feature",
                    assignee={"name": "Eng-1", "agent_id": "a1"}),
            _ticket("T-2", "Second", "in-review", "medium", "bug"),
            _ticket("T-4", "Fourth", "sprint-current", "critical", "infrastructure",
                    assignee={"agent_id": "a2"}),
        ]
        lines = present.ticket_table(tickets)

        def cells(l):
            return re.split(r"  +", l)

        def cell_start(l, n):
            """Character offset where the nth cell (1-based) starts."""
            pos = 0
            for _ in range(n - 1):
                pos = l.index("  ", pos) + 2
                while l[pos] == " ":
                    pos += 1
            return pos

        # Header is the six column labels, every line has six cells
        assert cells(lines[0]) == ["ID", "TYPE", "PRI", "STATUS", "TITLE", "ASSIGNEE"]
        assert all(len(cells(l)) == 6 for l in lines)
        # Board order: backlog -> sprint-current -> in-progress -> in-review
        bodies = lines[1:]
        ids = [c[0] for c in (cells(l) for l in bodies)]
        assert ids == ["T-3", "T-4", "T-1", "T-2"]
        # Width-aligned: every column starts at the same offset in every row
        for n in range(1, 7):
            starts = {cell_start(l, n) for l in lines}
            assert len(starts) == 1, f"column {n} not aligned: {starts}"
        # Assignee rendering: dict name wins, agent_id fallback, Unassigned
        assert cells(bodies[2])[5] == "Eng-1"
        assert cells(bodies[1])[5] == "a2"
        assert cells(bodies[0])[5] == "Unassigned"
        assert cells(bodies[3])[5] == "Unassigned"

    def test_title_truncated_to_60(self):
        lines = present.ticket_table([
            _ticket("T-9", "x" * 100, "backlog", "low", "chore"),
        ])
        assert "x" * 60 in lines[1] and "x" * 61 not in lines[1]

    def test_assignee_name_variants(self):
        assert present.assignee_name({"name": "N", "agent_id": "A"}) == "N"
        assert present.assignee_name({"agent_id": "A"}) == "A"
        assert present.assignee_name("bare") == "bare"
        assert present.assignee_name(None) == "Unassigned"
        assert present.assignee_name({}) == "Unassigned"


class TestRenderTicketList:
    def test_table_plus_count_line(self):
        doc = _dept_doc("rd", [
            _ticket("T-1", "One", "in-progress", "high", "feature",
                    assignee={"name": "Eng-1"}),
            _ticket("T-2", "Two", "backlog", "medium", "bug"),
        ])
        out = present.render_ticket_list(doc)
        lines = out.splitlines()
        assert "T-1" in out and "Eng-1" in out and "in-progress" in out
        # Count line at the end
        assert lines[-1] == "2 ticket(s) in rd"
        # The envelope keys must NOT be on screen
        for key in ("exit_code", "stderr", "ok"):
            assert key not in out

    def test_empty_dept_visible_not_silent(self):
        out = present.render_ticket_list(_dept_doc("it", []))
        assert "0 ticket(s) in it" in out


class TestRenderCompanyWide:
    def test_grouped_tables_and_company_count(self):
        data = {
            "departments": {
                "rd": _dept_doc("rd", [_ticket("T-1", "One", "backlog", "high", "feature")]),
                "it": _dept_doc("it", []),
            },
            "total": 1,
            "mode": "summary",
        }
        out = present.render_company_wide(data)
        assert "rd:" in out and "it:" in out
        assert "1 ticket(s) in rd" in out
        assert "0 ticket(s) in it" in out
        assert out.splitlines()[-1] == "1 ticket(s) company-wide across 2 department(s)"


class TestRenderTicketCard:
    def test_card_sections(self):
        t = _ticket("T-7", "Login", "in-review", "high", "feature",
                    assignee={"name": "Eng-1"},
                    description="Add OAuth\nsecond line",
                    acceptance_criteria=["Google login works"],
                    created_at="2026-11-10T09:00:00Z",
                    updated_at="2026-11-11T10:00:00Z",
                    comments=[{"author": "Eng-1", "timestamp": "2026-11-11T09:30:00Z",
                               "body": "implemented"}],
                    gates_passed=True, gates_passed_at="2026-11-11T09:45:00Z",
                    verified_by="Verification-IC", verified_at="2026-11-11T09:50:00Z",
                    verification_rationale="Verified AC1, probed edge cases")
        out = present.render_ticket_card(t)
        assert out.splitlines()[0] == "T-7 — Login"
        assert "status:    in-review" in out
        assert "priority:  high" in out
        assert "assignee:  Eng-1" in out
        assert "Description:" in out and "  Add OAuth" in out
        assert "1. Google login works" in out
        assert "  Eng-1 (2026-11-11T09:30:00Z): implemented" in out
        assert "Verification:" in out
        assert "gates_passed=true (2026-11-11T09:45:00Z)" in out
        assert "verified_by=Verification-IC (2026-11-11T09:50:00Z)" in out
        assert "rationale: Verified AC1, probed edge cases" in out

    def test_minimal_ticket_no_empty_sections(self):
        out = present.render_ticket_card(_ticket("T-1", "T", "backlog", "low", "chore"))
        assert "Comments" not in out and "Verification" not in out
        assert "Description:" not in out


class TestRenderChat:
    def test_read_hhmm_transcript(self):
        data = {"channel": "general", "count": 2, "messages": [
            {"timestamp": "2026-11-11T09:30:00Z", "role": "user",
             "department": "", "message": "sprint starting"},
            {"timestamp": "", "role": "system", "department": "",
             "message": "raw line"},
        ]}
        out = present.render_chat_read(data)
        lines = out.splitlines()
        assert lines[0] == "#general — 2 message(s)"
        assert lines[1] == "09:30 user: sprint starting"
        assert lines[2].endswith("system: raw line")

    def test_read_empty(self):
        out = present.render_chat_read({"channel": "incidents", "count": 0, "messages": []})
        assert out == "#incidents — no messages."

    def test_send(self):
        assert present.render_chat_send("engineering") == "✓ sent to engineering"


class TestRenderRoster:
    """T2 (0.18): the roster table — the document's own fields, active
    rows first, then claimed, then inactive, count line at the end;
    a derived-but-empty roster is a count-0 listing (visible, not
    silent)."""

    @staticmethod
    def _agent(name, role, dept, status, ticket=None, last=None):
        return {"agent_id": f"{role.lower()}-{dept}", "name": name,
                "role": role, "department": dept, "status": status,
                "assigned_ticket": ticket, "worktree_path": None,
                "last_active": last}

    def test_columns_and_count_line(self):
        out = present.render_roster([
            self._agent("VP-RD", "VP", "rd", "inactive"),
            self._agent("VP-IT", "VP", "it", "active", last="2026-11-11T09:00:00Z"),
        ])
        import re
        lines = out.splitlines()
        assert re.split(r"\s{2,}", lines[0]) == \
            ["NAME", "ROLE", "DEPT", "STATUS", "TICKET", "LAST ACTIVE"]
        assert lines[-1] == "2 agent(s) — 1 active"

    def test_active_first_then_claimed_then_inactive(self):
        out = present.render_roster([
            self._agent("VP-IT", "VP", "it", "inactive"),
            self._agent("Eng-1", "IC", "rd", "claimed", ticket="RD-7777"),
            self._agent("VP-RD", "VP", "rd", "active"),
        ])
        lines = out.splitlines()[1:-1]
        assert lines[0].startswith("VP-RD"), "active rows come first"
        assert lines[1].startswith("Eng-1"), "then claimed"
        assert lines[2].startswith("VP-IT"), "then inactive"
        assert "RD-7777" in lines[1]

    def test_null_fields_render_as_dashes(self):
        out = present.render_roster([
            self._agent("Eng-1", "IC", "rd", "claimed", ticket="RD-7777"),
        ])
        row = re.split(r"\s{2,}", out.splitlines()[1])
        assert row[3] == "claimed"
        assert row[4] == "RD-7777"
        assert row[-1] == "—", "missing cells render as dashes"

    def test_empty_roster_is_a_count_zero_listing(self):
        out = present.render_roster([])
        lines = out.splitlines()
        assert lines[0] == "NAME  ROLE  DEPT  STATUS  TICKET  LAST ACTIVE"
        assert lines[-1] == "0 agent(s) — 0 active"
        assert len(lines) == 2, "the listing names itself; it is not silent"


class TestRenderTicketCreateAndAction:
    def test_create_line_and_hint(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_PORT", "9999")
        res = {"ok": True, "exit_code": 0, "stderr": "",
               "stdout": "T-42"}
        args = argparse.Namespace(dept="rd", type="feature", priority="high")
        out = present.render_ticket_create(res, args)
        assert out.splitlines()[0] == "✓ T-42 (feature, high) in rd — backlog"
        assert out.splitlines()[1].strip() == "watch it at http://localhost:9999/"

    def test_action_first_line_checked_off(self):
        res = {"ok": True, "exit_code": 0, "stderr": "",
               "stdout": "Moved T-1: in-progress -> in-review"}
        assert present.render_action(res) == "✓ Moved T-1: in-progress -> in-review"

    def test_action_multiline_kept(self):
        res = {"ok": True, "exit_code": 0, "stderr": "",
               "stdout": "line one\nline two"}
        assert present.render_action(res) == "✓ line one\nline two"


class TestRenderBacklog:
    def test_topn_table(self):
        res = {"ok": True, "exit_code": 0, "stderr": "",
               "stdout": "T-2\nT-1"}   # ranked order from the script
        args = argparse.Namespace(dept="it")
        summaries = {
            "T-1": {"id": "T-1", "title": "Older", "type": "bug",
                    "priority": "medium"},
            "T-2": {"id": "T-2", "title": "Newer", "type": "feature",
                    "priority": "high"},
        }
        out = present.render_backlog(res, args, summaries)
        lines = out.splitlines()
        assert lines[0] == "Top 2 ticket(s) from it's backlog:"
        assert lines[2].split()[0] == "high" and lines[2].split()[-1] == "T-2"
        assert lines[3].split()[0] == "medium" and lines[3].split()[-1] == "T-1"

    def test_empty_backlog(self):
        res = {"ok": True, "exit_code": 0, "stderr": "", "stdout": ""}
        args = argparse.Namespace(dept="it")
        assert present.render_backlog(res, args, {}) == "Backlog for it is empty."


class TestRenderGates:
    def test_check_per_gate_lines(self):
        res = {"ok": True, "exit_code": 0, "stderr": "", "data": {
            "ticket_id": "T-5",
            "gates": {
                "worktree-exists": {"status": "pass", "detail": "/wt", "checked_at": "T"},
                "has-commits": {"status": "fail", "detail": "0 commits beyond main", "checked_at": "T"},
                "tests-pass": {"status": "skip", "detail": "no test framework detected", "checked_at": "T"},
            },
        }}
        out = present.render_gates_check(res)
        assert out.splitlines()[0] == "Gates for T-5:"
        assert "✓ worktree-exists — /wt" in out
        assert "✗ has-commits — 0 commits beyond main" in out
        assert "– tests-pass — no test framework detected" in out

    def test_check_no_gates(self):
        res = {"ok": True, "exit_code": 0, "stderr": "", "data": {"ticket_id": "T-5", "gates": {}}}
        assert present.render_gates_check(res) == "No gates recorded for T-5."

    def test_run_pass(self):
        res = {"ok": True, "exit_code": 0, "stderr":
               "[ts] gates dept=rd starting gate run\n"
               "[ts] gates dept=rd   PASS T-5: all gates passed - awaiting IC review"}
        out = present.render_gates_run(res, "rd")
        assert out == ("✓ T-5 (rd) — all gates passed, awaiting verification IC")

    def test_run_bounce(self):
        res = {"ok": False, "exit_code": 2, "stderr":
               "[ts] gates dept=rd   FAIL tdd-order: no commit on branch mentions T-5\n"
               "[ts] gates dept=rd   BOUNCE T-5: TDD violation: the first commit for T-5 must add/modify a test file."}
        out = present.render_gates_run(res, "rd")
        assert out.startswith("✗ T-5 (rd) — bounced to in-progress: TDD violation")

    def test_run_idle(self):
        res = {"ok": True, "exit_code": 0,
               "stderr": "[ts] gates dept=it no ticket in in-review for dept=it — nothing to gate"}
        assert present.render_gates_run(res, "it") == \
            "Nothing to gate — no in-review tickets in it."


class TestRenderGatesStatus:
    def test_layout_matches_016(self):
        cfg = {"_path": "/cd/gates.json", "mode": "strict",
               "require_tests": True, "require_lint": False,
               "require_security": True, "require_commits": False}
        rows = [{"dept": "it", "count": 1, "tickets": [
            {"id": "T-1", "state": "BLOCKED (gates not passed)",
             "gate_summary": "  [FAIL: has-commits]"},
        ]}]
        out = present.render_gates_status(cfg, rows)
        assert out.splitlines()[0] == "Gates config (/cd/gates.json):"
        assert "  mode:             strict" in out
        assert f"  {'require_lint':<17} False" in out
        assert "  it: 1 in-review" in out
        assert (f"    {'T-1':<25} BLOCKED (gates not passed)"
                "  [FAIL: has-commits]") in out

    def test_empty(self):
        out = present.render_gates_status({"_path": "/cd/gates.json"}, [])
        assert "No in-review tickets across any department." in out


class TestRenderAgentGhRelease:
    def test_agent_spawn(self):
        out = present.render_agent_spawn("ceo", "FRESH", "/p/.company")
        assert out.splitlines()[0] == "✓ ceo spawned (mode=FRESH)"
        assert out.splitlines()[1] == "  log: /p/.company/logs/spawns.log"

    def test_agent_run_vp(self):
        out = present.render_agent_run_vp("rd", "/p/.company")
        assert out.splitlines()[0] == "✓ VP cycle started for rd (agent vp-rd)"

    def test_gh_status_lines(self):
        out = present.render_gh_status({"installed": True, "authed": True,
                                        "remote": "git@github.com:o/r.git",
                                        "default_branch": "main"})
        lines = out.splitlines()
        assert lines[0] == "gh CLI:   ✓ installed"
        assert lines[1] == "auth:     ✓ authenticated"
        assert lines[2] == "remote:   git@github.com:o/r.git"
        assert lines[3] == "branch:   main"

    def test_gh_status_not_installed(self):
        out = present.render_gh_status({"installed": False, "authed": False,
                                        "remote": None, "default_branch": None})
        assert "✗ not installed" in out and "(none)" in out

    def test_gh_pr_open_confirmation(self):
        res = {"ok": True, "exit_code": 0, "stderr": "",
               "stdout": "gh-pr-open: opening PR for feature/T-1 → main (ticket=T-1)\n"
                         "gh-pr-open: opened PR #5 (https://github.com/o/r/pull/5)\n5"}
        assert present.render_gh_pr(res) == \
            "✓ opened PR #5 — https://github.com/o/r/pull/5"

    def test_gh_pr_fallback_last_line(self):
        res = {"ok": True, "exit_code": 0, "stderr": "", "stdout": "merged via api"}
        assert present.render_gh_pr(res) == "✓ merged via api"

    def test_release_status(self):
        data = {"sprint_id": "s1", "stage": "release-verify",
                "release": {"version": "0.17.0", "publish_status": "pending",
                            "tarball": "/t/tar.gz"}}
        out = present.render_release_status(data)
        assert out.splitlines()[0] == "sprint=s1  stage=release-verify"
        assert "  version: 0.17.0" in out and "  tarball: /t/tar.gz" in out

    def test_release_status_error(self):
        assert present.render_release_status({"error": "no release record found"}) == \
            "No release record found (no release record found)"

    def test_release_history(self):
        data = {"releases": [{"version": "0.16.0", "publish_status": "published",
                              "sprint_id": "s1", "ended_at": "2026-11-11T00:00:00Z"}]}
        out = present.render_release_history(data)
        assert "0.16.0" in out and "published" in out
        assert present.render_release_history({"releases": []}) == \
            "No releases in sprints/history."


class TestFinishContract:
    """The two-face contract: --json is byte-identical, the default is
    human, the exit matrix is 0 iff ok, stderr is always shown."""

    ENVELOPE = {"ok": True, "exit_code": 0, "stderr": "",
                "data": {"department": "rd", "count": 1,
                         "tickets": [_ticket("T-1", "One", "backlog", "low", "chore")],
                         "mode": "summary"}}

    def _args(self, as_json):
        return argparse.Namespace(json=as_json)

    def test_json_is_byte_identical_envelope(self, capsys):
        rc = present.finish(self.ENVELOPE, self._args(True),
                            lambda r: "HUMAN")
        out = capsys.readouterr().out
        assert rc == 0
        assert out == json.dumps(self.ENVELOPE, indent=2) + "\n"

    def test_default_is_human_not_envelope(self, capsys):
        rc = present.finish(self.ENVELOPE, self._args(False),
                            lambda r: "HUMAN LINE")
        out = capsys.readouterr().out
        assert rc == 0
        assert out == "HUMAN LINE\n"
        for key in ("exit_code", "stderr", '"ok"', 'data'):
            assert key not in out

    def test_error_exit_1_and_message_to_stderr(self, capsys):
        res = {"ok": False, "exit_code": 1, "stderr": "Channel #x not found",
               "data": None}
        rc = present.finish(res, self._args(False), lambda r: "HUMAN")
        captured = capsys.readouterr()
        assert rc == 1
        assert captured.out == ""
        assert "Channel #x not found" in captured.err

    def test_error_without_stderr_uses_error_field(self, capsys):
        res = {"ok": False, "error": "ConnectionError: server down"}
        rc = present.finish(res, self._args(False), lambda r: "HUMAN")
        captured = capsys.readouterr()
        assert rc == 1
        assert "ConnectionError: server down" in captured.err

    def test_error_json_still_full_envelope(self, capsys):
        res = {"ok": False, "exit_code": 1, "stderr": "boom", "data": None}
        rc = present.finish(res, self._args(True), lambda r: "HUMAN")
        out = capsys.readouterr().out
        assert rc == 1
        parsed = json.loads(out)
        assert set(parsed.keys()) == {"ok", "exit_code", "stderr", "data"}

    def test_success_with_stderr_passes_through(self, capsys):
        res = {"ok": True, "exit_code": 0, "stderr": "warn: slow", "data": {}}
        rc = present.finish(res, self._args(False), lambda r: "HUMAN LINE")
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.out == "HUMAN LINE\n"
        assert "warn: slow" in captured.err

    def test_json_envelope_success_keys_pinned(self):
        """Done-when pin: --json parses, top-level keys exactly
        {ok, exit_code, stderr, data} for a shell-run tool success."""
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            present.finish(self.ENVELOPE, self._args(True), lambda r: "H")
        parsed = json.loads(buf.getvalue())
        assert set(parsed.keys()) == {"ok", "exit_code", "stderr", "data"}
