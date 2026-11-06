"""C1: the harness prompt and the parser agree on the block format.

The drift this release fixes: SYSTEM_PROMPT showed the bare-fence form
(a lone ``` fence with `bash: <cmd>` on the next line) but parse_actions
only accepted the fence-line form (```bash:<cmd>```), so the prompt's own
example parsed to ZERO actions — and two live agent runs ended as
"successful final answers" after their first (and only) turn used the
documented form. Now both forms are accepted for bash: and mcp:, and the
guard below re-parses the SYSTEM_PROMPT text itself so a future edit can
never ship an example the parser does not understand.
"""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_harness():
    """agent-harness.py has a hyphen in its file name — load it via
    importlib (the same file agent-spawn.sh execs)."""
    spec = importlib.util.spec_from_file_location(
        "agent_harness", REPO / "lib" / "core" / "agent-harness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HARNESS = _load_harness()

MCP_ARGS = {"dept": "it", "type": "bug", "priority": "high"}

BASH_CMD = "echo hello"

# (text, [(type, command-or-name, args-or-None), ...]) — each case must
# parse to EXACTLY the listed actions.
PARSER_CASES = [
    # ── bash: fence-line form (the historically supported one) ──
    ("```bash: echo hello```",
     [("bash", BASH_CMD, None)]),
    ("```bash:echo hello```",
     [("bash", BASH_CMD, None)]),
    # Multi-line heredoc, fence-line form (the documented file-write path).
    ("```bash: cat > /tmp/f << 'EOF'\nline1\nline2\nEOF```",
     [("bash", "cat > /tmp/f << 'EOF'\nline1\nline2\nEOF", None)]),
    # ── bash: bare-fence form (the one SYSTEM_PROMPT used to show) ──
    ("```\nbash: echo hello\n```",
     [("bash", BASH_CMD, None)]),
    ("```\nbash:echo hello\n```",
     [("bash", BASH_CMD, None)]),
    ("```\nbash: cat > /tmp/f << 'EOF'\nline1\nline2\nEOF\n```",
     [("bash", "cat > /tmp/f << 'EOF'\nline1\nline2\nEOF", None)]),
    # ── mcp: fence-line form ──
    ('```mcp: ticket_create(dept="it", type="bug", priority="high")```',
     [("mcp", "ticket_create", MCP_ARGS)]),
    ("```mcp:ping()```",
     [("mcp", "ping", {})]),
    # ── mcp: bare-fence form (the one SYSTEM_PROMPT used to show) ──
    ('```\nmcp: ticket_create(dept="it", type="bug", priority="high")\n```',
     [("mcp", "ticket_create", MCP_ARGS)]),
    ("```\nmcp:ping()\n```",
     [("mcp", "ping", {})]),
    # ── mixed forms in one turn ──
    ("Sure: ```bash: ls```\nand\n```\nmcp: ping()\n```\ndone",
     [("bash", "ls", None), ("mcp", "ping", {})]),
]

# Item 1: the STANDARD language-tagged fence (```bash\n<cmd>\n```) —
# the form the models emit by default, which the field run (2026-11-06)
# proved the parser dropped: the CEO and four other agents' first turns
# all sat in exactly this shape and parsed to zero actions.
STANDARD_CASES = [
    ("```bash\necho hello\n```",
     [("bash", BASH_CMD, None)]),
    ("```bash\necho hello```",                    # no trailing newline
     [("bash", BASH_CMD, None)]),
    ("```bash   \necho hello\n```",               # trailing spaces on the tag
     [("bash", BASH_CMD, None)]),
    ("```bash\ncat > /tmp/f << 'EOF'\nline1\nline2\nEOF\n```",
     [("bash", "cat > /tmp/f << 'EOF'\nline1\nline2\nEOF", None)]),
    ("```mcp\nticket_create(dept=\"it\", type=\"bug\", priority=\"high\")\n```",
     [("mcp", "ticket_create", MCP_ARGS)]),
    ("```mcp\nping()\n```",
     [("mcp", "ping", {})]),
    # Prose around a standard fence (the field-run turns' shape).
    ("I'll look at the config.\n\n```bash\ncat /path/to/file.txt\n```\n\nDone.",
     [("bash", "cat /path/to/file.txt", None)]),
    # A standard fence that repeats the keyword on its first line must
    # not hand the shell a literal 'bash:' token.
    ("```bash\nbash: echo hello\n```",
     [("bash", BASH_CMD, None)]),
    # The standard form mixed with the legacy forms in one turn.
    ("```bash\nls -la\n```\nand\n```bash: pwd```\nand\n```\nmcp: ping()\n```",
     [("bash", "ls -la", None), ("bash", "pwd", None), ("mcp", "ping", {})]),
]


@pytest.mark.parametrize("text, expected", STANDARD_CASES,
                         ids=[f"std-{i}" for i in range(len(STANDARD_CASES))])
def test_parse_actions_standard_fence(text, expected):
    """Item 1: the standard ```bash/```mcp fence — the form the models
    naturally emit — parses to the right action, exactly like the legacy
    forms."""
    actions = HARNESS.parse_actions(text)
    got = [(a["type"], a.get("command") or a.get("name")) for a in actions]
    want = [(t, n) for t, n, _ in expected]
    assert got == want, f"{text!r} -> {actions}"
    for a, (t, n, args) in zip(actions, expected):
        if t == "mcp":
            assert a["args"] == args, f"{text!r} -> {a}"
        else:
            assert a["command"] == n, f"{text!r} -> {a}"


def test_standard_fence_is_not_double_parsed():
    """The three fence regexes are disjoint (they key off what follows
    the opening ```): a single standard block yields exactly one action,
    and a fence-line block never matches the standard regex."""
    assert len(HARNESS.parse_actions("```bash\nls\n```")) == 1
    assert len(HARNESS.parse_actions("```bash: ls```")) == 1
    assert len(HARNESS.parse_actions("```\nbash: ls\n```")) == 1


def test_standard_python_fence_is_not_an_action():
    """Only bash/mcp-tagged standard fences are actions — a ```python
    code sample in prose is not one."""
    text = "Example:\n```python\nprint('hello')\n```\n"
    assert HARNESS.parse_actions(text) == []


def test_prefixed_tool_names_are_normalized():
    """Item 2: the prompts historically taught mcp__hive__<tool> and
    models still emit it — the parser resolves the prefixed name to the
    plain registered name instead of handing the server an unknown tool.
    (Prompts are de-prefixed in the same change; this is the defense in
    depth for whatever slips through.)"""
    acts = HARNESS.parse_actions(
        '```mcp\nmcp__hive__ticket_get(dept="hr", ticket_id="HR-0001")\n```')
    assert len(acts) == 1, f"{acts}"
    assert acts[0]["type"] == "mcp"
    assert acts[0]["name"] == "ticket_get", f"{acts[0]['name']}"
    assert acts[0]["args"] == {"dept": "hr", "ticket_id": "HR-0001"}

    # Any mcp__<server>__ prefix is stripped, not just the hive one.
    acts = HARNESS.parse_actions('```mcp: mcp__other_server__ping()```')
    assert acts[0]["name"] == "ping", f"{acts[0]['name']}"

    # Plain names pass through unchanged.
    acts = HARNESS.parse_actions("```mcp\nping()\n```")
    assert acts[0]["name"] == "ping"

    # A plain name that merely STARTS with mcp__ (no server__ suffix)
    # is a registered name, not a prefix — leave it alone.
    acts = HARNESS.parse_actions("```mcp\nmcp__x()\n```")
    assert acts[0]["name"] == "mcp__x"



@pytest.mark.parametrize("text, expected", PARSER_CASES,
                         ids=[f"case-{i}" for i in range(len(PARSER_CASES))])
def test_parse_actions_both_forms(text, expected):
    """The prompt's literal example, the fence-line form, and both mcp:
    variants each parse to the right action — exactly one action per
    block, with the documented command / call / args."""
    actions = HARNESS.parse_actions(text)
    got = [(a["type"], a.get("command") or a.get("name")) for a in actions]
    want = [(t, n) for t, n, _ in expected]
    assert got == want, f"{text!r} -> {actions}"
    for a, (t, n, args) in zip(actions, expected):
        if t == "mcp":
            assert a["args"] == args, f"{text!r} -> {a}"
        else:
            assert a["command"] == n, f"{text!r} -> {a}"


def test_bare_fence_without_keyword_is_not_an_action():
    """A bare fence that does NOT start a line with bash:/mcp: (a code
    sample in prose, a diff) is not an action."""
    text = ("Here is some python:\n```\nprint('hello')\n```\n"
            "And a diff:\n```\n+foo\n```\n")
    assert HARNESS.parse_actions(text) == []


def test_inline_backticks_do_not_match():
    """Single/double inline backticks in prose must not be mistaken for a
    block (the parser keys off triple backticks)."""
    text = "run `bash: cat x` or `mcp: t(a=1)` — or use ``bash: y`` inline."
    assert HARNESS.parse_actions(text) == []


def test_no_mcp_disables_both_mcp_forms():
    text = "```mcp: ping()```\n```\nmcp: ping()\n```\n"
    assert HARNESS.parse_actions(text, no_mcp=True) == []
    assert len(HARNESS.parse_actions(text, no_mcp=False)) == 2


def test_system_prompt_examples_parse():
    """C1 + item 1 guard: every block example in SYSTEM_PROMPT must parse
    to the action the prompt documents. The drift this fixes was exactly
    an unparsed prompt example — an edit that teaches a form the parser
    does not accept must fail here, not in a live company. The prompt
    documents the standard ```bash/```mcp fence FIRST (item 1: the form
    the models emit by default, dropped by the 0.9.0 parser), then the
    two legacy spellings: 5 bash examples + 3 mcp examples = 8."""
    acts = HARNESS.parse_actions(HARNESS.SYSTEM_PROMPT)
    bash_cmds = [a["command"] for a in acts if a["type"] == "bash"]
    mcp_acts = [a for a in acts if a["type"] == "mcp"]

    # The shell example is shown in ALL THREE forms — all must parse.
    assert bash_cmds.count("cat /path/to/file.txt") == 3, (
        f"the shell example does not parse in all three forms: {bash_cmds}"
    )
    # The mcp example is shown in ALL THREE forms — all must parse with
    # the documented args.
    assert len(mcp_acts) == 3, (
        f"the mcp example does not parse in all three forms: {acts}"
    )
    for a in mcp_acts:
        assert a["name"] == "ticket_create", f"{acts}"
        assert a["args"].get("dept") == "backend", f"{a['args']}"
    # The file-read and file-write examples are standard fences.
    assert "cat /path/to/file" in bash_cmds, f"{bash_cmds}"
    assert any(c.startswith("cat > /path/to/file << 'HEREDOC'") and
               "HEREDOC" in c for c in bash_cmds), f"{bash_cmds}"
    # No example may silently vanish: every documented fence block
    # carries an action (8 examples: 5 bash, 3 mcp).
    assert len(acts) == 8, f"expected 8 parseable examples, got {len(acts)}: {acts}"


def test_prompt_lists_standard_form_first():
    """Item 1: SYSTEM_PROMPT presents the STANDARD fence first (the
    preferred form — the one the models emit by default and the 0.9.0
    parser dropped) for both bash and mcp, before the legacy
    keyword-glued and bare-fence spellings."""
    p = HARNESS.SYSTEM_PROMPT
    bash_std = p.index("\n```bash\ncat /path/to/file.txt\n```")
    bash_line = p.index("```bash: cat /path/to/file.txt```")
    bash_bare = p.index("\n```\nbash: cat /path/to/file.txt\n```")
    assert bash_std < bash_line < bash_bare, \
        "bash: standard form must come first, then the legacy forms"
    mcp_call = "ticket_create(dept=\"backend\""
    mcp_std = p.index("\n```mcp\n" + mcp_call)
    mcp_line = p.index("```mcp: " + mcp_call)
    mcp_bare = p.index("\n```\nmcp: " + mcp_call)
    assert mcp_std < mcp_line < mcp_bare, \
        "mcp: standard form must come first, then the legacy forms"


def test_parse_correction_shows_standard_form_first():
    """Item 1: the correction fed back on an unparseable turn must teach
    the standard fence first — the model is most likely to retry in the
    shape it already reached for."""
    p = HARNESS.PARSE_CORRECTION
    assert p.index("```bash\n<your command>\n```") < p.index("```bash: <cmd>```"), \
        "standard form must be listed first in the correction"
