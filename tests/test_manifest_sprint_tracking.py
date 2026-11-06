"""E1: the manifest tracks the sprint.

Only init-manifest.sh wrote manifest.json — after three sprints it still
said status "intake", cycle_count 0, current_sprint null. The sprint-state
writers now update it on the same atomic path as the rest of the sprint
state (flock + tmp + rename): current_sprint when a sprint starts,
cycle_count + 1 on archive, idle_mode in A1's idle flow (covered by
tests/test_idle_sprint.py).
"""
import json
import os
import shlex
import stat
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SS = REPO / "lib" / "tools" / "sprint-state.sh"
INIT_MANIFEST = REPO / "lib" / "tools" / "init-manifest.sh"


def _mk_company(tmp_path, name="manifest-proj"):
    cd = tmp_path / ".company"
    cd.mkdir()
    r = subprocess.run(["bash", str(INIT_MANIFEST), name, "true"],
                       capture_output=True, text=True, timeout=10,
                       env={**os.environ, "COMPANY_DIR": str(cd)})
    assert r.returncode == 0, r.stderr
    m = json.loads((cd / "manifest.json").read_text())
    assert m["current_sprint"] is None and m["cycle_count"] == 0
    return cd


def _ss(cd, code):
    """Run `code` in a bash shell that has sprint-state.sh sourced."""
    script = ("export COMPANY_DIR=" + shlex.quote(str(cd)) + "\n"
              + ". " + shlex.quote(str(SS)) + "\n"
              + code)
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=30)


def test_sprint_start_syncs_manifest_current_sprint(tmp_path):
    cd = _mk_company(tmp_path)
    r = _ss(cd, "ss_init; jq -r .sprint_id " + shlex.quote(str(cd / "sprints" / "current.json")) + "")
    assert r.returncode == 0, r.stderr
    sid = r.stdout.strip()
    m = json.loads((cd / "manifest.json").read_text())
    assert m["current_sprint"] == sid
    state = json.loads((cd / "sprints" / "current.json").read_text())
    assert m["current_sprint"] == state["sprint_id"]
    # A second ss_init (runner relaunch) must not clobber the live sprint.
    r2 = _ss(cd, "ss_init; jq -r .sprint_id " + shlex.quote(str(cd / "sprints" / "current.json")) + "")
    assert r2.stdout.strip() == sid
    m2 = json.loads((cd / "manifest.json").read_text())
    assert m2["current_sprint"] == sid


def test_archive_increments_cycle_count_and_rolls_sprint(tmp_path):
    cd = _mk_company(tmp_path)
    r = _ss(cd, "ss_init; jq -r .sprint_id " + shlex.quote(str(cd / "sprints" / "current.json")) + "")
    old_sid = r.stdout.strip()
    r = _ss(cd, "ss_archive_and_start_new; jq -r .sprint_id " + shlex.quote(str(cd / "sprints" / "current.json")) + "")
    assert r.returncode == 0, r.stderr
    new_sid = r.stdout.strip()
    assert new_sid != old_sid
    assert (cd / "sprints" / "history" / f"{old_sid}.json").exists()
    m = json.loads((cd / "manifest.json").read_text())
    assert m["cycle_count"] == 1, m
    assert m["current_sprint"] == new_sid, m


def test_killed_writer_never_leaves_torn_manifest(tmp_path):
    """kill -9 the manifest writer MID-UPDATE (jq slowed with a wrapper)
    -> the manifest still parses: writes go tmp + rename, never in place."""
    cd = _mk_company(tmp_path)
    real_jq = subprocess.run(["which", "jq"], capture_output=True,
                             text=True).stdout.strip()
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    wrapper = fakebin / "jq"
    wrapper.write_text(f'#!/usr/bin/env bash\nsleep 0.3\nexec {real_jq} "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    before = (cd / "manifest.json").read_text()

    script = ("export COMPANY_DIR=" + shlex.quote(str(cd)) + "\n"
              + ". " + shlex.quote(str(SS)) + "\n"
              + "ss_sync_manifest '.current_sprint = \"killed-test\"'")
    proc = subprocess.Popen(["bash", "-c", script],
                            env={**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}"},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.12)          # inside the wrapper's sleep -> mid-update
    proc.kill()               # SIGKILL
    proc.wait(timeout=10)
    after = (cd / "manifest.json").read_text()
    assert after, "manifest is empty"
    m = json.loads(after)      # must still parse
    assert m.get("current_sprint") in (None, "killed-test")
    assert m["cycle_count"] == 0


def test_failed_update_leaves_manifest_intact(tmp_path):
    """A failing jq program must not truncate the manifest (the mv is
    guarded by the jq exit status)."""
    cd = _mk_company(tmp_path)
    before = (cd / "manifest.json").read_bytes()
    r = _ss(cd, "ss_sync_manifest '.current_sprint = $broken'")
    # $broken undefined -> jq errors -> no mv
    after = (cd / "manifest.json").read_bytes()
    assert after == before, "a failed update must not modify the manifest"
    json.loads(after.decode())
