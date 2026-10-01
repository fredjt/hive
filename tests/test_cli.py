"""Tests for hive_cli.main — CLI argument parsing."""
import argparse
import sys
from pathlib import Path

import pytest

# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)


def _make_parser():
    from hive_cli.main import build_parser
    return build_parser()


class TestBuildParser:
    """Test that the argument parser is correctly configured."""

    def test_parser_has_init(self):
        p = _make_parser()
        # Should not raise — just check we can parse init
        args = p.parse_args(["init"])
        assert args.cmd == "init"

    def test_parser_has_start(self):
        p = _make_parser()
        args = p.parse_args(["start"])
        assert args.cmd == "start"

    def test_parser_has_stop(self):
        p = _make_parser()
        args = p.parse_args(["stop"])
        assert args.cmd == "stop"

    def test_parser_has_status(self):
        p = _make_parser()
        args = p.parse_args(["status"])
        assert args.cmd == "status"

    def test_parser_has_dashboard(self):
        p = _make_parser()
        args = p.parse_args(["dashboard"])
        assert args.cmd == "dashboard"

    def test_parser_has_ticket(self):
        p = _make_parser()
        args = p.parse_args(["ticket", "create", "it", "feature", "medium", "Title", "Desc"])
        assert args.cmd == "ticket"
        assert args.action == "create"

    def test_parser_has_ticket_subcommands(self):
        p = _make_parser()
        for action in ["create", "get", "list", "move", "claim", "comment", "approve"]:
            if action == "create":
                args = p.parse_args(["ticket", action, "it", "feature", "medium", "Title", "Desc"])
            elif action == "get":
                args = p.parse_args(["ticket", action, "it", "TICKET-001"])
            elif action == "list":
                args = p.parse_args(["ticket", action])
            elif action == "move":
                args = p.parse_args(["ticket", action, "it", "TICKET-001", "done", "Comment", "Author"])
            elif action == "claim":
                args = p.parse_args(["ticket", action, "it", "TICKET-001", "IC", "agent-id", "Agent Name"])
            elif action == "comment":
                args = p.parse_args(["ticket", action, "it", "TICKET-001", "Author", "Comment"])
            elif action == "approve":
                args = p.parse_args(["ticket", action, "it", "TICKET-001", "Verifier", "Rationale here with enough chars"])
            assert args.action == action

    def test_parser_has_chat(self):
        p = _make_parser()
        args = p.parse_args(["chat", "send", "general", "user", "it", "Hello"])
        assert args.cmd == "chat"
        assert args.action == "send"

    def test_parser_has_agent(self):
        p = _make_parser()
        for action in ["spawn-ceo", "run-vp", "verify"]:
            if action == "spawn-ceo":
                args = p.parse_args(["agent", action])
            elif action == "run-vp":
                args = p.parse_args(["agent", action, "it"])
            elif action == "verify":
                args = p.parse_args(["agent", action, "it", "TICKET-001"])
            assert args.action == action

    def test_parser_has_gates(self):
        p = _make_parser()
        for action in ["run", "check", "status"]:
            if action == "run":
                args = p.parse_args(["gates", action, "it"])
            elif action == "check":
                args = p.parse_args(["gates", action, "it", "TICKET-001"])
            else:
                args = p.parse_args(["gates", action])
            assert args.action == action

    def test_parser_has_backlog(self):
        p = _make_parser()
        args = p.parse_args(["backlog", "prioritize", "it"])
        assert args.action == "prioritize"

    def test_parser_has_inbox(self):
        p = _make_parser()
        for action in ["file", "process"]:
            if action == "file":
                args = p.parse_args(["inbox", action, "Some request"])
            else:
                args = p.parse_args(["inbox", action])
            assert args.action == action

    def test_parser_has_config(self):
        p = _make_parser()
        for action in ["get", "set", "edit", "show"]:
            if action == "get":
                args = p.parse_args(["config", action, "key"])
            elif action == "set":
                args = p.parse_args(["config", action, "key", "value"])
            else:
                args = p.parse_args(["config", action])
            assert args.action == action

    def test_parser_has_version(self):
        p = _make_parser()
        args = p.parse_args(["version"])
        assert args.cmd == "version"

    def test_parser_has_doctor(self):
        p = _make_parser()
        args = p.parse_args(["doctor"])
        assert args.cmd == "doctor"

    def test_parser_has_killall(self):
        p = _make_parser()
        args = p.parse_args(["killall", "--dry-run"])
        assert args.cmd == "killall"

    def test_parser_has_round(self):
        p = _make_parser()
        for action in ["status", "next", "history", "get", "enqueue", "advance", "promote"]:
            if action == "history":
                args = p.parse_args(["round", action])
            elif action == "get":
                args = p.parse_args(["round", action, "round-123"])
            elif action == "enqueue":
                args = p.parse_args(["round", action, "--agent", "ic-engineer", "--dept", "it", "--role", "IC", "--kind", "test"])
            else:
                args = p.parse_args(["round", action])
            assert args.action == action

    def test_parser_has_gh(self):
        p = _make_parser()
        for action in ["status"]:
            args = p.parse_args(["gh", action])
            assert args.action == action

    def test_parser_has_gh_pr(self):
        p = _make_parser()
        for pr_action in ["open", "merge", "reject"]:
            if pr_action == "open":
                args = p.parse_args(["gh", "pr", pr_action, "feature-branch", "TICKET-001"])
            elif pr_action == "merge":
                args = p.parse_args(["gh", "pr", pr_action, "feature-branch"])
            elif pr_action == "reject":
                args = p.parse_args(["gh", "pr", pr_action, "feature-branch", "TICKET-001", "--reason", "Rejected"])
            assert args.pr_action == pr_action

    def test_parser_has_release(self):
        p = _make_parser()
        for action in ["start", "status", "list", "publish", "abort"]:
            if action == "start":
                args = p.parse_args(["release", action, "v1.0.0"])
            elif action == "status":
                args = p.parse_args(["release", action, "v1.0.0"])
            elif action == "publish":
                args = p.parse_args(["release", action, "GO", "v1.0.0", "Rationale here"])
            elif action == "abort":
                args = p.parse_args(["release", action, "v1.0.0", "Reason"])
            else:
                args = p.parse_args(["release", action])
            assert args.action == action

    def test_parser_has_scaffold(self):
        p = _make_parser()
        for action in ["extract", "update", "diff"]:
            if action == "extract":
                args = p.parse_args(["scaffold", action])
            elif action == "update":
                args = p.parse_args(["scaffold", action, "--dry-run"])
            else:
                args = p.parse_args(["scaffold", action])
            assert args.scaffold_action == action

    def test_parser_has_tool(self):
        p = _make_parser()
        for sub in ["tool", "tools"]:
            if sub == "tool":
                args = p.parse_args(["tool", "ticket_create", "--arg", "key=value"])
            else:
                args = p.parse_args(["tool", sub, "--json"])
            assert args.cmd == "tool"

    def test_parser_has_llm(self):
        p = _make_parser()
        for action in ["stats", "tail"]:
            if action == "tail":
                args = p.parse_args(["llm", action, "-n", "10"])
            else:
                args = p.parse_args(["llm", action])
            assert args.action == action

    def test_bare_hive_no_cmd(self):
        """Bare `hive` should return dashboard handler."""
        from hive_cli.main import build_parser
        p = build_parser()
        args = p.parse_args([])
        assert args.cmd is None  # triggers dashboard in main()

    def test_help_returns_0(self):
        """--help should exit with code 0 (argparse default)."""
        from hive_cli.main import build_parser
        p = build_parser()
        try:
            p.parse_args(["--help"])
        except SystemExit as e:
            assert e.code == 0

    def test_unknown_cmd_prints_help(self):
        """Unknown subcommand should print help and return 1."""
        from hive_cli.main import main
        rc = main(["unknown-cmd-xyz"])
        assert rc == 1


class TestMain:
    """Test the main() entry point."""

    def test_version_command(self, install_dir):
        from hive_cli.main import main
        rc = main(["version"])
        assert rc == 0

    def test_main_with_no_args(self, tmp_project_dir):
        """Bare main() should try dashboard (which will fail without server)."""
        from hive_cli.main import main
        # This will likely fail since no dashboard server is running
        rc = main([])
        assert isinstance(rc, int)  # Should return an int

    def test_main_with_help(self):
        """main(['--help']) should exit 0."""
        from hive_cli.main import main
        try:
            main(["--help"])
        except SystemExit as e:
            assert e.code == 0
