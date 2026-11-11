"""hive CLI entry point.

Subcommands:
  init, start, stop, status, dashboard, restart, ticket, chat, agent, gates,
  backlog, config, doctor, selftest, killall, llm, gh, release,
  tool, tools, version

Bare `hive` (no args) launches the dashboard for the project in CWD.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Bootstrap: lib/ must be on sys.path before hive_paths (which lives in
# lib/) can be imported. This single parent.parent hop is the one
# unavoidable path derivation in the Python codebase (G9) — everything
# else resolves through hive_paths.
_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from hive_paths import version_file  # noqa: E402

from hive_cli.commands import (  # noqa: E402
    company, dashboard, init, ticket, chat, agent, gates, backlog,
    config, doctor, tool, killall, llm, selftest,
    gh as gh_cmd, release as release_cmd,
    restart,
)


_VERSION_FILE = version_file()
try:
    VERSION = _VERSION_FILE.read_text().strip()
except FileNotFoundError:
    VERSION = "0.1.0+unknown"


def _add_init(sub) -> None:
    p = sub.add_parser("init", help="Initialize a hive workspace in <project-dir>")
    p.add_argument("project_dir", nargs="?", default=".")
    p.add_argument("--brief", help="Project brief text")
    p.add_argument("--brief-file", help="Read brief from file")
    p.add_argument("--auto", action="store_true", help="Skip prompts")
    p.add_argument("--name", help="Override project name (defaults to dir basename)")
    p.add_argument("--github", default="private",
                   help="GitHub setup mode: 'private' (default — auto-create private repo via gh CLI), "
                        "'public' (auto-create public), 'skip' (no GitHub — PR merges will fail), "
                        "or a git URL like git@github.com:owner/repo.git to set as origin.")
    p.set_defaults(_handler=init.handle)


def _add_start(sub) -> None:
    p = sub.add_parser("start", help="Spawn all background daemons")
    p.add_argument("project_dir", nargs="?", default=".")
    p.set_defaults(_handler=company.handle_start)


def _add_stop(sub) -> None:
    p = sub.add_parser("stop", help="Kill all daemon processes")
    p.add_argument("project_dir", nargs="?", default=".")
    p.set_defaults(_handler=company.handle_stop)


def _add_status(sub) -> None:
    p = sub.add_parser("status", help="Show derived department status")
    p.add_argument("project_dir", nargs="?", default=".")
    p.add_argument("--dept", help="Filter to one department")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of formatted text")
    p.set_defaults(_handler=company.handle_status)


def _add_dashboard(sub) -> None:
    p = sub.add_parser("dashboard", help="Launch the web dashboard")
    p.add_argument("project_dir", nargs="?", default=".")
    p.add_argument("--port", type=int, default=3456)
    p.add_argument("--no-open", action="store_true", help="Don't auto-open browser")
    p.set_defaults(_handler=dashboard.handle)


def _add_ticket(sub) -> None:
    p = sub.add_parser("ticket", help="Ticket operations")
    sp = p.add_subparsers(dest="action", required=True)

    pc = sp.add_parser("create", help="Create a new ticket")
    pc.add_argument("dept"); pc.add_argument("type"); pc.add_argument("priority")
    pc.add_argument("title"); pc.add_argument("description")
    pc.add_argument("--created-by", default="user"); pc.add_argument("--source", default="manual")
    pc.add_argument("--estimate")
    pc.add_argument("--json", action="store_true",
                    help="Print the raw MCP envelope (machine contract; human by default)")

    pg = sp.add_parser("get", help="Read a ticket")
    pg.add_argument("dept"); pg.add_argument("ticket_id")
    pg.add_argument("--json", action="store_true",
                    help="Print the raw MCP envelope (machine contract; human by default)")

    pl = sp.add_parser("list", help="List tickets")
    pl.add_argument("--priority", help="Filter by priority (critical|high|medium|low)")
    pl.add_argument("dept", nargs="?")
    pl.add_argument("status", nargs="?")
    pl.add_argument("--json", action="store_true")

    pm = sp.add_parser("move", help="Move a ticket between statuses")
    pm.add_argument("dept"); pm.add_argument("ticket_id"); pm.add_argument("new_status")
    pm.add_argument("comment"); pm.add_argument("author")
    pm.add_argument("reason", nargs="?"); pm.add_argument("--force", action="store_true")
    pm.add_argument("--json", action="store_true",
                    help="Print the raw MCP envelope (machine contract; human by default)")

    pcl = sp.add_parser("claim", help="Claim a ticket for an IC")
    pcl.add_argument("dept"); pcl.add_argument("ticket_id"); pcl.add_argument("role")
    pcl.add_argument("agent_id"); pcl.add_argument("agent_name")
    pcl.add_argument("--worktree-path")
    pcl.add_argument("--json", action="store_true",
                     help="Print the raw MCP envelope (machine contract; human by default)")

    pco = sp.add_parser("comment", help="Add a comment")
    pco.add_argument("dept"); pco.add_argument("ticket_id"); pco.add_argument("author"); pco.add_argument("comment")
    pco.add_argument("--json", action="store_true",
                     help="Print the raw MCP envelope (machine contract; human by default)")

    pa = sp.add_parser("approve", help="Verification IC approval (>=30 char rationale)")
    pa.add_argument("dept"); pa.add_argument("ticket_id"); pa.add_argument("verifier")
    pa.add_argument("rationale")
    pa.add_argument("--json", action="store_true",
                    help="Print the raw MCP envelope (machine contract; human by default)")

    p.set_defaults(_handler=ticket.handle)


def _add_chat(sub) -> None:
    p = sub.add_parser("chat", help="Chat operations")
    sp = p.add_subparsers(dest="action", required=True)

    ps = sp.add_parser("send", help="Send a message")
    ps.add_argument("channel"); ps.add_argument("author")
    ps.add_argument("department"); ps.add_argument("message")

    pr = sp.add_parser("read", help="Read messages")
    pr.add_argument("channel")
    pr.add_argument("--last", type=int)
    pr.add_argument("--since")
    pr.add_argument("--json", action="store_true")

    p.set_defaults(_handler=chat.handle)


def _add_agent(sub) -> None:
    p = sub.add_parser("agent", help="Spawn / run agents")
    sp = p.add_subparsers(dest="action", required=True)

    pc = sp.add_parser("spawn-ceo", help="Spawn the CEO for initial build")
    pc.add_argument("project_dir", nargs="?", default=".")
    pc.add_argument("--mode", default="FRESH", choices=["FRESH", "RESUME"])

    pv = sp.add_parser("run-vp", help="Run one cycle of a VP agent")
    pv.add_argument("dept")
    pv.add_argument("project_dir", nargs="?", default=".")

    # NOTE: a `verify` subcommand used to exist here; it called the
    # agent_verify MCP tool, which was removed under the sprint-staged
    # lifecycle (verification ICs are spawned by sprint-lifecycle during
    # the verify stage). Do not re-add it — see
    # tests/test_cli.py::test_agent_verify_subcommand_removed.

    p.set_defaults(_handler=agent.handle)


def _add_gates(sub) -> None:
    p = sub.add_parser("gates", help="Verification gates")
    sp = p.add_subparsers(dest="action", required=True)
    pr = sp.add_parser("run", help="Run gates against in-review tickets in a dept")
    pr.add_argument("dept"); pr.add_argument("project_dir", nargs="?", default=".")
    pc = sp.add_parser("check", help="Read most recent gate results for a ticket")
    pc.add_argument("dept"); pc.add_argument("ticket_id")
    sp.add_parser("status", help="Show gate state for every in-review ticket (debug stuck tickets)")
    p.set_defaults(_handler=gates.handle)


def _add_backlog(sub) -> None:
    p = sub.add_parser("backlog", help="Backlog operations")
    sp = p.add_subparsers(dest="action", required=True)
    pp = sp.add_parser("prioritize", help="Get top N tickets from backlog")
    pp.add_argument("dept"); pp.add_argument("--limit", type=int, default=1)
    p.set_defaults(_handler=backlog.handle)



def _add_config(sub) -> None:
    p = sub.add_parser("config", help="Read / write hive config")
    sp = p.add_subparsers(dest="action", required=True)
    pg = sp.add_parser("get"); pg.add_argument("key")
    psr = sp.add_parser("set"); psr.add_argument("key"); psr.add_argument("value")
    sp.add_parser("edit", help="Open ~/.config/hive/config.toml in $EDITOR")
    sp.add_parser("show", help="Print effective config")
    sp.add_parser("path", help="Print the effective user-config path and its state")
    p.set_defaults(_handler=config.handle)


def _add_doctor(sub) -> None:
    p = sub.add_parser("doctor", help="Diagnose installation and dependencies")
    p.add_argument("project_dir", nargs="?", default=".", help="Project root to check .company/ daemon health in (default: .)")
    p.add_argument("--deep", action="store_true",
                   help="Also probe LLM endpoint reachability (uses the network)")
    p.add_argument("--json", action="store_true",
                   help="C1: emit a machine-readable JSON dump of every check (exit code unchanged)")
    p.set_defaults(_handler=doctor.handle)


def _add_selftest(sub) -> None:
    p = sub.add_parser("selftest", help="Probe the agent spawn path (no LLM call)")
    p.add_argument("project_dir", nargs="?", default=".", help="Project root")
    p.set_defaults(_handler=selftest.handle)


def _add_killall(sub) -> None:
    p = sub.add_parser("killall", help="Emergency: kill ALL hive processes (sledgehammer)")
    p.add_argument("--dry-run", action="store_true", help="List what would be killed without doing it")
    p.set_defaults(_handler=killall.handle)


def _add_llm(sub) -> None:
    p = sub.add_parser("llm", help="LLM usage (stats/tail) + endpoint test")
    sp = p.add_subparsers(dest="action", required=True)
    s = sp.add_parser("stats", help="Windowed call/token/cost totals")
    s.add_argument("--window", default="24h", choices=["1h", "24h", "7d", "all"],
                   help="time window to sum (default: 24h)")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    pt = sp.add_parser("tail", help="Tail recent LLM calls")
    pt.add_argument("-n", type=int, default=20, help="number of recent calls to show")
    sp.add_parser("test", help="One-shot round trip through the configured endpoint (B1)")
    p.set_defaults(_handler=llm.handle)


def _add_gh(sub) -> None:
    p = sub.add_parser("gh", help="GitHub integration (status / issues / PR ops)")
    sp = p.add_subparsers(dest="action", required=True)
    sp.add_parser("status", help="gh CLI / auth / remote / default branch")
    pr = sp.add_parser("pr", help="PR operations")
    pr_sp = pr.add_subparsers(dest="pr_action", required=True)
    po = pr_sp.add_parser("open",   help="Open a PR for a branch")
    po.add_argument("branch"); po.add_argument("ticket")
    pm = pr_sp.add_parser("merge",  help="Merge an open PR (verification IC GO path)")
    pm.add_argument("branch"); pm.add_argument("--method", default="merge", choices=["merge","squash","rebase"])
    # Item 5: per-company push/PR opt-in (the field run's company was
    # never asked to touch a remote).
    pu = sp.add_parser("push", help="Per-company push/PR opt-in")
    pu_sp = pu.add_subparsers(dest="push_action", required=True)
    pu_sp.add_parser("enable", help="Allow this company to push branches and open/merge PRs")
    pu_sp.add_parser("disable", help="Disable pushes/PRs for this company")

    # No `reject`: PR close is a scripted side-effect of rejecting the
    # ticket (ticket-move ... done rejected -> gh-pr-close.sh). The old
    # gh-pr-reject.sh is a deprecation shim.
    p.set_defaults(_handler=gh_cmd.handle)


def _add_release(sub) -> None:
    p = sub.add_parser("release", help="Release pipeline (gates → build → tag → adversarial verify → publish)")
    sp = p.add_subparsers(dest="action", required=True)
    ps = sp.add_parser("start",  help="Drive release pipeline through enqueue of release-ic")
    ps.add_argument("version", help="vX.Y.Z (or X.Y.Z)")
    pst = sp.add_parser("status", help="Print a release record")
    pst.add_argument("version", nargs="?")
    pl = sp.add_parser("list",   help="List recent releases")
    pl.add_argument("--limit", type=int, default=20)
    pp = sp.add_parser("publish", help="Commit GO/NO-GO (release-ic uses this; admin can too)")
    pp.add_argument("verdict", choices=["GO","NO-GO"])
    pp.add_argument("version")
    pp.add_argument("rationale")
    pa = sp.add_parser("abort", help="Mark a release failed and file an IT ticket")
    pa.add_argument("version"); pa.add_argument("reason")
    p.set_defaults(_handler=release_cmd.handle)


def _add_tool(sub) -> None:
    p = sub.add_parser("tool", help="Invoke a single MCP tool by name")
    p.add_argument("name", help="Tool name (e.g. ticket_create)")
    p.add_argument("--arg", action="append", default=[],
                   help="Argument as key=value (repeatable). Values parsed as JSON if possible.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(_handler=tool.handle_invoke)

    p2 = sub.add_parser("tools", help="List MCP tools")
    p2.add_argument("--json", action="store_true")
    p2.set_defaults(_handler=tool.handle_list)


def _add_restart(sub) -> None:
    p = sub.add_parser("restart", help="Restart the web dashboard (agents/MCP untouched)")
    p.add_argument("project_dir", nargs="?", default=".")
    p.add_argument("--port", type=int, default=3456, help="Dashboard port (default: 3456)")
    p.set_defaults(_handler=restart.handle)


def _add_version(sub) -> None:
    p = sub.add_parser("version", help="Show version")
    p.set_defaults(_handler=lambda args: print(f"hive {VERSION}"))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="hive",
        description="Hive — orchestrate a virtual company of AI agents on a project.",
    )
    ap.add_argument("--mcp-host", default=os.environ.get("HIVE_MCP_HOST", "127.0.0.1"))
    ap.add_argument("--mcp-port", type=int, default=int(os.environ.get("HIVE_MCP_PORT", "7891")))
    sub = ap.add_subparsers(dest="cmd")

    _add_init(sub)
    _add_start(sub)
    _add_stop(sub)
    _add_status(sub)
    _add_dashboard(sub)
    _add_ticket(sub)
    _add_chat(sub)
    _add_agent(sub)
    _add_gates(sub)
    _add_backlog(sub)

    _add_config(sub)
    _add_doctor(sub)
    _add_selftest(sub)
    _add_killall(sub)
    _add_llm(sub)
    _add_gh(sub)
    _add_release(sub)
    _add_restart(sub)
    _add_tool(sub)
    _add_version(sub)

    return ap


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 1

    # Bare `hive` opens dashboard
    if not args.cmd:
        return dashboard.handle(argparse.Namespace(
            project_dir=".", port=3456, no_open=False,
        )) or 0

    os.environ["HIVE_MCP_HOST"] = args.mcp_host
    os.environ["HIVE_MCP_PORT"] = str(args.mcp_port)

    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.print_help()
        return 1
    rc = handler(args)
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
