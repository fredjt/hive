"""The xdist worker contract (0.11, R1) — the suite is worker-safe by
construction, pinned by static guards instead of by rerunning until the
unlucky draw stops happening.

Three flakes in one week (the 0.10.0 CI matrix) had three mechanisms and
one theme: tests sharing mutable state across xdist workers.

(a) importlib.reload() of a lib/ module inside the test process re-executes
    the module and replaces shared objects (e.g. hive_mcp.shell.ShellResult)
    with NEW class objects; on the worker that also ran the module's other
    test file, isinstance() was False.
(b) two move paths a few ms apart crossed a wall-clock second boundary on
    ~5% of runs — state comparison without clock normalization.
(c) stage=idle written before the manifest's idle_mode=true left an
    observation window — no single commit point.

Each was fixed by hand; this file is what prevents the next one. See the
"xdist worker contract" section of the root AGENTS.md."""
import ast
import configparser
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent


class TestClockNormalizationContract:
    """One shared clock normalizer, in conftest, used everywhere.

    The normalizer that lived hand-rolled in test_state_layer moved to
    tests/conftest.py (R1). Any test comparing state written at different
    instants must go through it — a second copy is how the normalization
    silently diverges from the contract it is supposed to pin.
    """

    def _test_modules(self):
        # The guard file itself is out of scope (it names the offender
        # patterns it hunts for).
        return sorted(p for p in TESTS_DIR.glob("test_*.py")
                      if p.name != "test_xdist_contract.py")

    def test_shared_normalizer_lives_in_conftest(self):
        conftest = (TESTS_DIR / "conftest.py").read_text()
        assert re.search(r"^def normalize_clock\(", conftest, re.M), \
            "conftest.py must define the shared normalize_clock() helper"
        assert "TIMESTAMP_KEYS" in conftest, \
            "conftest.py must name the timestamp key set"

    def test_no_test_file_hand_rolls_its_own_normalizer(self):
        # A clock normalizer's def name carries both halves — e.g.
        # _normalize_clock. (Plain "normalized" test names like
        # test_prefixed_tool_names_are_normalized are not offenders.)
        offenders = []
        for p in self._test_modules():
            src = p.read_text()
            if re.search(r"^\s*def\s+\w*normaliz\w*clock\w*\s*\(",
                         src, re.M | re.I) or \
               re.search(r"^\s*def\s+\w*clock\w*normaliz\w*\s*\(",
                         src, re.M | re.I):
                offenders.append(p.name)
        assert not offenders, (
            "hand-rolled clock normalization in "
            f"{offenders} — use conftest.normalize_clock() instead "
            "(xdist worker contract, R1)"
        )

    def test_state_layer_uses_the_shared_helper(self):
        src = (TESTS_DIR / "test_state_layer.py").read_text()
        assert re.search(r"\bnormalize_clock\(", src), \
            "test_state_layer must normalize via the shared helper"
        assert "_normalize_clock" not in src, \
            "test_state_layer still carries its own normalizer"


class TestNoInProcessReload:
    """No test module may call importlib.reload() on a lib/ module inside
    the test process.

    Reloading a lib module re-executes it: module-level state is
    re-initialized and shared objects (e.g. hive_mcp.shell.ShellResult)
    are replaced with NEW class objects, while every other test module
    still holds the old bindings. On the xdist worker that also ran the
    reloaded module's other test file, isinstance() checks then fail —
    the 0.10.0 CI flake behind test_shell.py::TestRun (the
    test_run_filters_none_args failure on the 3.10 job). Subprocess
    isolation is the pattern: a fresh interpreter imports the module
    fresh under the same env contract, with no shared-module poisoning
    (tests/test_hive_paths.py is the working example).
    """

    def _test_modules(self):
        # The guard file itself is out of scope (it names the offender
        # pattern it hunts for).
        return sorted(p for p in TESTS_DIR.glob("test_*.py")
                      if p.name != "test_xdist_contract.py")

    def test_no_test_module_calls_importlib_reload(self):
        offenders = []
        for p in self._test_modules():
            src = p.read_text()
            for i, line in enumerate(src.splitlines(), 1):
                if "importlib.reload" in line or \
                   re.search(r"from\s+importlib\s+import\s+[^#]*\breload\b",
                             line):
                    offenders.append(f"{p.name}:{i}: {line.strip()}")
        assert not offenders, (
            "importlib.reload() inside the test process — xdist worker "
            "contract violation (root AGENTS.md, 'xdist worker contract'). "
            "Reload re-executes the module and replaces shared objects "
            "with new class objects, so isinstance() fails on the worker "
            "that also ran the module's other test file. Run the module "
            "in a subprocess instead (tests/test_hive_paths.py is the "
            "pattern): " + "; ".join(offenders)
        )


