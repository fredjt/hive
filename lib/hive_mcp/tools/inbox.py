"""Inbox / outbox tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from hive_mcp.shell import run


def register(server: FastMCP) -> None:
    @server.tool()
    def inbox_write(
        type: str = "auto",
        dept: str = "auto",
        content: str = "",
        company_dir: str | None = None,
    ) -> dict:
        """File a user request to the inbox.

        type: auto | bug | feature | feedback   (auto uses small-model classification)
        dept: auto | <dept-name>                (auto uses small-model classification)

        Returns the inbox entry number.
        """
        return run(
            "inbox-write",
            type,
            "--dept",
            dept,
            "--content",
            content,
            company_dir=company_dir,
        ).to_dict()

    @server.tool()
    def inbox_process(company_dir: str | None = None, dry_run: bool = False) -> dict:
        """Process pending inbox entries (route to dept boards / expedited)."""
        args = []
        if dry_run:
            args.append("--dry-run")
        return run("inbox-process", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def outbox_write(
        type: str,
        content: str = "",
        company_dir: str | None = None,
    ) -> dict:
        """Write a CEO notification/approval/question to the outbox.

        type: notification | approval | question
        """
        return run(
            "outbox-write", type, "--content", content, company_dir=company_dir
        ).to_dict()

    @server.tool()
    def outbox_archive(days: int = 7, company_dir: str | None = None) -> dict:
        """Archive outbox entries older than `days` (preserves pending-approvals.md)."""
        return run("outbox-archive", "--days", str(days), company_dir=company_dir).to_dict()
