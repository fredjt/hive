"""Round queue management tools.

Each tool shells to the corresponding script under lib/tools/round-*.sh.
Mirrors the pattern in lib/hive_mcp/tools/sprint.py.
"""

from __future__ import annotations

from hive_mcp.server_compat import Server

from hive_mcp.shell import run


def register(server: Server) -> None:
    @server.tool()
    def round_get_current(company_dir: str | None = None) -> dict:
        """Read .company/rounds/current-round.json.

        Returns the full queue: round_id, started_at, source, cursor, entries.
        """
        return run("round-status", "--current", company_dir=company_dir).to_dict()

    @server.tool()
    def round_get_next(company_dir: str | None = None) -> dict:
        """Read .company/rounds/next-plan.json (the CEO's pending plan).

        Returns {} if no plan is queued for promotion.
        """
        return run("round-status", "--next", company_dir=company_dir).to_dict()

    @server.tool()
    def round_history(limit: int = 20, company_dir: str | None = None) -> dict:
        """List the last N completed rounds with summary stats per round."""
        return run("round-status", "--history", str(limit), company_dir=company_dir).to_dict()

    @server.tool()
    def round_get(round_id: str, company_dir: str | None = None) -> dict:
        """Read a specific historical round JSON by id."""
        return run("round-status", "--round", round_id, company_dir=company_dir).to_dict()

    @server.tool()
    def round_enqueue(
        agent: str,
        dept: str,
        role: str,
        kind: str,
        ticket: str | None = None,
        prompt_extra: str | None = None,
        prompt_inline: str | None = None,
        log_to: str | None = None,
        target: str = "current",
        queued_by: str = "manual-mcp",
        company_dir: str | None = None,
    ) -> dict:
        """Append a turn entry to the queue.

        target=current appends to the in-flight round; target=next appends
        to the CEO's pending plan (will be promoted on the next round-promote).
        """
        args = [
            "--agent", agent, "--dept", dept, "--role", role, "--kind", kind,
            "--queued-by", queued_by, "--target", target,
        ]
        if ticket:
            args += ["--ticket", ticket]
        if log_to:
            args += ["--log-to", log_to]
        if prompt_extra:
            args += ["--prompt-extra", prompt_extra]
        if prompt_inline:
            # Pass via stdin to avoid shell-arg length limits
            return run("round-enqueue", *args, "--prompt-inline-stdin",
                       stdin=prompt_inline, company_dir=company_dir).to_dict()
        return run("round-enqueue", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def round_advance(company_dir: str | None = None) -> dict:
        """Force-skip the currently-running entry. Admin/debug use only."""
        return run("round-status", "--force-advance", company_dir=company_dir).to_dict()

    @server.tool()
    def round_promote_next(company_dir: str | None = None) -> dict:
        """Manually trigger round-promote: archive current round, run discovery,
        promote next-plan.json (or fall back to failsafe).

        Normally invoked automatically by sprint-lifecycle at sprint boundaries.
        """
        return run("round-promote", company_dir=company_dir).to_dict()