class TestSkipReasonContract:
    """Every skip in the suite names the condition it skips on.

    A skip that outlives its reason is a silent coverage hole: nobody
    re-asks "is this still true?" (the 0.10-matrix flake class, one of
    whose lessons is that assumptions stop being re-audited the moment
    they stop being read). 0.13 T5 therefore pins the bar:

    - the condition must be a STATIC string in the source — a literal,
      an f-string, or a chain of literal concatenations — so the guard
      can audit it here without running the test, and a future reader
      can re-audit the whole skip list by eye from the tree. A reason
      held in a variable is not auditable and fails.
    - the condition must not be trivial: a bare `pytest.skip()`, a
      one-word reason ("skip", "broken", "flaky", ...), or a stub under
      ten characters fails. The string must NAME the condition —
      "node unavailable — cannot run the SPA smoke" does;
      "todo" does not.

    This is the audit hook: re-running the suite on a new host/CI row
    surfaces the skip list; the guard keeps every line of it honest.
    """

    TRIVIAL_REASONS = {
        "skip", "skipped", "skip this", "skip it", "skip test",
        "todo", "fixme", "xxx", "wip", "work in progress", "later",
        "temp", "temporary", "temporarily", "broken", "failing",
        "failing test", "flaky", "flaky test", "disabled", "not ready",
        "incomplete", "unimplemented", "placeholder", "stub", "no reason",
        "n/a", "hmm", "?", "!",
    }
    MIN_REASON_LEN = 10

    def _test_modules(self):
        # The guard file itself is out of scope (it names the offender
        # patterns it hunts for).
        return sorted(p for p in TESTS_DIR.glob("test_*.py")
                      if p.name != "test_xdist_contract.py")

    @staticmethod
    def _is_pytest_mark(node, mark):
        """node is `pytest.mark.<mark>` (Attribute chain)."""
        return (isinstance(node, ast.Attribute) and node.attr == mark
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "mark"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "pytest")

    def _skip_sites(self, path):
        """Yield (lineno, kind, call_or_bare) for every skip site:
        pytest.skip(...) calls and pytest.mark.skip / .skipif decorators
        (called or bare)."""
        tree = ast.parse(path.read_text())
        call_funs = set()
        bare_mark_attrs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_funs.add(id(node.func))
                func = node.func
                if (isinstance(func, ast.Attribute) and func.attr == "skip"
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "pytest"):
                    yield node.lineno, "pytest.skip", node
                elif self._is_pytest_mark(func, "skip") or \
                        self._is_pytest_mark(func, "skipif"):
                    yield node.lineno, "mark." + func.attr, node
            elif self._is_pytest_mark(node, "skip") and \
                    id(node) not in call_funs:
                bare_mark_attrs.append((node.lineno, node))
        # Bare `@pytest.mark.skip` decorators: no reason is possible in
        # the source at all, so every one is an offender by construction
        # (the kind records why).
        for lineno, node in bare_mark_attrs:
            yield lineno, "mark.skip-bare", node

    @classmethod
    def _reason_text(cls, node):
        """(ok, text): static string value of the expression, with
        f-string interpolations rendered as {?}. ok=False when the
        expression is not statically readable (variable, call, ...)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return True, node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                else:
                    parts.append("{?}")
            return True, "".join(parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            ok_l, t_l = cls._reason_text(node.left)
            ok_r, t_r = cls._reason_text(node.right)
            if ok_l and ok_r:
                return True, t_l + t_r
        return False, ""

    def _is_trivial(self, text):
        norm = re.sub(r"\s+", " ", text.lower()).strip()
        return norm in self.TRIVIAL_REASONS or len(norm) < self.MIN_REASON_LEN

    def _extract_reason(self, kind, call):
        """(ok, text, why) for the reason expression of a skip site.
        ok=False -> the site fails the contract; why says why.
        kind 'mark.skip' accepts a positional string or reason=;
        'mark.skipif' requires reason=; 'pytest.skip' requires the
        first positional argument."""
        if kind == "pytest.skip":
            if not call.args:
                return False, "", "no reason — name the condition being skipped"
            ok, text = self._reason_text(call.args[0])
            return (ok, text, None) if ok else \
                (False, "", "reason is not a static string")
        if kind == "mark.skip":
            if len(call.args) == 1:
                ok, text = self._reason_text(call.args[0])
                return (ok, text, None) if ok else \
                    (False, "", "reason is not a static string")
            kws = [k for k in call.keywords if k.arg == "reason"]
            if kws:
                ok, text = self._reason_text(kws[0].value)
                return (ok, text, None) if ok else \
                    (False, "", "reason is not a static string")
            return False, "", "no reason= — name the condition being skipped"
        # mark.skipif: the condition is a live boolean; the REASON is
        # the contract and must be a keyword.
        kws = [k for k in call.keywords if k.arg == "reason"]
        if not kws:
            return False, "", "no reason= — name the condition being skipped"
        ok, text = self._reason_text(kws[0].value)
        return (ok, text, None) if ok else \
            (False, "", "reason is not a static string")

    def test_every_skip_carries_a_literal_reason_naming_the_condition(self):
        offenders = []
        for p in self._test_modules():
            for lineno, kind, call in self._skip_sites(p):
                if kind == "mark.skip-bare":
                    offenders.append(
                        f"{p.name}:{lineno}: @pytest.mark.skip (bare) — "
                        f"no reason is possible in the source; use "
                        f'@pytest.mark.skip(reason="<condition>")')
                    continue
                ok, text, why = self._extract_reason(kind, call)
                if not ok:
                    offenders.append(f"{p.name}:{lineno}: {kind} — {why}")
                elif self._is_trivial(text):
                    offenders.append(
                        f"{p.name}:{lineno}: {kind} — trivial reason "
                        f"{text.strip()!r} does not name the condition")
        assert not offenders, (
            "skip-reason contract violation (0.13 T5): every skip must "
            "carry a static, non-trivial reason naming the condition "
            "being skipped, so the skip list stays auditable from the "
            "tree: " + "; ".join(offenders)
        )


class TestScratchSnowballGuard:
    """T4 (0.14) — the scratch must not snowball: per-TEST mktemp
    litter is banned.

    The mktemp-shaped scratch (tmp.*/hive-*/pip-unpack-*) and the
    pytest basetemp are reclaimed by construction: the conftest
    _basetemp_retention hook keeps only the newest 3 basetemp
    generations per session, and packaging/clean-scratch.sh applies
    the same policy to the mktemp litter (age + open-file gated).
    This guard pins the existing module-level mktemp call SITES
    (each creates ONE dir per module/session, all with the hive-*
    prefix the reaper reaps): a new per-test mkdtemp/mkstemp call
    fails the guard until the set is extended with a deliberate,
    documented exception — the snowball is bounded both ways
    (reaper reaps the old, this guard stops the new)."""

    #: (file, line) of every mkdtemp/mkstemp call site in the suite
    #: (module/session scope, hive-* prefixed). A NEW site fails the
    #: guard; an intentional extension updates THIS set with a reason.
    PINNED_SITES = {
        ("conftest.py", "tempfile.mkdtemp(prefix=\"hive-test-\")"),
        ("test_mcp_tools.py", "tempfile.mkdtemp(prefix=\"hive-poison-\")"),
        ("test_verification_gates.py", "tempfile.mkdtemp(prefix=\"hive-gates-\""),
    }

    def test_no_new_per_test_mktemp_sites(self):
        tests_dir = Path(__file__).resolve().parent
        found = set()
        for f in sorted(tests_dir.glob("*.py")):
            if f.name == Path(__file__).name:
                continue  # the guard's own source names the pattern
            for line in f.read_text().splitlines():
                s = line.strip()
                if "mkdtemp" in s or "mkstemp" in s:
                    found.add((f.name, s))
        unknown = {k: v for (k, v) in found
                   if not any(k == pf and q in v
                              for pf, q in self.PINNED_SITES)}
        assert not unknown, (
            "new mktemp call sites — per-test mktemp litter snowballs "
            "the scratch (T4). Use the tmp_path fixture, or extend "
            "PINNED_SITES with a deliberate reason:\n"
            + "\n".join(f"  {k}: {v}" for k, v in sorted(unknown)))


class TestXdistGroupContract:
    """T4 (0.18) — an ordered class must not be split across workers.

    Under the default --dist=load, xdist sends the pending queue in
    consecutive chunks (up to --maxschedchunk, 8 by default), so an
    ordered end-to-end class with more tests than a chunk can be
    split across two workers — and each worker instantiates the
    class-scoped fixture in ITS OWN temp dir, so the test that reads
    the state the earlier same-class tests mutated sees the unmutated
    fixture (0.18 gate-2 caught TestTicketHumanFace: "5 ticket(s) in
    rd" read as 3 — the mutations had landed on another worker). The
    fix is the loadgroup strategy in pytest.ini (per-test scheduling
    for unmarked tests, group-unit scheduling for marked ones) plus
    @pytest.mark.xdist_group on every class whose class-scoped
    fixture carries ordered state. This guard pins both halves: a
    class-scoped fixture without the mark, or the missing
    --dist=loadgroup, is a FAIL here."""

    def test_pytest_ini_runs_loadgroup(self):
        ini = (TESTS_DIR.parent / "pytest.ini").read_text()
        assert "--dist=loadgroup" in ini, (
            "pytest.ini must run the loadgroup strategy — under the "
            "default --dist=load an xdist_group mark alone cannot "
            "stop a class from being split across workers")

    def test_pytest_ini_uses_the_section_pytest_reads(self):
        """0.19 — the pin above only exists if pytest reads the file.

        pytest reads the [pytest] section of a pytest.ini. The
        [tool:pytest] spelling (setup.cfg's section) in a pytest.ini is
        SILENTLY ignored — every ini option in it reads back as the
        default (verified on pytest 8.4 and 9.1: addopts=[]), --dist
        falls back to per-test load, and the ordered e2e classes split
        and flake. The 0.18 pin was dead on arrival for exactly this
        reason; this is the static half of the proof (the runtime half
        is test_loadgroup_pin_is_live)."""
        cp = configparser.ConfigParser()
        cp.read_string((TESTS_DIR.parent / "pytest.ini").read_text())
        assert "pytest" in cp.sections(), (
            "pytest.ini must use the [pytest] section — the "
            "[tool:pytest] spelling is silently ignored by pytest and "
            "turns addopts into dead config (the 0.18 loadgroup pin "
            "went dead exactly this way)")
        assert "--dist=loadgroup" in cp.get("pytest", "addopts",
                                            fallback=""), \
            "the live [pytest] section must carry --dist=loadgroup"

    # A minimal group-marked class: 7 bump tests + a final read. The
    # class-scoped counter fixture is per-WORKER (tmp_path_factory), so
    # the final read sees 7 only if ALL eight tests ran on one worker —
    # i.e. only if the group was pinned. Under a dead ini (per-test
    # load, -n 4) the eight tests scatter across four workers and the
    # final read sees far less than 7.
    PROBE = '''
import pytest


@pytest.mark.xdist_group("probe")
class TestProbe:
    @pytest.fixture(scope="class")
    def counter(self, tmp_path_factory):
        import pathlib
        d = tmp_path_factory.mktemp("counter")
        (d / "n").write_text("0")
        return d

    def _bump(self, counter):
        import pathlib
        p = pathlib.Path(counter) / "n"
        p.write_text(str(int(p.read_text()) + 1))

    def test_b01(self, counter): self._bump(counter)
    def test_b02(self, counter): self._bump(counter)
    def test_b03(self, counter): self._bump(counter)
    def test_b04(self, counter): self._bump(counter)
    def test_b05(self, counter): self._bump(counter)
    def test_b06(self, counter): self._bump(counter)
    def test_b07(self, counter): self._bump(counter)

    def test_final(self, counter):
        import pathlib
        assert int((pathlib.Path(counter) / "n").read_text()) == 7
'''

    def test_loadgroup_pin_is_live(self):
        """0.19 — runtime proof that the pin is actually in force.

        A NESTED `python -m pytest -n 4` (subprocess: contract-safe) on
        the probe class passes only if the repo's pytest.ini is read by
        the runner AND its addopts carries --dist=loadgroup — otherwise
        the probe's group scatters across the four workers and the
        final read fails. This is the check that would have caught the
        dead [tool:pytest] section on day one (the 0.18 pin was
        inert; TestTicketHumanFace flaked on the T2 (0.19) suite run).
        The probe module lives under .scratch/ (gitignored) so it can
        never leak into the repo."""
        pytest.importorskip("xdist")
        probe_dir = TESTS_DIR.parent / ".scratch" / "xdist-probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = probe_dir / "test_probe.py"
        probe.write_text(self.PROBE)
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "test_probe.py", "-n", "4",
             "-q", "--no-header", "-p", "no:cacheprovider"],
            cwd=str(probe_dir),
            capture_output=True, text=True, timeout=120,
            env={**os.environ,
                 "PYTHONPATH": str(TESTS_DIR.parent / "lib")
                 + os.pathsep + os.environ.get("PYTHONPATH", "")})
        assert "8 passed" in r.stdout, (
            "the nested probe must run all eight tests on ONE worker "
            "(the group pinned by --dist=loadgroup) — if the repo's "
            "pytest.ini is not read, the group scatters and the final "
            "test sees the unmutated counter:\n" + r.stdout[-400:])

    def _class_scoped_fixture_names(self, tree):
        """Module-level fixture names declared scope='class'."""
        names = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.decorator_list:
                    if (isinstance(d, ast.Call)
                            and isinstance(d.func, ast.Attribute)
                            and d.func.attr == "fixture"):
                        for kw in d.keywords:
                            if (kw.arg == "scope"
                                    and isinstance(kw.value, ast.Constant)
                                    and kw.value.value == "class"):
                                names.add(node.name)
        return names

    @staticmethod
    def _has_class_scoped_fixture(node):
        """True when the class DEFINES a scope='class' fixture."""
        for fn in node.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in fn.decorator_list:
                if (isinstance(d, ast.Call)
                        and isinstance(d.func, ast.Attribute)
                        and d.func.attr == "fixture"):
                    for kw in d.keywords:
                        if (kw.arg == "scope"
                                and isinstance(kw.value, ast.Constant)
                                and kw.value.value == "class"):
                            return True
        return False

    @staticmethod
    def _uses_class_scoped_fixture(node, names):
        """True when a test of the class REQUESTS a module-level
        class-scoped fixture by name."""
        for fn in node.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for arg in fn.args.args:
                if arg.arg in names:
                    return True
        return False

    @staticmethod
    def _marked(node):
        return any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "xdist_group"
            for d in node.decorator_list)

    def test_class_scoped_state_classes_are_group_marked(self):
        for f in sorted(TESTS_DIR.glob("test_*.py")):
            if f.name == "test_xdist_contract.py":
                continue
            tree = ast.parse(f.read_text())
            module_class_fixtures = self._class_scoped_fixture_names(tree)
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                shared = (self._has_class_scoped_fixture(node)
                          or self._uses_class_scoped_fixture(
                              node, module_class_fixtures))
                if not shared:
                    continue
                assert self._marked(node), (
                    f"{f.name}::{node.name}: a class-scoped fixture "
                    "carries state shared across the class's tests — "
                    "mark the class @pytest.mark.xdist_group(name) so "
                    "the loadgroup strategy pins the class to one "
                    "worker in order (the 0.18 gate-2 split class)")


class TestLibCommitPointGuard:
    """T3 (0.15) — the commit-point class is pinned by construction.

    The audit (docs/commit-point-audit.md) classified every state write
    in lib/tools + lib/core + agent-harness.py: cross-process-observed
    direct (TRUNCATE-then-fill) writes commit at a single point (tmp +
    rename), and the sites that keep a direct write are registered with
    a one-line justification. This guard scans the bash tree for direct
    truncate writes whose target is a state path ($COMPANY_DIR/... or a
    variable derived from it) and is not a tmp+rename target, and fails
    on any site that is not in the registered set below — a new un-
    audited write fails the guard until it is converted or justified in
    the audit doc + this set (the scratch-snowball guard's pattern)."""

    #: (file, line-content-substring) — every direct truncate write to a
    #: state path that is NOT tmp+rename, per docs/commit-point-audit.md
    #: Class B (one-line justification in the doc; a new registration
    #: requires a new audit row).
    REGISTERED = {
        # chat-send.sh — create-only channel header (guarded by [ ! -f ];
        # identical one-line content, no partial-line reader window)
        ("lib/tools/chat-send.sh",
         'echo "# Channel: $CHANNEL" > "$CHAT_FILE"'),
        # company-health.sh — the health daemon's self-contained dedup
        # stamps (sole reader: itself)
        ("lib/tools/company-health.sh", 'echo "$now" > "$_last_file"'),
        ("lib/tools/company-health.sh", 'echo "$_now" > "$_last_file"'),
        ("lib/tools/company-health.sh",
         'echo "$now" > "$_status_dir/last-disk-crit"'),
        ("lib/tools/company-health.sh",
         'echo "$now" > "$_status_dir/last-disk-warn"'),
        ("lib/tools/company-health.sh",
         'echo "$_wt_now" > "$_wt_last_file"'),
        # init-brief.sh / init-chat-channels.sh — init-time one-shot
        # writes (no concurrent reader exists before init completes)
        ("lib/tools/init-brief.sh", 'cat > "$COMPANY_DIR/brief.md" <<EOF'),
        ("lib/tools/init-chat-channels.sh",
         'echo "# Channel: $channel" > "$CHANNEL_FILE"'),
        # onboard-generate.sh — onboarding runs in the init phase
        ("lib/tools/onboard-generate.sh", "cat > \"$ONBOARD_FILE\" <<'EOF'"),
        ("lib/tools/onboard-generate.sh", "cat > \"$README_FILE\" <<'EOF'"),
        ("lib/tools/onboard-generate.sh", "cat > \"$CHARTER_FILE\" <<'EOF'"),
        # release-test-suite.sh — the per-version test log; the first
        # write CREATES this version's log and a re-run deliberately
        # resets it (new-run semantics); single header line
        ("lib/tools/release-test-suite.sh", '} > "$LOG"'),
        # sprint-lifecycle.sh / verification-gates.sh — DEDICATED lock
        # files (flock only; the content is never read by anyone)
        ("lib/tools/sprint-lifecycle.sh", 'exec 9>"$RUNNER_LOCK"'),
        ("lib/tools/verification-gates.sh", 'exec 8>"$GATE_LOCK"'),
        # vp-idle-check.sh — the VP idle-check's self-contained run
        # stamps (sole reader: itself; one-line content)
        ("lib/tools/vp-idle-check.sh", 'echo "run" > "$LAST_RUN_FILE.reason"'),
        ("lib/tools/vp-idle-check.sh", "date -u +%s > \"$LAST_RUN_FILE\""),
        ("lib/tools/vp-idle-check.sh",
         'echo "run:$ACTIVE active tickets" > "$LAST_RUN_FILE.reason"'),
        ("lib/tools/vp-idle-check.sh",
         'echo "run:dept-chat-updated" > "$LAST_RUN_FILE.reason"'),
        ("lib/tools/vp-idle-check.sh",
         'echo "run:leadership-chat-updated" > "$LAST_RUN_FILE.reason"'),
        ("lib/tools/vp-idle-check.sh",
         'echo "run:30-min-heartbeat" > "$LAST_RUN_FILE.reason"'),
        ("lib/tools/vp-idle-check.sh",
         'echo "skip:idle (active=0, no chat changes, last run $((NOW - LAST_RUN_TS))s ago)" > "$LAST_RUN_FILE.reason"'),
        # orchestrator.sh — one-shot startup log line at orchestrator boot
        ("lib/core/orchestrator.sh",
         'printf \'%s\\n\' "$CEO_OUTPUT" > "$COMPANY_DIR/logs/ceo-initial-build.log"'),
    }

    ASSIGN_RE = re.compile(r'^\s*(?:local\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$')
    # a '>' that is not part of '>>' / '>=' / '2>'-style fd-dup, followed
    # by the redirect target token
    WRITE_RE = re.compile(r'(?<![><])>(?!=)(?!>)\s*"?([A-Za-z_{/$.][^" \n]*)')

    @classmethod
    def _state_vars(cls, text):
        """(vars, all-assignments): variables assigned from $COMPANY_DIR
        (transitively) — a state path's address may travel in a var."""
        vm = {}
        for line in text.splitlines():
            m = cls.ASSIGN_RE.match(line)
            if m:
                vm.setdefault(m.group(1), m.group(2))
        sv = {n for n, r in vm.items() if "COMPANY_DIR" in r}
        changed = True
        while changed:
            changed = False
            for n, r in vm.items():
                if n in sv:
                    continue
                for v in list(sv):
                    if re.search(r"\$%s\b|\$\{%s\}" % (v, v), r):
                        sv.add(n)
                        changed = True
        return sv, vm

    @classmethod
    def _is_state_target(cls, tok, sv, vm):
        if not tok or tok in ("/dev/null",) or tok.startswith("/tmp"):
            return False
        if "COMPANY_DIR" in tok:
            return True
        if ".tmp" in tok:
            return False  # the tmp+rename idiom (halt.tmp.$$ & kin)
        m = re.match(r'^["\']?\$?\{?([A-Za-z_][A-Za-z0-9_]*)\}?', tok)
        if m and m.group(1) in sv:
            return ".tmp" not in vm.get(m.group(1), "")
        return False

    def scan(self, path):
        """[(lineno, stripped-line)] of every direct truncate write in
        the file whose target is a state path (not a tmp+rename target)."""
        text = Path(path).read_text()
        sv, vm = self._state_vars(text)
        hits = []
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = self.WRITE_RE.search(s)
            if m and self._is_state_target(m.group(1), sv, vm):
                hits.append((i, s))
        return hits

    def test_no_unregistered_direct_state_writes(self):
        repo = Path(__file__).resolve().parent.parent
        violations = []
        for d in ("lib/tools", "lib/core"):
            for f in sorted((repo / d).glob("*.sh")):
                rel = str(f.relative_to(repo))
                for i, s in self.scan(f):
                    if not any(pf == rel and q in s
                               for pf, q in self.REGISTERED):
                        violations.append(f"{rel}:{i}: {s[:90]}")
        assert not violations, (
            "direct truncate write(s) to a state path that the audit "
            "(docs/commit-point-audit.md) does not register — the "
            "commit-point class (T3, 0.15): a reader can land between "
            "create-truncate and fill. Convert to tmp + rename, or add "
            "the site to the audit + REGISTERED with a justification:\n"
            + "\n".join(violations))

    def test_planted_direct_write_is_caught(self, tmp_path):
        """Test-first (a): plant a new direct write (a resurrected
        `cat > halt`-shaped site) in a copy of a real script — the
        scanner names it (red against a guard that only checks the
        registered set)."""
        repo = Path(__file__).resolve().parent.parent
        src = repo / "lib" / "tools" / "sprint-state.sh"
        planted = tmp_path / "planted.sh"
        body = (
            "#!/usr/bin/env bash\n"
            'COMPANY_DIR="${COMPANY_DIR:-.company}"\n'
            'cat > "$COMPANY_DIR/rounds/halt" <<EOF\n'
            '{ "reason": "planted" }\n'
            "EOF\n")
        planted.write_text(body)
        hits = self.scan(planted)
        assert any("COMPANY_DIR/rounds/halt" in s for _, s in hits), (
            "the planted direct write must be caught: " + repr(hits))
