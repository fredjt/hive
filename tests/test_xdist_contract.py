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
import re
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
