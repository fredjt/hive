"""Tests for hive_cli.main — CLI argument parsing."""
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from unittest import mock

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
        for action in ["spawn-ceo", "run-vp"]:
            if action == "spawn-ceo":
                args = p.parse_args(["agent", action])
            elif action == "run-vp":
                args = p.parse_args(["agent", action, "it"])
            assert args.action == action

    def test_agent_verify_subcommand_removed(self):
        """hive agent verify is gone: it called the agent_verify MCP tool,
        which was removed under the sprint-staged lifecycle (verification
        ICs are spawned by sprint-lifecycle during the verify stage, not
        via an ad-hoc tool). The subcommand must not come back silently."""
        p = _make_parser()
        with pytest.raises(SystemExit):
            p.parse_args(["agent", "verify", "it", "TICKET-001"])

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
        for pr_action in ["open", "merge"]:
            if pr_action == "open":
                args = p.parse_args(["gh", "pr", pr_action, "feature-branch", "TICKET-001"])
            elif pr_action == "merge":
                args = p.parse_args(["gh", "pr", pr_action, "feature-branch"])
            assert args.pr_action == pr_action

    def test_parser_gh_pr_reject_removed(self):
        """C.9: `gh pr reject` was dead on arrival — gh-pr-reject.sh is a
        deprecation shim (unconditional exit 4). PR close is now a scripted
        side-effect of rejecting the ticket, so the subcommand is gone."""
        p = _make_parser()
        with pytest.raises(SystemExit):
            p.parse_args(["gh", "pr", "reject", "feature-branch", "TICKET-001",
                          "--reason", "Rejected"])

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


class TestMcpStdioFallback:
    """The CLI's stdio MCP fallback must work when the install dir is a
    source checkout with no venv (the documented PYTHONPATH=lib dev mode).

    Regression: every MCP-routed command (ticket, chat, company, gates, …)
    died with a raw `FileNotFoundError: <install>/venv/bin/python` because
    _stdio_session() hard-coded the venv interpreter and had no fallback —
    the same missing-venv failure class 0.8.3 fixed in agent-spawn.sh.
    """

    def test_stdio_python_prefers_venv_when_present(self, tmp_path):
        from hive_cli import mcp_client as mc
        venv_py = tmp_path / "venv" / "bin" / "python"
        venv_py.parent.mkdir(parents=True)
        venv_py.write_text("#!/bin/sh\n")
        with mock.patch.object(mc, "HIVE_INSTALL_DIR", tmp_path):
            assert mc._stdio_python() == str(venv_py)

    def test_stdio_python_falls_back_to_current_interpreter(self, tmp_path):
        from hive_cli import mcp_client as mc
        # tmp_path has no venv/ — the source-checkout case.
        with mock.patch.object(mc, "HIVE_INSTALL_DIR", tmp_path):
            assert mc._stdio_python() == sys.executable

    def test_cli_ticket_create_without_venv_uses_stdio_fallback(self, tmp_project_dir):
        """End-to-end: HTTP server down + no venv in the install dir, and an
        MCP-routed CLI command still succeeds (stdio fallback, not a
        FileNotFoundError)."""
        pytest.importorskip("mcp")  # stdio transport needs the SDK
        # Dead port → the HTTP probe fails → the stdio transport is forced.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()

        install_dir = Path(__file__).resolve().parent.parent  # repo: no venv/
        assert not (install_dir / "venv" / "bin" / "python").exists()

        company = tmp_project_dir / ".company"
        (company / "departments" / "it" / "tickets" / "backlog").mkdir(parents=True)

        env = {**os.environ,
               "HIVE_INSTALL_DIR": str(install_dir),
               "HIVE_MCP_PORT": str(dead_port),
               "PYTHONPATH": str(install_dir / "lib")}
        r = subprocess.run(
            [sys.executable, "-m", "hive_cli.main", "ticket", "create",
             "it", "feature", "medium", "stdio-fallback", "regression"],
            capture_output=True, text=True, timeout=120,
            cwd=str(tmp_project_dir), env=env)
        combined = r.stdout + r.stderr
        assert "FileNotFoundError" not in combined, combined
        assert r.returncode == 0, combined
        # Ticket files are flat JSON: departments/<dept>/tickets/<status>/<TID>.json
        tickets = list(
            (company / "departments" / "it" / "tickets" / "backlog").glob("*.json"))
        assert len(tickets) == 1
        assert json.loads(tickets[0].read_text())["status"] == "backlog"


