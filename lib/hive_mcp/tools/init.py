"""Workspace initialization tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from hive_mcp.shell import run


def register(server: FastMCP) -> None:
    @server.tool()
    def init_workspace(project_root: str) -> dict:
        """Create a fresh `.company/` workspace in `project_root`.

        Idempotent: if `.company/` already exists, this returns success without
        modifying anything. Also adds `.company/` and `.worktrees/` to the
        project's `.gitignore`.
        """
        return run("init-workspace", project_root).to_dict()

    @server.tool()
    def init_manifest(
        project_name: str,
        auto_mode: bool = False,
        mode: str = "build",
        company_dir: str | None = None,
    ) -> dict:
        """Create the initial `manifest.json` for a project.

        `mode` is one of `build` (initial construction) or `perpetual`.
        Returns the manifest JSON.
        """
        return run(
            "init-manifest",
            project_name,
            "true" if auto_mode else "false",
            "--mode",
            mode,
            company_dir=company_dir,
        ).to_dict()

    @server.tool()
    def init_brief(
        brief: str,
        clarifications: str | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Write the project brief to `.company/brief.md`."""
        args = [brief]
        if clarifications:
            args += ["--clarifications", clarifications]
        return run("init-brief", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def init_chat_channels(
        channels: list[str] | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Create chat channel files. Defaults to general/engineering/leadership/incidents."""
        return run("init-chat-channels", *(channels or []), company_dir=company_dir).to_dict()

    @server.tool()
    def init_meeting_schedule(company_dir: str | None = None) -> dict:
        """Create the empty `meetings/schedule.json`."""
        return run("init-meeting-schedule", company_dir=company_dir).to_dict()

    @server.tool()
    def validate_workspace(project_root: str) -> dict:
        """Validate `.company/` integrity in `project_root`. Returns gate-style report."""
        return run("validate-workspace", project_root).to_dict()
