"""Report-writing tools (research findings, test results, meeting records)."""

from __future__ import annotations

from hive_mcp.server_compat import Server

from hive_mcp.shell import run


def register(server: Server) -> None:
    @server.tool()
    def report_write(
        type: str,
        filename: str,
        content: str,
        company_dir: str | None = None,
    ) -> dict:
        """Write a report. Filename may include subdirectories (e.g.
        "2026-Q2/budget.md") — the parent directories are created
        automatically.

        Recognized types (all surfaced in the dashboard's Reports panel):
          marketing                  -> .company/marketing/reports/
          research                   -> .company/research/ongoing/
          meeting                    -> .company/meetings/history/
          sprint-review              -> .company/meetings/sprint-reviews/
          productivity | hiring      -> .company/departments/hr/reports/
          dept:<dept-name>           -> .company/departments/<dept>/reports/

        Path-like type (fallback for anything not in the list above):
          any/path/like/this         -> .company/any/path/like/this/

        VPs writing end-of-sprint reports should use type="sprint-review"
        with filename="<sprint_id>-<dept>.md".

        Returns the absolute path of the written file.
        """
        return run(
            "report-write",
            type,
            filename,
            "--stdin",
            stdin=content,
            company_dir=company_dir,
        ).to_dict()

    @server.tool()
    def research_findings(
        ticket_id: str,
        content: str,
        append: bool = False,
        company_dir: str | None = None,
    ) -> dict:
        """Write R&D research findings to .company/research/ongoing/<ticket-id>-findings.md."""
        args = [ticket_id, "--stdin"]
        if append:
            args.append("--append")
        return run("research-findings", *args, stdin=content, company_dir=company_dir).to_dict()

    @server.tool()
    def test_results(
        dept: str,
        component: str,
        content: str,
        format: str = "markdown",
        company_dir: str | None = None,
    ) -> dict:
        """Write IC-tester output for a component."""
        return run(
            "test-results",
            dept,
            component,
            "--stdin",
            "--format",
            format,
            stdin=content,
            company_dir=company_dir,
        ).to_dict()

    @server.tool()
    def meeting_record(
        type: str,
        timestamp: str,
        content: str,
        company_dir: str | None = None,
    ) -> dict:
        """Record meeting notes.

        type: standup | sprint-review | sprint-planning | sprint-retro | emergency
        timestamp: e.g. "2026-04-27-15-00"
        """
        return run(
            "meeting-record",
            type,
            timestamp,
            "--stdin",
            stdin=content,
            company_dir=company_dir,
        ).to_dict()