class TestTicketListPriority:
    """E2 (0.9.0): hive ticket list --priority filters by priority."""

    @staticmethod
    def _mk(company, tid, pri, status="backlog"):
        d = company / "departments" / "it" / "tickets" / status
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{tid}.json").write_text(json.dumps(
            {"id": tid, "title": tid, "status": status,
             "type": "feature", "priority": pri}))


    @staticmethod
    def _env(tmp_path):
        # stdio MCP child inherits env: PYTHONPATH=lib is the documented
        # source mode so the child can import hive_mcp without a venv.
        # ticket._company_dir resolves the CWD's .company/ — chdir there.
        lib = Path(__file__).resolve().parent.parent / "lib"
        return {**os.environ, "PYTHONPATH": str(lib)}

    def test_list_priority_filter(self, tmp_path, monkeypatch, capsys):
        company = tmp_path / ".company"
        self._mk(company, "P-HIGH", "high")
        self._mk(company, "P-LOW", "low")
        r = subprocess.run([sys.executable, "-m", "hive_cli.main",
                            "ticket", "list", "it", "--priority", "high"],
                           capture_output=True, text=True, timeout=90,
                           cwd=str(tmp_path), env=self._env(tmp_path))
        assert r.returncode == 0, r.stdout + r.stderr
        out = r.stdout
        obj = json.loads(out[out.find("{"):])
        data = obj.get("data", obj)
        ids = [t.get("id") for t in data.get("tickets", [])]
        assert ids == ["P-HIGH"], ids

    def test_list_without_priority_lists_all(self, tmp_path, monkeypatch,
                                             capsys):
        company = tmp_path / ".company"
        self._mk(company, "A-HIGH", "high")
        self._mk(company, "B-LOW", "low")
        r = subprocess.run([sys.executable, "-m", "hive_cli.main",
                            "ticket", "list", "it"],
                           capture_output=True, text=True, timeout=90,
                           cwd=str(tmp_path), env=self._env(tmp_path))
        assert r.returncode == 0, r.stdout + r.stderr
        out = r.stdout
        obj = json.loads(out[out.find("{"):])
        data = obj.get("data", obj)
        ids = sorted(t.get("id") for t in data.get("tickets", []))
        assert ids == ["A-HIGH", "B-LOW"], ids

    def test_priority_flag_in_help(self, capsys):
        from hive_cli.main import build_parser
        with pytest.raises(SystemExit):
            build_parser().parse_args(["ticket", "list", "--help"])
        assert "--priority" in capsys.readouterr().out


class TestSelftest:
    """A2 (0.9.0): hive selftest probes the spawn path for real."""

    @staticmethod
    def _repo():
        return Path(__file__).resolve().parent.parent

    def test_healthy_spawn_path_exit_0(self, tmp_path, monkeypatch, capfd):
        for k, v in {"HIVE_PROVIDER": "openai", "HIVE_MODEL": "probe-model",
                     "HIVE_API_BASE": "http://127.0.0.1:9", "HIVE_API_KEY": "k"}.items():
            monkeypatch.setenv(k, v)
        from hive_cli.main import main
        rc = main(["selftest", str(tmp_path)])
        out = capfd.readouterr().out
        assert rc == 0, out
        assert "SELFTEST: OK" in out

    def test_missing_httpx_fails_naming_module(self, tmp_path):
        repo = self._repo()
        shadow = tmp_path / "shadow"
        shadow.mkdir()
        (shadow / "httpx.py").write_text(
            'raise ImportError("blocked for selftest")\n')
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("HIVE_")}
        env.update({"PYTHONPATH": str(shadow), "HIVE_ROOT": str(repo),
                    "HOME": str(tmp_path), "HIVE_PROVIDER": "openai",
                    "HIVE_MODEL": "m", "HIVE_API_BASE": "http://127.0.0.1:9",
                    "HIVE_API_KEY": "k"})
        r = subprocess.run(
            [sys.executable, str(repo / "lib" / "core" / "agent-harness.py"),
             "--selftest"],
            capture_output=True, text=True, timeout=60, env=env)
        assert r.returncode == 1
        assert "FAIL import httpx" in r.stdout + r.stderr

    def test_unconfigured_provider_fails_naming_keys(self, tmp_path):
        repo = self._repo()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("HIVE_")}
        env.update({"PYTHONPATH": str(repo / "lib"),
                    "HIVE_ROOT": str(repo), "HOME": str(tmp_path)})
        r = subprocess.run(
            [sys.executable, str(repo / "lib" / "core" / "agent-harness.py"),
             "--selftest"],
            capture_output=True, text=True, timeout=60, env=env)
        assert r.returncode == 1
        out = r.stdout + r.stderr
        assert "FAIL llm_providers.resolve" in out
        assert "HIVE_PROVIDER" in out or "[llm]" in out


class TestCliDocsDrift:
    """E1 (0.9.0): share/docs/CLI.md tracks the dispatcher. A subcommand
    the parser serves but the docs omit — or a command line the docs show
    the parser does not have — fails CI instead of drifting silently."""

    @staticmethod
    def _tree():
        def walk(p):
            subs = {}
            sp = getattr(p, "_subparsers", None)
            if sp is not None and sp._group_actions:
                for name, sub in sp._group_actions[0].choices.items():
                    subs[name] = walk(sub)
            return subs
        return walk(_make_parser())

    @staticmethod
    def _documented(doc):
        out = set()
        for line in doc.splitlines():
            m = re.match(r"^hive ([a-z][a-z-]+)(?: ([a-z][a-z-]+))?",
                         line.strip())
            if m:
                out.add(tuple(x for x in m.groups() if x))
        return out

    def test_every_subcommand_is_documented(self):
        tree = self._tree()
        doc = (Path(__file__).resolve().parent.parent /
               "share" / "docs" / "CLI.md").read_text()
        missing = [cmd for cmd in tree if f"hive {cmd}" not in doc]
        for cmd, subs in tree.items():
            missing += [f"{cmd} {s}" for s in subs if f"hive {cmd} {s}" not in doc]
        assert not missing, f"CLI.md does not document: {sorted(set(missing))}"

    def test_documented_commands_exist(self):
        tree = self._tree()
        doc = (Path(__file__).resolve().parent.parent /
               "share" / "docs" / "CLI.md").read_text()
        bad = []
        for parts in self._documented(doc):
            if parts[0] not in tree:
                bad.append("hive " + " ".join(parts))
            elif len(parts) == 2 and parts[1] not in tree[parts[0]]:
                bad.append("hive " + " ".join(parts))
        assert not bad, (
            f"CLI.md shows commands the dispatcher lacks: {sorted(set(bad))}")
