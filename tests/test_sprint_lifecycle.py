"""Behavioral tests for sprint-lifecycle.sh with agent-spawn stubbed.

The real driver spawns LLM agents through lib/core/agent-spawn.sh. These
tests run the REAL sprint-lifecycle.sh against a temporary project, with a
temporary copy of lib/ in which core/agent-spawn.sh is replaced by a
recording stub (logs env + prompt per spawn and, per STUB_MODE, may call
ticket-approve.sh or fail on the verify stage). Everything else
ticket-move, sprint-fill, verification-gates, ticket-approve, git, the
release test suite) runs for real.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_DIR = Path(__file__).resolve().parent.parent
LIFECYCLE = INSTALL_DIR / "lib" / "tools" / "sprint-lifecycle.sh"

DEPT = "backend"          # dynamic (non-admin) dept -> full code gates
TICKET = "TICK-1"
TITLE = "ALPHATITLE distinctive title marker"
DESC = "ALPHADESC distinctive description marker"

STUB_AGENT_SPAWN = r'''#!/usr/bin/env bash
# Test stub for agent-spawn.sh: records each spawn (env + prompt + the
# sprint stage at that moment) and optionally acts on the verify stage.
log() { printf '%s\n' "$@" >> "$AGENT_SPAWN_LOG"; }
args=("$@")
[ "${args[0]:-}" = "--" ] && args=("${args[@]:1}")
PROMPT="${args[*]}"
log "=== SPAWN agent=$HIVE_AGENT dept=$HIVE_DEPT ticket=$HIVE_TICKET stage=$(jq -r '.stage // "?"' "$COMPANY_DIR/sprints/current.json" 2>/dev/null)"
printf '%s\n' "$PROMPT" >> "$AGENT_SPAWN_LOG"
log "=== END SPAWN agent=$HIVE_AGENT ==="
case "${STUB_MODE:-noop}" in
  approve)
    if printf '%s' "$PROMPT" | grep -q -- "--- STAGE: verify"; then
      bash "$SKILL_DIR/lib/tools/ticket-approve.sh" "$HIVE_DEPT" "$HIVE_TICKET" \
        "stub-verifier" "Stub approval: checked the diff against every acceptance criterion, re-ran the suite green, and probed the edge cases called out in the ticket." \
        >> "$AGENT_SPAWN_LOG" 2>&1
    fi
    ;;
  fail-verify)
    if printf '%s' "$PROMPT" | grep -q -- "--- STAGE: verify"; then
      log "STUB: verify spawn failing on purpose"
      exit 1
    fi
    ;;
esac
exit 0
'''


def _ticket_json(status):
    return json.dumps({
        "id": TICKET,
        "title": TITLE,
        "description": DESC,
        "status": status,
        "type": "feature",
        "priority": "high",
        "created_by": "test",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "sprint": "sprint-test",
        "labels": [],
    }, indent=2)


class LifecycleHarness:
    """Temp project + .company + lib copy with a stubbed agent-spawn."""

    def __init__(self, tmp_path, mode="noop", with_code_branch=False,
                 failing_release_tests=False, preset_release_retries=0):
        self.root = tmp_path
        self.project = tmp_path / "proj"
        self.hive = tmp_path / "hive"
        self.spawn_log = tmp_path / "spawns.log"
        self.mode = mode

        self._build_project()
        self._build_company(failing_release_tests, preset_release_retries)
        if with_code_branch:
            self._build_code_branch()
        self._build_hive_copy()

    def _git(self, *args, cwd=None):
        r = subprocess.run(["git", *args], cwd=cwd or self.project,
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr}")
        return r

    def _build_project(self):
        self.project.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "test")
        self._git("config", "user.email", "test@example.com")
        (self.project / "README.md").write_text("test project\n")
        self._git("add", "README.md")
        self._git("commit", "-m", "initial commit")

    def _build_company(self, failing_release_tests, preset_release_retries):
        company = self.project / ".company"
        (company / "logs").mkdir(parents=True)
        (company / "sprints").mkdir()
        (company / "org-chart.json").write_text(json.dumps({
            "departments": {
                DEPT: {"name": "Backend", "type": "dynamic"},
                "it": {"name": "IT"},
            },
            "created_at": "2026-01-01T00:00:00Z",
        }, indent=2))
        for dept in (DEPT, "it"):
            for status in ("backlog", "sprint-current", "in-progress",
                           "in-review", "done"):
                (company / "departments" / dept / "tickets" / status).mkdir(
                    parents=True)
        if failing_release_tests:
            # Release-level (project root) test suite that FAILS.
            (self.project / "pytest.ini").write_text("")
            (self.project / "test_release.py").write_text(
                "def test_release_never_ships():\n    assert False\n")
            self._git("add", "pytest.ini", "test_release.py")
            self._git("commit", "-m", "failing release test")
        if preset_release_retries:
            (company / "sprints" / "current.json").write_text(json.dumps({
                "sprint_id": "sprint-preset",
                "started_at": "2026-01-01T00:00:00Z",
                "stage": "ceo-plan",
                "stage_started_at": "2026-01-01T00:00:00Z",
                "loop_iteration": 0,
                "release_retry_count": preset_release_retries,
                "tickets_in_sprint": [],
                "ceo_direction_path": None,
                "release": {
                    "version": None, "test_log": None, "verify_log": None,
                    "tarball": None, "sha256": None,
                    "publish_status": "pending",
                },
            }, indent=2))

    def _build_code_branch(self):
        """Worktree + feature branch with a [TDD-RED] commit whose tests
        pass, so the deterministic gates go green and the verifier runs."""
        branch = f"feature/{TICKET}"
        wt = self.project / ".worktrees" / TICKET
        self._git("worktree", "add", "-b", branch, str(wt))
        (wt / "pytest.ini").write_text("")
        (wt / "test_tick.py").write_text("def test_alpha():\n    assert True\n")
        self._git("add", "-A", cwd=wt)
        self._git("commit", "-m", f"[TDD-RED] {TICKET}: add failing test first",
                  cwd=wt)

    def _build_hive_copy(self):
        shutil.copytree(INSTALL_DIR / "lib", self.hive / "lib")
        stub = self.hive / "lib" / "core" / "agent-spawn.sh"
        stub.write_text(STUB_AGENT_SPAWN)
        stub.chmod(0o755)

    def run(self, timeout=180):
        env = os.environ.copy()
        env.update({
            "HIVE_ROOT": str(self.hive),
            "AGENT_SPAWN_LOG": str(self.spawn_log),
            "STUB_MODE": self.mode,
            # Cap the main loop so the safety valve fires after a handful of
            # bounces instead of 100 - the tests only need it to trigger.
            "HIVE_MAX_LOOP_ITERATIONS": "3",
        })
        r = subprocess.run(
            ["bash", str(LIFECYCLE), str(self.project)],
            env=env, capture_output=True, text=True, timeout=timeout,
        )
        self.result = r
        return r

    def ticket_at(self, *status):
        for s in status:
            p = (self.project / ".company" / "departments" / DEPT / "tickets"
                 / s / f"{TICKET}.json")
            if p.exists():
                return json.loads(p.read_text())
        return None

    def spawn_blocks(self, agent=None):
        """List of (agent, stage, prompt) tuples recorded by the stub."""
        text = self.spawn_log.read_text() if self.spawn_log.exists() else ""
        out = []
        for b in text.split("=== SPAWN ")[1:]:
            header, _, rest = b.partition("\n")
            prompt, _, _ = rest.partition("=== END SPAWN")
            kv = dict(kv.split("=", 1) for kv in header.split()
                     if "=" in kv)
            if agent is None or kv.get("agent") == agent:
                out.append((kv.get("agent"), kv.get("stage"), prompt))
        return out


def test_tester_loop_is_bounded_when_no_tdd_red_commit(tmp_path):
    """Regression: when the tester never produces a [TDD-RED] commit, the
    TDD-RED phase must terminate. Previously the phase ran `while true`
    and `continue`d after each failed attempt: the 3-strike skip_tdd_red
    write went to the ticket file, but the loop checked a stale local read
    from before the loop, so the tester was re-spawned FOREVER (one spawn
    per ~second, unbounded API spend). Now: at most MAX_TESTER_ITER tester
    spawns per pass, the tdd_red_attempts counter persists at strike 3
    (possible only once the ticket is claimed into in-progress), the
    engineer takes over, and the ticket bounces on the gate loop until the
    loop-iteration safety valve defers it to backlog; the release bugfix
    cycle's sprint-fill then re-promotes it, so the run ends with the
    ticket back in sprint-current."""
    h = LifecycleHarness(tmp_path)
    ticket_file = (h.project / ".company" / "departments" / DEPT / "tickets"
                   / "sprint-current" / f"{TICKET}.json")
    ticket_file.write_text(_ticket_json("sprint-current"))

    r = h.run()
    assert r.returncode == 0, r.stderr

    # No [TDD-RED] commit is ever produced (the tester is a stub), so the
    # 3-strike fallback must kick in. The ticket then bounces on the gate
    # loop; the loop-iteration safety valve defers it to backlog, and the
    # release bugfix cycle's sprint-fill re-promotes it - so after the run
    # the ticket is back in sprint-current.
    ticket = h.ticket_at("sprint-current")
    assert ticket is not None, (
        f"ticket not in sprint-current after the release bugfix cycle. "
        f"backlog: {h.ticket_at('backlog')} "
        f"in-progress: {h.ticket_at('in-progress')}\n"
        f"stderr: {r.stderr}"
    )
    assert ticket.get("tdd_red_attempts") == 3, (
        "tdd_red_attempts never reached 3 - the counter writes went to a "
        "missing file (ticket not claimed into in-progress during TDD-RED)"
    )

    # The tester ran exactly 3 times IN TOTAL (all on pass 1; later passes
    # skip it via the persisted state), and every pass spawned an engineer.
    testers = h.spawn_blocks("ic-tester")
    engineers = h.spawn_blocks("ic-engineer")
    assert len(testers) == 3, f"expected 3 tester attempts, got {len(testers)}"
    assert len(engineers) == 3, (
        f"expected an engineer on every pass, got {len(engineers)}"
    )
    for _agent, _stage, prompt in testers + engineers:
        assert "--- STAGE: " in prompt
        # The prompts carry the ticket title: it is only present once the
        # ticket lives in in-progress (the scripted pre-spawn claim), where
        # TFILE points. Before the claim, a sprint-current ticket yields an
        # empty title/description in the prompt.
        assert TITLE in prompt, (
            "ticket title missing from agent prompt - ticket was not in "
            "in-progress (pre-spawn claim) when the prompt was built"
        )
    assert "--- STAGE: tdd-red" in testers[0][2]
    assert "--- STAGE: tdd-green" in engineers[0][2]

    # The gate bounces are recorded on the ticket as comments; the
    # safety-valve deferral (in-progress -> backlog) lands in the lifecycle
    # log (ticket-move only appends comments for done / in-progress moves).
    bodies = " ".join((c.get("body") or "") for c in ticket.get("comments", []))
    assert "GATE FAILURE" in bodies, "no GATE FAILURE comments on the ticket"
    assert "safety valve" in r.stderr.lower(), (
        "no safety-valve deferral in the lifecycle log - the bounce loop "
        f"was not bounded\nstderr: {r.stderr}"
    )
