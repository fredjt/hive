"""T4 (0.22): a bare `pytest tests/` (no -n) runs parallel with the
capped adaptive worker count.

Why a plugin and not the conftest: xdist 3.8 reads
config.option.numprocesses in its OWN pytest_configure, which a
conftest\u2019s pytest_configure lands AFTER (probed on this stack: a
bare run stayed serial), and a conftest cannot implement
pytest_load_initial_conftests for itself — the hook that loads it.
This module is registered by pytest.ini\u2019s addopts
(``-p adaptive_xdist``): -p plugins load at startup, before the
initial conftest, so its pytest_load_initial_conftests lands before
xdist\u2019s pytest_configure.

The formula is a documented fact, not a 0.11 reference-box constant:
``min(XDIST_CAP, max(XDIST_FLOOR, cpu // 4))``. "-n 8 is the sweet
spot" was ONE box\u2019s measurement; on an 88-core host -n 32 beats
it by ~50s. The cap exists because the subprocess-heavy tail (the
harness round-trips, the cut-receipt re-run) is load-sensitive —
beyond the cap it flakes; the floor exists because below 4 workers
the parallel overhead buys nothing. Workers do not scale linearly
with cores (each worker spawns its own process tree), hence the
quarter. An explicit -n ALWAYS wins — CI\u2019s ``-n auto`` on the
2-core runners is untouched by this module.
"""
import os

XDIST_CAP = 32
XDIST_FLOOR = 4


def _adaptive_worker_count():
    """min(XDIST_CAP, max(XDIST_FLOOR, cpu // 4)) — the documented
    formula. 1-core host -> 4 (floor); 88-core -> 22; 128+ -> 32
    (cap)."""
    try:
        cpu = os.cpu_count() or XDIST_FLOOR
    except Exception:
        cpu = XDIST_FLOOR
    return min(XDIST_CAP, max(XDIST_FLOOR, cpu // 4))


def pytest_load_initial_conftests(early_config):
    config = early_config
    try:
        if getattr(config.option, "numprocesses", None) is not None:
            return  # an explicit -n ALWAYS wins
        config.option.numprocesses = _adaptive_worker_count()
    except Exception:
        pass  # xdist absent / option not parsed — serial is fine
