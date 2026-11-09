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
import re
from pathlib import Path

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
