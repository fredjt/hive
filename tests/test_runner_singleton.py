"""Item 6: the runner singleton is explicit, and its logs go to one file.

The 2026-11-06 field run ran with TWO sprint-lifecycle runners at once
(an old 0.8.2 runner nobody had stopped, plus the 0.9.0 one) - they
interleaved sprints and rounds. The old script hid this: it silently
SIGTERM/KILLed a live prior runner and carried on. Now a live prior
runner is REFUSED (exit 1, explicit message) unless HIVE_RUNNER_FORCE=1;
a heartbeat file makes a live runner distinguishable from a crashed one;
and health restarts append to the runner's REAL log filename (the old
tr '-' '_' produced 'sprint-lifecycle_log', an orphan file).
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIFECYCLE = REPO / "lib" / "tools" / "sprint-lifecycle.sh"

# Reuse the stubbed-spawn harness from the lifecycle tests.
sys.path.insert(0, str(REPO / "tests"))
from test_sprint_lifecycle import LifecycleHarness  # noqa: E402


def _spawn_runner(h, env_extra=None):
    env = os.environ.copy()
    env.update({
        "HIVE_ROOT": str(h.hive),
        "AGENT_SPAWN_LOG": str(h.spawn_log),
        "STUB_MODE": "noop",
        "HIVE_MAX_LOOP_ITERATIONS": "3",
    })
    if env_extra:
        env.update(env_extra)
    return subprocess.Popen(
        ["bash", str(LIFECYCLE), str(h.project)], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_for_runner_files(h, timeout=60):
    pidfile = h.project / ".company" / "rounds" / "runner.pid"
    hb = h.project / ".company" / "rounds" / "runner-heartbeat"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pidfile.exists() and hb.exists():
            return
        time.sleep(0.2)
    raise AssertionError("runner pidfile/heartbeat never appeared")


def test_second_runner_is_refused(tmp_path):
    h = LifecycleHarness(tmp_path, mode="noop")
    p1 = _spawn_runner(h)
    try:
        _wait_for_runner_files(h)
        pidfile = h.project / ".company" / "rounds" / "runner.pid"
        pid1 = pidfile.read_text().strip()
        assert str(p1.pid) in pidfile.read_text(), \
            f"pidfile {pidfile.read_text()!r} != runner pid {p1.pid}"

        # the heartbeat carries the live pid (a reader can distinguish
        # live from crashed without racing the pidfile)
        hb = json.loads((h.project / ".company" / "rounds"
                         / "runner-heartbeat").read_text())
        assert hb.get("pid") == int(pid1), hb

        env = os.environ.copy()
        env["HIVE_ROOT"] = str(h.hive)
        r2 = subprocess.run(["bash", str(LIFECYCLE), str(h.project)],
                            env=env, capture_output=True, text=True,
                            timeout=30)
        assert r2.returncode != 0, "second runner must be refused"
        assert "Refusing to start" in r2.stderr, r2.stderr
        assert "HIVE_RUNNER_FORCE" in r2.stderr, r2.stderr
        # the first runner is still alive - it was NOT killed
        assert p1.poll() is None, "refusal must not kill the live runner"
    finally:
        p1.terminate()
        try:
            p1.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p1.kill()


def test_forced_takeover_is_deliberate(tmp_path):
    h = LifecycleHarness(tmp_path, mode="noop")
    p1 = _spawn_runner(h)
    p2 = None
    try:
        _wait_for_runner_files(h)
        pid1 = (h.project / ".company" / "rounds"
                / "runner.pid").read_text().strip()
        p2 = _spawn_runner(h, env_extra={"HIVE_RUNNER_FORCE": "1"})
        deadline = time.time() + 30
        pidfile = h.project / ".company" / "rounds" / "runner.pid"
        while time.time() < deadline:
            if p1.poll() is not None and str(p2.pid) in pidfile.read_text():
                break
            time.sleep(0.2)
        assert p1.poll() is not None, \
            "HIVE_RUNNER_FORCE=1 must terminate the prior runner"
        assert str(p2.pid) in pidfile.read_text(), \
            f"pidfile {pidfile.read_text()!r} != forced runner pid {p2.pid}"
    finally:
        for p in (p1, p2):
            if p is not None:
                p.terminate()
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()


def test_forced_takeover_pid_never_empty_under_hammer(tmp_path):
    """T3 (0.15) — the empty-pidfile window is gone.

    N=20 forced takeovers in ONE company, with the takeover poll
    hammered (10 ms): every read of runner.pid must be a pid — never
    empty, never partial — and every heartbeat read must parse as
    complete JSON. Red before the hardening: the 0.14 design did
    `exec 9>"$RUNNER_PID"` (the file is created truncated) and wrote
    the pid only after the flock — a poller landing in the window read
    an empty file (it flaked once in one full -n 8 run and passed 3/3
    in serial isolation — the signature of a load-dependent race, not
    a logic failure).
    """
    h = LifecycleHarness(tmp_path, mode="noop")
    pidfile = h.project / ".company" / "rounds" / "runner.pid"
    hbfile = h.project / ".company" / "rounds" / "runner-heartbeat"
    prev = None
    total_reads = 0
    try:
        for i in range(20):
            p = _spawn_runner(
                h, {"HIVE_RUNNER_FORCE": "1"} if prev is not None else None)
            deadline = time.time() + 30
            committed = False
            while not committed:
                if pidfile.exists():
                    content = pidfile.read_text()
                    total_reads += 1
                    assert content.strip(), (
                        f"takeover {i}: runner.pid read EMPTY — the "
                        "window between truncate and write (the 0.14 "
                        "exec-9 design)")
                    committed = content.strip() == str(p.pid)
                if hbfile.exists():
                    hb_text = hbfile.read_text()
                    if hb_text.strip():
                        hb = json.loads(hb_text)  # partial line = crash
                        assert isinstance(hb.get("pid"), int), hb
                if time.time() > deadline:
                    raise AssertionError(
                        f"takeover {i}: pid {p.pid} never committed "
                        f"to {pidfile}")
                if prev is not None:
                    # T1 (0.16): reap the dead prior runner INSIDE the poll
                    # window. kill -0 succeeds on a zombie as well as a live
                    # process, and this test's parent (pytest) only reaped
                    # the corpse AFTER the commit (the assertion below) —
                    # so the production death check saw a zombie and every
                    # takeover ate the full cap. A healthy supervisor
                    # reaps promptly (bash reaps background jobs at its
                    # next iteration; an orphaned runner is re-parented to
                    # init and reaped immediately): the hammer now models
                    # that. The N=20 confidence, the 10 ms observer, and
                    # every assertion are unchanged — the parent model
                    # was the cost, not the pin.
                    prev.poll()
                time.sleep(0.01)
            if prev is not None:
                assert prev.poll() is not None, (
                    f"takeover {i}: the prior runner must be terminated")
            prev = p
    finally:
        for p in ([prev] if prev is not None else []):
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
    assert total_reads > 0, "the hammer never read the pidfile"


def test_forced_takeover_commits_within_cap_when_parent_holds_zombie(
        tmp_path):
    """T1 (0.16) — the production worst case, pinned ONCE, not twenty.

    The hammer models a REAPING supervisor (prev.poll() inside the
    window — bash reaps background jobs, an orphaned runner is
    re-parented to init and reaped immediately). The realistic worst
    case is a parent that does NOT reap: the dead prior stays a
    ZOMBIE, kill -0 succeeds on it (a zombie is not "dead" to kill -0),
    and the production death check must burn the FULL cap before the
    KILL backstop and the atomic pid commit. The hammer's N=20 would
    multiply that payment twenty times if the parent ever held a
    zombie; this test pays it exactly once and pins the end-to-end
    worst case: the new pid commits to runner.pid inside the cap
    (1s death check + 0.3s KILL backstop + commit — well under the
    ~8s allowance).
    """
    h = LifecycleHarness(tmp_path, mode="noop")
    p1 = _spawn_runner(h)
    p2 = None
    try:
        _wait_for_runner_files(h)
        pidfile = h.project / ".company" / "rounds" / "runner.pid"
        assert pidfile.read_text().strip() == str(p1.pid), \
            "the prior runner must own the pidfile before the takeover"
        t0 = time.time()
        p2 = _spawn_runner(h, env_extra={"HIVE_RUNNER_FORCE": "1"})
        deadline = time.time() + 15
        committed = False
        while time.time() < deadline:
            # NOTE: deliberately NO p1.poll() in this window — holding
            # the zombie IS the test (the unreaped worst case).
            if pidfile.read_text().strip() == str(p2.pid):
                committed = True
                break
            time.sleep(0.01)
        committed_in = time.time() - t0
        assert committed, (
            "the takeover must commit to runner.pid while the parent "
            "holds the prior's zombie")
        assert p1.poll() is not None, \
            "HIVE_RUNNER_FORCE=1 must terminate the prior runner"
        assert committed_in < 8, (
            "the unreaped worst case must stay inside the cap (1s death "
            f"check + 0.3s KILL backstop + commit); took {committed_in:.2f}s")
    finally:
        for p in (p1, p2):
            if p is not None:
                p.terminate()
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()


def test_takeover_tick_granularity_is_0_1s():
    """T1 (0.16) — the tick guard, next to the pin it protects.

    No sleep coarser than 0.1s between the kill -TERM and the KILL
    backstop in the forced-takeover section. The 0.15 5s cap
    (sleep 0.5 x 10) cost every takeover a full second on a reaping
    supervisor and the whole cap when the prior was a zombie —
    103.03s of the 266.72s suite wall through the hammer. The cap is
    0.1s x 10 (1s) now; this guard makes the regression loud. Red
    while the 0.5s literal exists.
    """
    content = LIFECYCLE.read_text()
    i_term = content.find('kill -TERM "$PRIOR_PID"')
    i_kill = content.find('kill -KILL "$PRIOR_PID"')
    assert i_term != -1 and i_kill != -1, \
        "the forced-takeover TERM/KILL section is missing from " \
        "lib/tools/sprint-lifecycle.sh"
    assert i_term < i_kill, \
        "the TERM must precede the KILL backstop in the takeover section"
    section = content[i_term:i_kill]
    sleeps = re.findall(r"sleep\s+([0-9.]+)", section)
    assert sleeps, \
        "no sleep tick between the TERM and the KILL backstop — the " \
        "death check must poll, not fire-and-forget"
    coarse = [s for s in sleeps if float(s) > 0.1 + 1e-9]
    assert not coarse, (
        f"takeover death check has a tick coarser than 0.1s: {coarse} — "
        "every such tick is a cost on EVERY takeover (the 0.15 5s cap "
        "was 38.7% of the suite wall through the hammer)")


def test_health_restarts_runner_into_real_log_file():
    """Static guard: company-health.sh maps the sprint-lifecycle role to
    the hyphen-named log (the old tr '-' '_' produced the orphan
    'sprint-lifecycle_log' file, where every health restart vanished)."""
    content = (REPO / "lib" / "tools" / "company-health.sh").read_text()
    assert 'sprint-lifecycle.log' in content
    assert '"$role" = "sprint-lifecycle"' in content
    # the orphan name must not appear anywhere as a target filename
    assert "sprint-lifecycle_log" not in content
