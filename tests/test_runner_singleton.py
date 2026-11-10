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


def test_health_restarts_runner_into_real_log_file():
    """Static guard: company-health.sh maps the sprint-lifecycle role to
    the hyphen-named log (the old tr '-' '_' produced the orphan
    'sprint-lifecycle_log' file, where every health restart vanished)."""
    content = (REPO / "lib" / "tools" / "company-health.sh").read_text()
    assert 'sprint-lifecycle.log' in content
    assert '"$role" = "sprint-lifecycle"' in content
    # the orphan name must not appear anywhere as a target filename
    assert "sprint-lifecycle_log" not in content
