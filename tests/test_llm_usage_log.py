"""C3: llm-usage.log stops hiding the response.

The 0.10.0 field run turned on exactly this: the CEO/vp-hr turns' broken
tool call sat after the first sentence, and _log_llm_usage kept only the
first 200 chars of the FIRST line — invisible in the one log built for
post-mortems. Now up to 500 chars of the FULL response (multi-line
allowed) are recorded, with a truncated flag.
"""
import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "agent_harness", REPO / "lib" / "core" / "agent-harness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HARNESS = _load_harness()


def _log_one(tmp_path, response, turn=1, usage=None):
    HARNESS._log_llm_usage(str(tmp_path), "ceo", "agent", "stub", turn,
                           usage or {}, response)
    lines = (tmp_path / "logs" / "llm-usage.log").read_text().strip().splitlines()
    return json.loads(lines[-1])


def test_second_line_is_recorded(tmp_path):
    """A response with a first sentence and a second-line block (the run's
    exact shape: the broken tool call sat after the first sentence) ->
    the usage entry contains the second line and truncated is False."""
    response = (
        "I will check the configuration file now.\n"
        "<tool call>\n"
        "bash: cat /tmp/company/config.toml\n"
        "</tool call>"
    )
    entry = _log_one(tmp_path, response)
    assert entry["response"] == response, entry
    # The line the old first-line record threw away is in the log:
    assert "bash: cat /tmp/company/config.toml" in entry["response"]
    assert entry["truncated"] is False


def test_long_response_truncates_at_500(tmp_path):
    """A >500-char response -> truncated true, recorded length exactly 500."""
    response = "y" * 700
    entry = _log_one(tmp_path, response, turn=2)
    assert entry["truncated"] is True
    assert len(entry["response"]) == 500
    assert entry["response"] == "y" * 500


def test_multiline_response_beyond_500(tmp_path):
    """Multi-line content past 500 chars: the truncation keeps EARLY
    multi-line content (not just a first line) and flags it."""
    response = "First sentence with the outcome.\n" + "second-line block\n" * 60
    entry = _log_one(tmp_path, response, turn=3)
    assert len(response) > 500
    assert entry["truncated"] is True
    assert len(entry["response"]) == 500
    assert "second-line block" in entry["response"]  # beyond the first line


def test_exactly_500_is_not_truncated(tmp_path):
    entry = _log_one(tmp_path, "z" * 500, turn=4)
    assert entry["truncated"] is False
    assert len(entry["response"]) == 500


def test_empty_response(tmp_path):
    entry = _log_one(tmp_path, "", turn=5)
    assert entry["response"] == ""
    assert entry["truncated"] is False
