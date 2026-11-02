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
  bounce)
    if printf '%s' "$PROMPT" | grep -q -- "--- STAGE: verify"; then
      bash "$SKILL_DIR/lib/tools/ticket-bounce.sh" "$HIVE_DEPT" "$HIVE_TICKET" \
        "stub-verifier" "Stub rejection: the diff misses every numbered acceptance criterion, ignores the edge cases the ticket calls out, and touches files the ticket never asked for. Rework before re-review." \
        >> "$AGENT_SPAWN_LOG" 2>&1
    fi
    ;;
  go)
    # Clean release: approve the ticket on verify; release-ic writes a GO
    # verdict to the sprint-state verify log on release-verify.
    if printf '%s' "$PROMPT" | grep -q -- "--- STAGE: verify"; then
      bash "$SKILL_DIR/lib/tools/ticket-approve.sh" "$HIVE_DEPT" "$HIVE_TICKET" \
        "stub-verifier" "Stub approval: every numbered acceptance criterion verified, edge cases from the ticket probed, deterministic gates already green." \
        >> "$AGENT_SPAWN_LOG" 2>&1
    elif printf '%s' "$PROMPT" | grep -q -- "--- STAGE: release-verify"; then
      VLOG=$(jq -r '.release.verify_log // empty' "$COMPANY_DIR/sprints/current.json" 2>/dev/null)
      if [ -n "$VLOG" ]; then
        {
          echo "VERDICT: GO"
          echo "RATIONALE: Stub release verification: all release gates passed."
        } >> "$VLOG" 2>> "$AGENT_SPAWN_LOG"
      fi
    fi
    ;;
  nogo)
    # Release NO-GO: release-ic writes a NO-GO verdict with blockers.
    if printf '%s' "$PROMPT" | grep -q -- "--- STAGE: release-verify"; then
      VLOG=$(jq -r '.release.verify_log // empty' "$COMPANY_DIR/sprints/current.json" 2>/dev/null)
      if [ -n "$VLOG" ]; then
        {
          echo "VERDICT: NO-GO"
          echo "RATIONALE: Stub release verification: the release is not shippable."
          echo "BLOCKERS:"
          echo "- Stub blocker: release candidate failed its smoke check"
        } >> "$VLOG" 2>> "$AGENT_SPAWN_LOG"
      fi
    fi
    ;;
  badverdict)
    # Release-ic writes an unparseable verdict (wrong keyword).
    if printf '%s' "$PROMPT" | grep -q -- "--- STAGE: release-verify"; then
      VLOG=$(jq -r '.release.verify_log // empty' "$COMPANY_DIR/sprints/current.json" 2>/dev/null)
      if [ -n "$VLOG" ]; then
        {
          echo "VERDICT: MAYBE"
          echo "RATIONALE: Stub wrote a malformed verdict."
        } >> "$VLOG" 2>> "$AGENT_SPAWN_LOG"
      fi
    fi
    ;;
  happy)
    # End-to-end happy path (goal 7): tester writes the failing [TDD-RED]
    # test commit, engineer writes the [TDD-GREEN] implementation, the
    # verifier approves, the release-ic records GO.
    case "$HIVE_AGENT" in
      ic-tester)
        WT="$PROJECT_ROOT/.worktrees/$HIVE_TICKET"
        printf 'from happy_impl import works\n\ndef test_works():\n    assert works()\n' > "$WT/test_happy.py" 2>> "$AGENT_SPAWN_LOG"
        git -C "$WT" add test_happy.py 2>> "$AGENT_SPAWN_LOG"
        git -C "$WT" commit -m "[TDD-RED] $HIVE_TICKET: add failing test for works()" >> "$AGENT_SPAWN_LOG" 2>&1
        ;;
      ic-engineer)
        WT="$PROJECT_ROOT/.worktrees/$HIVE_TICKET"
        printf 'def works():\n    return True\n' > "$WT/happy_impl.py" 2>> "$AGENT_SPAWN_LOG"
        git -C "$WT" add happy_impl.py 2>> "$AGENT_SPAWN_LOG"
        git -C "$WT" commit -m "[TDD-GREEN] $HIVE_TICKET: implement works()" >> "$AGENT_SPAWN_LOG" 2>&1
        ;;
      verification-ic)
        if printf '%s' "$PROMPT" | grep -q -- "--- STAGE: verify ("; then
          bash "$SKILL_DIR/lib/tools/ticket-approve.sh" "$HIVE_DEPT" "$HIVE_TICKET" \
            "stub-verifier" "Stub approval: happy-path review - RED test added, GREEN implementation, every acceptance criterion covered, deterministic gates green." \
            >> "$AGENT_SPAWN_LOG" 2>&1
        fi
        ;;
      release-ic)
        VLOG=$(jq -r '.release.verify_log // empty' "$COMPANY_DIR/sprints/current.json" 2>/dev/null)
        if [ -n "$VLOG" ]; then
          { echo "VERDICT: GO"; echo "RATIONALE: Stub happy-path release: all release gates passed."; } >> "$VLOG" 2>> "$AGENT_SPAWN_LOG"
        fi
        ;;
    esac
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
                 failing_tests=False,
                 failing_release_tests=False, preset_release_retries=0):
        self.root = tmp_path
        self.project = tmp_path / "proj"
        self.hive = tmp_path / "hive"
        self.spawn_log = tmp_path / "spawns.log"
        self.mode = mode

        self._build_project()
        self._build_company(failing_release_tests, preset_release_retries)
        if with_code_branch:
            self._build_code_branch(failing=failing_tests)
        self._build_hive_copy()

    def _git(self, *args, cwd=None):
        r = subprocess.run(["git", *args], cwd=cwd or self.project,
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr}")
        return r

    def _build_project(self):
        self.project.mkdir(parents=True, exist_ok=True)
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

    def _build_code_branch(self, failing: bool = False):
        """Worktree + feature branch with a [TDD-RED] commit so the
        TDD-RED gate passes without spawning a tester. By default the
        committed tests pass (deterministic gates go green and the
        verifier runs); with failing=True the committed test fails, so
        the tests-pass gate bounces the ticket on every pass."""
        branch = f"feature/{TICKET}"
        wt = self.project / ".worktrees" / TICKET
        self._git("worktree", "add", "-b", branch, str(wt))
        (wt / "pytest.ini").write_text("")
        assert_line = "assert False" if failing else "assert True"
        (wt / "test_tick.py").write_text(
            f"def test_alpha():\n    {assert_line}\n"
        )
        self._git("add", "-A", cwd=wt)
        self._git("commit", "-m", f"[TDD-RED] {TICKET}: add failing test first",
                  cwd=wt)

    def _build_hive_copy(self):
        shutil.copytree(INSTALL_DIR / "lib", self.hive / "lib")
        stub = self.hive / "lib" / "core" / "agent-spawn.sh"
        stub.write_text(STUB_AGENT_SPAWN)
        stub.chmod(0o755)

    def run(self, timeout=180, extra_env=None):
        env = os.environ.copy()
        env.update({
            "HIVE_ROOT": str(self.hive),
            "AGENT_SPAWN_LOG": str(self.spawn_log),
            "STUB_MODE": self.mode,
            # Cap the main loop so the safety valve fires after a handful of
            # bounces instead of 100 - the tests only need it to trigger.
            "HIVE_MAX_LOOP_ITERATIONS": "3",
        })
        if extra_env:
            env.update(extra_env)
        r = subprocess.run(
            ["bash", str(LIFECYCLE), str(self.project)],
            env=env, capture_output=True, text=True, timeout=timeout,
        )
        self.result = r
        return r

    @property
    def sprint_archive(self):
        """Most recent archived sprint JSON (Path; may not exist)."""
        hist = self.project / ".company" / "sprints" / "history"
        files = sorted(hist.glob("sprint-*.json"))
        return files[-1] if files else hist / ".none"

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
    loop-iteration safety valve defers it to backlog. release-verify then
    finds no verdict at all - which is an EXPLICIT release failure (not a
    silent NO-GO default): the stage moves to vp-review, the sprint is
    archived with verdict_error, no retry is consumed, and the ticket
    stays in backlog."""
    h = LifecycleHarness(tmp_path)
    ticket_file = (h.project / ".company" / "departments" / DEPT / "tickets"
                   / "sprint-current" / f"{TICKET}.json")
    ticket_file.write_text(_ticket_json("sprint-current"))

    r = h.run()
    assert r.returncode == 0, r.stderr

    # No [TDD-RED] commit is ever produced (the tester is a stub), so the
    # 3-strike fallback must kick in. The ticket then bounces on the gate
    # loop; the loop-iteration safety valve defers it to backlog. With no
    # release verdict recorded, the run ends in the explicit-failure
    # path: vp-review -> archive, ticket left in backlog.
    ticket = h.ticket_at("backlog")
    assert ticket is not None, (
        f"ticket not in backlog after the safety-valve deferral. "
        f"in-progress: {h.ticket_at('in-progress')}\n"
        f"stderr: {r.stderr}"
    )
    assert "no parseable verdict" in r.stderr, (
        "release-verify did not fail explicitly on the missing verdict"
    )
    assert h.sprint_archive.exists(), "sprint was not archived"
    data = json.loads(h.sprint_archive.read_text())
    assert data.get("release", {}).get("verdict_error"), (
        "verdict_error was not recorded on the release"
    )
    assert data.get("release_retry_count", 0) == 0, (
        "an unparseable verdict must not consume a release retry"
    )
    assert ticket.get("tdd_red_attempts") == 3, (
        "tdd_red_attempts never reached 3 - the counter writes went to a "
        "missing file (ticket not claimed into in-progress during TDD-RED)"
    )
    assert not ticket.get("skip_tdd_red"), (
        "the 3-strike fallback set the skip_tdd_red flag - that flag is "
        "never cleared and zombie-ifies the ticket at the gates"
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

    # The 3-strike comment and the gate bounces are recorded on the ticket
    # as comments; the safety-valve deferral (in-progress -> backlog) lands
    # in the lifecycle log (ticket-move only appends comments for done /
    # in-progress moves).
    bodies = " ".join((c.get("body") or "") for c in ticket.get("comments", []))
    assert "Skipping the tester from now on" in bodies, (
        "no 3-strike tester-exhausted comment on the ticket"
    )
    assert "gate failure" in bodies.lower(), (
        "no gate-failure comments on the ticket"
    )
    assert "no commits" in bodies.lower(), (
        "the gate bounce did not cite the missing commits - it bounced on "
        "the skip_tdd_red zombie gate instead of evaluating the branch"
    )
    assert "safety valve" in r.stderr.lower(), (
        "no safety-valve deferral in the lifecycle log - the bounce loop "
        f"was not bounded\nstderr: {r.stderr}"
    )


def test_verifier_crash_increments_bounce_count(tmp_path):
    """Regression (release goal A.1): when the verification IC's
    agent-spawn exits non-zero (a crash, not a bounce), sprint-lifecycle
    wrote the bounce_count increment to TFILE (the in-progress path)
    BEFORE the scripted move put the ticket back there - jq on a
    not-yet-existing file, silently lost. The ticket sat at
    bounce_count=0 in in-progress, pick_next_ticket's priority-2 bounce
    picker never saw it again, and the loop advanced to release with the
    ticket abandoned. Now the move happens first, the counter lands, and
    the ticket is re-processed every loop pass until the safety valve
    defers it."""
    h = LifecycleHarness(tmp_path, mode="fail-verify", with_code_branch=True)
    ticket_file = (h.project / ".company" / "departments" / DEPT / "tickets"
                   / "sprint-current" / f"{TICKET}.json")
    ticket_file.write_text(_ticket_json("sprint-current"))

    r = h.run()
    assert r.returncode == 0, r.stderr

    # The verifier spawn runs once per loop pass. With the increment
    # lost, the ticket was never re-picked and exactly ONE crash bounce
    # happened; with it persisted, the ticket bounces on every pass until
    # the safety valve (3 loop iterations in the harness) defers it.
    n_crash_bounces = r.stderr.count("verifier failed (exit=")
    assert n_crash_bounces >= 2, (
        f"crash-bounced ticket was not re-processed - the bounce_count "
        f"increment did not persist ({n_crash_bounces} crash bounces)\n"
        f"stderr: {r.stderr}"
    )
    assert "safety valve" in r.stderr.lower(), (
        "no safety-valve deferral - the crash-bounced ticket was not "
        f"re-enqueued on later passes\nstderr: {r.stderr}"
    )
    # The ticket must not be abandoned in in-progress, and the release
    # bugfix cycle's sprint-fill re-promotes it after the deferral.
    assert h.ticket_at("in-progress") is None, (
        "crash-bounced ticket abandoned in in-progress with a zero "
        "bounce_count - the increment was lost"
    )
    # Not re-promoted in this run (the no-verdict release now fails
    # explicitly instead of running a bugfix cycle): the deferred ticket
    # stays in backlog for the next sprint.
    ticket = h.ticket_at("backlog")
    assert ticket is not None, (
        f"ticket not in backlog after deferral\nstderr: {r.stderr}"
    )


def test_verifier_bounce_cap_defers_and_resets_counter(tmp_path):
    """Regression (release goal A.1): the bounce-cap deferral must reset
    bounce_count on the ticket's NEW (backlog) location. ticket-bounce.sh
    moves the ticket to in-progress, so the cap check reads the right
    counter - but the scripted move to backlog then relocates the file,
    and a `del` on the old in-progress path is jq-on-a-missing-file. The
    stale counter survived the deferral, so the next sprint deferred the
    ticket on its very first bounce instead of giving it a fresh budget."""
    h = LifecycleHarness(tmp_path, mode="bounce", with_code_branch=True)
    ticket_file = (h.project / ".company" / "departments" / DEPT / "tickets"
                   / "sprint-current" / f"{TICKET}.json")
    ticket_file.write_text(_ticket_json("sprint-current"))

    r = h.run(extra_env={"HIVE_MAX_VERIFICATION_BOUNCES": "2"})
    assert r.returncode == 0, r.stderr

    # Two verifier bounces (bc 1 then 2) hit the cap; the third loop pass
    # finds no active tickets and advances straight to release.
    assert "bounce limit exceeded" in r.stderr.lower(), (
        "per-ticket bounce cap never fired\nstderr: {r.stderr}"
    )
    assert "safety valve" not in r.stderr.lower(), (
        "the bounce cap should defer before the loop safety valve does\n"
        f"stderr: {r.stderr}"
    )
    bodies = " ".join(
        (c.get("body") or "")
        for c in (h.ticket_at("backlog") or {}).get("comments", [])
    )
    assert bodies.count("REJECTED by verification") >= 2, (
        "expected two verifier bounces before the cap deferral"
    )

    # After the deferral + bugfix-cycle re-promotion the ticket must have
    # a FRESH bounce budget: no stale bounce_count left behind.
    ticket = h.ticket_at("backlog")
    assert ticket is not None, (
        f"ticket not in backlog after the cap deferral\nstderr: {r.stderr}"
    )
    assert ticket.get("bounce_count", 0) == 0, (
        f"stale bounce_count={ticket.get('bounce_count')} survived the "
        "backlog deferral - the reset wrote to the pre-move path"
    )


def test_gate_failure_bounce_hits_per_ticket_cap(tmp_path):
    """Release goal A.2: a ticket whose deterministic gates keep failing
    (flaky test, broken branch, skip_tdd_red rework loop) bounced only
    against the GLOBAL MAX_LOOP_ITERATIONS safety valve, so one bad
    ticket could burn up to 100 loop iterations and starve the rest of
    the sprint. The gate-failure branch now applies the same per-ticket
    bounce cap (MAX_VERIFICATION_BOUNCES) as the verifier-bounce path."""
    h = LifecycleHarness(tmp_path, mode="noop", with_code_branch=True,
                         failing_tests=True)
    ticket_file = (h.project / ".company" / "departments" / DEPT / "tickets"
                   / "sprint-current" / f"{TICKET}.json")
    ticket_file.write_text(_ticket_json("sprint-current"))

    r = h.run(extra_env={"HIVE_MAX_VERIFICATION_BOUNCES": "2"})
    assert r.returncode == 0, r.stderr

    # The cap must fire on the 2nd gate bounce - NOT the loop safety
    # valve (harness sets 3 loop iterations; the old code gate-bounced on
    # passes 1-3 and only then deferred via the valve).
    assert "bounce limit exceeded" in r.stderr, (
        "per-ticket bounce cap never fired on the gate-failure path"
    )
    assert "safety valve" not in r.stderr.lower(), (
        "gate-failure ticket looped to the safety valve instead of the "
        "per-ticket cap"
    )
    n_gate_bounces = r.stderr.count("gates failed (rc=")
    assert n_gate_bounces == 2, (
        f"expected exactly 2 gate bounces before cap deferral, saw "
        f"{n_gate_bounces}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# A.3 — Release pipeline terminal paths
#
# The release pipeline (build-and-test → release-verify → publish →
# vp-review → archive) is ~900 lines' worth of state machine that the
# earlier tests only exercised incidentally (no verdict → NO-GO). These
# scenario tests drive each terminal path end-to-end: clean GO release,
# NO-GO retry, retry-cap → vp-review. (The safety-valve deferral path is
# covered by the tester-loop / gate-bounce tests above.)
# ─────────────────────────────────────────────────────────────────────────────

def test_clean_release_terminal_path(tmp_path):
    """Release goal A.3: ticket work verified + approved, release
    verdict GO, publish attempted, sprint archived and a new sprint
    started. The whole terminal path, start to finish."""
    h = LifecycleHarness(tmp_path, mode="go", with_code_branch=True)
    ticket_file = (h.project / ".company" / "departments" / DEPT / "tickets"
                   / "sprint-current" / f"{TICKET}.json")
    ticket_file.write_text(_ticket_json("sprint-current"))

    r = h.run()
    assert r.returncode == 0, r.stderr

    # The ticket reached done through the verifier's atomic approval; the
    # sprint-end sweep (sprint-archive-done.sh) then moves done -> archive.
    ticket = h.ticket_at("done", "archive")
    assert ticket is not None, (
        f"ticket did not reach done/archive\nstderr: {r.stderr[-800:]}"
    )
    assert ticket.get("status") == "done"
    assert ticket.get("verified_by") == "stub-verifier"
    assert ticket.get("gates_passed") is True

    # B.6: the security-clean gate skipped (no audit tool in the sandbox)
    # and the skip must be explicit in the gate record - a detail that
    # names the stack and what to install, never a bare "no tool" that
    # reads like a pass.
    gates_file = (h.project / ".company" / "departments" / DEPT
                  / "components" / TICKET / "gates.json")
    assert gates_file.exists(), "gates.json was not written"
    gates = json.loads(gates_file.read_text())["gates"]
    sec = gates.get("security-clean", {})
    assert sec.get("status") == "skip", (
        f"expected security-clean to skip in the sandbox, got "
        f"{sec.get('status')}"
    )
    assert "NOT scanned" in sec.get("detail", ""), (
        f"skip detail does not say the audit was skipped: "
        f"{sec.get('detail')}"
    )
    assert "install:" in sec.get("detail", ""), (
        f"skip detail does not say what to install: {sec.get('detail')}"
    )

    # GO was parsed and the sprint archived with the release record.
    assert h.sprint_archive.exists(), (
        f"sprint was not archived\nstderr: {r.stderr[-800:]}"
    )
    data = json.loads(h.sprint_archive.read_text())
    assert data.get("stage") == "complete"
    rel = data.get("release", {})
    assert rel.get("verdict") == "GO"
    # No tarball exists in the sandbox, so the publish attempt fails
    # cleanly - the stage still records that it was attempted.
    assert rel.get("publish_status") in ("published", "failed")
    assert "release published" in r.stderr or "release-publish FAILED" in r.stderr

    # A fresh sprint must have started for the next round.
    current = json.loads(
        (h.project / ".company" / "sprints" / "current.json").read_text()
    )
    assert current.get("stage") == "ceo-plan"
    assert current.get("sprint_id") != data.get("sprint_id")


def test_no_go_retry_terminal_path(tmp_path):
    """Release goal A.3: a NO-GO verdict consumes one release retry and
    re-enters the bugfix cycle (fresh planning + sprint-fill) instead of
    archiving the sprint."""
    h = LifecycleHarness(tmp_path, mode="nogo")  # no tickets: pure release path

    r = h.run()
    assert r.returncode == 0, r.stderr

    # NO-GO was parsed and the bugfix cycle re-entered.
    assert "release-verify NO-GO" in r.stderr, (
        f"NO-GO verdict not processed\nstderr: {r.stderr[-800:]}"
    )
    assert "running bugfix cycle (attempt 1)" in r.stderr

    # The retry was counted and the sprint re-armed for another pass.
    state = json.loads((h.project / ".company" / "sprints" / "current.json").read_text())
    assert state.get("release_retry_count") == 1
    assert state.get("stage") == "tdd-red"
    assert not h.sprint_archive.exists(), (
        "a NO-GO sprint must not be archived on the first retry"
    )


def test_retry_cap_terminal_path(tmp_path):
    """Release goal A.3: once the release retry cap is exceeded the
    lifecycle stops retrying and defers to vp-review with the failure
    recorded (publish_status=failed) - the operator / VPs surface the
    blocker instead of the loop churning forever."""
    h = LifecycleHarness(tmp_path, mode="nogo", preset_release_retries=3)
    # HIVE_MAX_RELEASE_RETRIES defaults to 3; the next retry would be 4.

    r = h.run()
    assert r.returncode == 0, r.stderr

    assert "retry cap exceeded" in r.stderr, (
        f"retry cap not enforced\nstderr: {r.stderr[-800:]}"
    )
    # After the cap the run still finishes through vp-review and the
    # sprint is archived with the failure recorded; a fresh sprint starts.
    assert h.sprint_archive.exists(), (
        f"sprint was not archived\nstderr: {r.stderr[-800:]}"
    )
    data = json.loads(h.sprint_archive.read_text())
    assert data.get("release_retry_count") == 4
    assert data.get("release", {}).get("publish_status") == "failed"
    assert data.get("release", {}).get("verdict") == "NO-GO"
    current = json.loads(
        (h.project / ".company" / "sprints" / "current.json").read_text()
    )
    assert current.get("stage") == "ceo-plan"
    assert current.get("sprint_id") != data.get("sprint_id")


def test_skip_tdd_red_honoured_and_cleared_at_done(tmp_path):
    """Release goal A.4: skip_tdd_red=true means the tester is skipped
    (the engineer works the ticket directly) when no [TDD-RED] commit
    exists yet, and when the ticket eventually reaches done the flag
    must be CLEARED - otherwise a re-promoted ticket skips the RED
    stage again in the next sprint."""
    # Part 1: the flag is honoured - no tester spawns at all.
    h1 = LifecycleHarness(tmp_path / "p1", mode="noop")
    tf1 = (h1.project / ".company" / "departments" / DEPT / "tickets"
           / "sprint-current" / f"{TICKET}.json")
    d1 = json.loads(_ticket_json("sprint-current"))
    d1["skip_tdd_red"] = True
    tf1.write_text(json.dumps(d1, indent=2))
    r1 = h1.run()
    assert r1.returncode == 0, r1.stderr
    assert "skipping tester (skip_tdd_red=true" in r1.stderr, (
        f"skip_tdd_red was not honoured\nstderr: {r1.stderr[:400]}"
    )
    assert "spawning ic-cycle-tester" not in r1.stderr, (
        "tester was spawned despite skip_tdd_red=true"
    )

    # Part 2: the flag is cleared when the ticket reaches done.
    h2 = LifecycleHarness(tmp_path / "p2", mode="go", with_code_branch=True)
    tf2 = (h2.project / ".company" / "departments" / DEPT / "tickets"
           / "sprint-current" / f"{TICKET}.json")
    d2 = json.loads(_ticket_json("sprint-current"))
    d2["skip_tdd_red"] = True
    tf2.write_text(json.dumps(d2, indent=2))
    r2 = h2.run()
    assert r2.returncode == 0, r2.stderr
    ticket = h2.ticket_at("done", "archive")
    assert ticket is not None, (
        f"ticket did not reach done\nstderr: {r2.stderr[-400:]}"
    )
    assert "skip_tdd_red" not in ticket, (
        "skip_tdd_red survived the done move - it would skip the RED "
        "stage again on a future re-promotion"
    )

def test_unparseable_release_verdict_fails_explicitly(tmp_path):
    """Release goal B.5: a malformed release-ic verdict (wrong keyword)
    must fail the release EXPLICITLY - no silent NO-GO default, no retry
    consumed, verdict_error + publish_status=failed recorded, stage
    vp-review so the operator sees it."""
    h = LifecycleHarness(tmp_path, mode="badverdict")
    r = h.run()
    assert r.returncode == 0, r.stderr

    assert "no parseable verdict" in r.stderr, (
        f"malformed verdict was not surfaced\nstderr: {r.stderr[-500:]}"
    )
    assert "NO-GO" not in r.stderr.replace("no parseable", ""), (
        "the malformed verdict was treated as a NO-GO decision"
    )
    assert h.sprint_archive.exists(), "sprint was not archived"
    data = json.loads(h.sprint_archive.read_text())
    assert data.get("release", {}).get("verdict_error"), (
        "verdict_error missing from the archived release"
    )
    assert data.get("release", {}).get("publish_status") == "failed"
    assert data.get("release", {}).get("verdict") is None
    assert data.get("release_retry_count", 0) == 0
    current = json.loads(
        (h.project / ".company" / "sprints" / "current.json").read_text()
    )
    assert current.get("stage") == "ceo-plan"

def _seed_current_state(h, retries=0):
    """Write .company/sprints/current.json with the given retry count so
    the release build fields can be seeded before h.run()."""
    state = {
        "sprint_id": "sprint-preset",
        "started_at": "2026-01-01T00:00:00Z",
        "stage": "ceo-plan",
        "stage_started_at": "2026-01-01T00:00:00Z",
        "loop_iteration": 0,
        "release_retry_count": retries,
        "tickets_in_sprint": [],
        "ceo_direction_path": None,
        "release": {
            "version": None, "test_log": None, "verify_log": None,
            "tarball": None, "sha256": None,
            "publish_status": "pending",
        },
    }
    (h.project / ".company" / "sprints" / "current.json").write_text(
        json.dumps(state, indent=2))


def _seed_release_build(h):
    """Mark the sprint state as having a real build (version + tarball +
    sha256 all present on disk) so release-publish-runner.sh passes its
    prechecks and WOULD invoke the publisher."""
    tarball = h.project / "release.tar.gz"
    tarball.write_bytes(b"fake-tarball")
    sha = h.project / "release.tar.gz.sha256"
    sha.write_text("0" * 64 + "  release.tar.gz\n")
    f = h.project / ".company" / "sprints" / "current.json"
    state = json.loads(f.read_text())
    state["release"]["version"] = "9.9.9-test"
    state["release"]["tarball"] = str(tarball)
    state["release"]["sha256"] = str(sha)
    f.write_text(json.dumps(state, indent=2))


def _fake_publisher(h):
    """Point the publish runner at a recording fake publisher via
    HIVE_CONFIG. Returns (config_path, marker_path); the marker appears
    only if the publisher is actually invoked."""
    publisher = h.root / "fake-publisher.sh"
    marker = h.root / "published.marker"
    publisher.write_text(
        f"#!/usr/bin/env bash\n"
        f"echo \"PUBLISHED $*\" >> \"{marker}\"\n"
        f"exit 0\n"
    )
    publisher.chmod(0o755)
    cfg = h.root / "config.toml"
    cfg.write_text(f'[release]\npublisher_script = "{publisher}"\n')
    return str(cfg), marker


def _blocker_tickets_in_it_backlog(h):
    """RELEASE-BLOCKER tickets currently sitting in the it dept backlog."""
    out = []
    d = h.project / ".company" / "departments" / "it" / "tickets" / "backlog"
    if d.exists():
        for f in d.glob("*.json"):
            data = json.loads(f.read_text())
            if "RELEASE-BLOCKER" in (data.get("title") or ""):
                out.append(data)
    return out


def test_release_retry_cap_never_publishes_and_files_blocker_ticket(tmp_path):
    """A release that is ruled NO-GO past the retry cap must NOT fall
    through to release-publish — with a real tarball in state the old
    code handed it to the publisher anyway, and a successful publish
    overwrote publish_status back to 'published'. It must also file a
    visible RELEASE-BLOCKER ticket (the old code 'moved' a ticket id no
    code path ever creates, so the blockage was invisible to planning)."""
    h = LifecycleHarness(tmp_path, mode="nogo", preset_release_retries=3)
    # The retry cap is 3; the next NO-GO is retry 4 -> cap exceeded.
    _seed_release_build(h)
    cfg, marker = _fake_publisher(h)

    r = h.run(extra_env={"HIVE_CONFIG": cfg})
    assert r.returncode == 0, r.stderr
    assert "retry cap exceeded" in r.stderr, r.stderr[-800:]

    # The publisher must NEVER be invoked for a blocked release.
    assert not marker.exists(), (
        "release-publish ran for a retry-capped (NO-GO) release"
    )

    # The archived sprint records the failure, not a phantom publish.
    assert h.sprint_archive.exists(), r.stderr[-800:]
    data = json.loads(h.sprint_archive.read_text())
    assert data.get("release", {}).get("publish_status") == "failed"
    assert data.get("release", {}).get("verdict") == "NO-GO"

    # The blockage is visible to the next sprint's planning.
    blockers = _blocker_tickets_in_it_backlog(h)
    assert len(blockers) == 1, (
        f"expected exactly one RELEASE-BLOCKER ticket in the it backlog, "
        f"got {len(blockers)}"
    )


def test_unparseable_verdict_never_publishes(tmp_path):
    """B.5 follow-up: an unparseable release-ic verdict marks the release
    failed and goes to vp-review — but the old code then fell through to
    release-publish. With a real tarball + working publisher in place, a
    build that could NOT be verified still got published (publish_status
    flipped failed -> published). Unverifiable releases must never ship."""
    h = LifecycleHarness(tmp_path, mode="badverdict")
    _seed_current_state(h, retries=0)
    _seed_release_build(h)
    cfg, marker = _fake_publisher(h)

    r = h.run(extra_env={"HIVE_CONFIG": cfg})
    assert r.returncode == 0, r.stderr
    assert "no parseable verdict" in r.stderr, r.stderr[-800:]

    assert not marker.exists(), (
        "release-publish ran despite an unparseable (unverifiable) verdict"
    )

    assert h.sprint_archive.exists(), r.stderr[-800:]
    data = json.loads(h.sprint_archive.read_text())
    assert data.get("release", {}).get("publish_status") == "failed"
    assert data.get("release", {}).get("verdict_error")
    assert data.get("release_retry_count", 0) == 0


def test_build_failure_retry_cap_skips_release_verify(tmp_path):
    """When build-and-test fails past the retry cap there is no build to
    review: the release-ic must not spawn at all (the old code fell
    through and spawned it on a phantom build, whose empty verdict then
    logged a spurious 'no parseable verdict' failure), and publish must
    be skipped too. The blockage is still filed as a ticket."""
    h = LifecycleHarness(tmp_path, mode="noop",
                         failing_release_tests=True,
                         preset_release_retries=3)
    _seed_release_build(h)
    cfg, marker = _fake_publisher(h)

    r = h.run(extra_env={"HIVE_CONFIG": cfg})
    assert r.returncode == 0, r.stderr
    assert "retry cap exceeded" in r.stderr, r.stderr[-800:]

    assert h.spawn_blocks("release-ic") == [], (
        "release-ic spawned to review a build that never existed"
    )
    assert not marker.exists(), "release-publish ran for a failed build"

    assert h.sprint_archive.exists(), r.stderr[-800:]
    data = json.loads(h.sprint_archive.read_text())
    assert data.get("release", {}).get("publish_status") == "failed"
    assert len(_blocker_tickets_in_it_backlog(h)) == 1


def test_stage_machine_tracks_each_stage(tmp_path):
    """sprint-state's .stage is the dispatch contract: ticket-move.sh keys
    the VP rejection window off it (vp-plan/vp-review only), and startup
    uses it to decide re-plan vs resume. The lifecycle used to leave the
    stage at 'ceo-plan' through the whole first pass — so VP rejections
    during their own planning stage were denied, and every relaunch
    re-ran CEO + VP planning on an already-planned sprint. Each stage must
    be recorded as it runs."""
    h = LifecycleHarness(tmp_path, mode="noop")  # no tickets: plan -> build -> release
    r = h.run()
    assert r.returncode == 0, r.stderr
    spawns = h.spawn_blocks()
    by_agent = {}
    for agent, stage, _ in spawns:
        by_agent.setdefault(agent, []).append(stage)

    assert by_agent.get("ceo", ["?"])[0] == "ceo-plan", by_agent.get("ceo")
    vp_stages = [s for a, ss in by_agent.items() if a.startswith("vp-") for s in ss]
    assert "vp-plan" in vp_stages, (
        f"VPs did not spawn at stage vp-plan (rejection window is broken): {vp_stages}"
    )
    assert "vp-review" in vp_stages, vp_stages
    rel = by_agent.get("release-ic")
    assert rel and rel[0] == "release-verify", rel


def test_relance_on_planned_sprint_skips_planning(tmp_path):
    """A relaunch on an in-progress sprint (stage != ceo-plan — e.g. after
    the bugfix cycle's exit 0, which already re-planned inline, or after a
    crash) must resume the ticket loop, NOT re-run CEO + VP planning: the
    old code double-spent the CEO and every VP on every bugfix cycle and
    risked duplicate ticket filings."""
    h = LifecycleHarness(tmp_path, mode="noop")
    _seed_current_state(h, retries=0)
    f = h.project / ".company" / "sprints" / "current.json"
    state = json.loads(f.read_text())
    state["stage"] = "tdd-red"  # as a crash-relaunch / bugfix relaunch would see it
    f.write_text(json.dumps(state, indent=2))

    r = h.run()
    assert r.returncode == 0, r.stderr
    spawns = h.spawn_blocks()
    assert not any(a == "ceo" for a, _, _ in spawns), (
        "CEO re-planned an already-planned sprint on relaunch"
    )
    assert not any(a.startswith("vp-") and s == "vp-plan"
                   for a, s, _ in spawns), (
        "VPs re-ran planning on an already-planned sprint"
    )
    # The release path still ran to completion and the sprint archived.
    assert h.sprint_archive.exists()


def test_full_happy_path_forward_flow(tmp_path):
    """Release goal 7: one green-path run that drives a full ticket
    forward — create -> claim -> TDD-RED (tester writes the failing test
    commit) -> TDD-GREEN (engineer writes the implementation) -> gates
    pass -> verify-approve -> done -> release GO -> archive. The existing
    scenario tests mostly cover failure/bounce corners; this proves the
    intended forward path works start to finish."""
    h = LifecycleHarness(tmp_path, mode="happy")
    # Test infrastructure committed to main so BOTH the worktree gates
    # (pytest in the branch) and the release build-and-test (pytest at the
    # project root) find a framework with passing tests.
    (h.project / "pytest.ini").write_text("")
    (h.project / "test_root.py").write_text("def test_ok():\n    assert True\n")
    h._git("add", "pytest.ini", "test_root.py")
    h._git("commit", "-m", "test infrastructure")

    ticket_file = (h.project / ".company" / "departments" / DEPT / "tickets"
                   / "sprint-current" / f"{TICKET}.json")
    ticket_file.write_text(_ticket_json("sprint-current"))

    r = h.run()
    assert r.returncode == 0, r.stderr

    # Exactly one tester pass and one engineer pass - no bounce loop.
    testers = h.spawn_blocks("ic-tester")
    engineers = h.spawn_blocks("ic-engineer")
    assert len(testers) == 1, f"expected 1 tester spawn, got {len(testers)}"
    assert len(engineers) == 1, (
        f"expected 1 engineer spawn, got {len(engineers)}"
    )

    # The branch carries a RED commit before a GREEN commit.
    wt = h.project / ".worktrees" / TICKET
    log = subprocess.run(
        ["git", "-C", str(wt), "log", "main..HEAD", "--format=%s"],
        capture_output=True, text=True, timeout=30,
    ).stdout
    red_i = log.find("[TDD-RED]")
    green_i = log.find("[TDD-GREEN]")
    assert red_i != -1, "no [TDD-RED] commit on the branch"
    assert green_i != -1, "no [TDD-GREEN] commit on the branch"

    # The ticket went done through the verifier's approval (then the
    # sprint-end sweep archives it).
    ticket = h.ticket_at("done", "archive")
    assert ticket is not None, f"ticket did not reach done\nstderr: {r.stderr[-600:]}"
    assert ticket.get("status") == "done"
    assert ticket.get("verified_by") == "stub-verifier"
    assert ticket.get("gates_passed") is True

    # Gates actually ran and passed (tests-pass is real: pytest in the
    # worktree); security-clean skipped with a visible detail.
    gates_file = (h.project / ".company" / "departments" / DEPT / "components"
                  / TICKET / "gates.json")
    assert gates_file.exists(), "gates.json missing - gates never ran"
    gates = json.loads(gates_file.read_text())["gates"]
    assert gates["tests-pass"]["status"] == "pass"
    assert gates["security-clean"]["status"] == "skip"
    assert "NOT scanned" in gates["security-clean"]["detail"]

    # The release ran clean: GO verdict, sprint archived.
    assert h.sprint_archive.exists(), (
        f"sprint was not archived\nstderr: {r.stderr[-600:]}"
    )
    data = json.loads(h.sprint_archive.read_text())
    assert data.get("release", {}).get("verdict") == "GO"

    # No verifier bounces anywhere along the way.
    assert "bounced by verifier" not in r.stderr, (
        "the happy path bounced - it should have gone straight through"
    )
