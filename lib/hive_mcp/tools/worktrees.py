"""Git worktree tools."""

from __future__ import annotations

from hive_mcp.server_compat import Server

from hive_mcp.shell import run


def register(server: Server) -> None:
    @server.tool()
    def worktree_create(ticket_id: str, project_root: str) -> dict:
        """Create a git worktree for a ticket. Branch: `feature/<ticket-id>`."""
        return run("git-worktree", "create", ticket_id, project_root).to_dict()

    @server.tool()
    def worktree_merge(ticket_id: str, project_root: str) -> dict:
        """Merge a ticket's worktree branch into main and remove the worktree."""
        return run("git-worktree", "merge", ticket_id, project_root).to_dict()

    @server.tool()
    def worktree_list(project_root: str) -> dict:
        """List all active worktrees with their branches."""
        return run("git-worktree", "list", project_root).to_dict()

    @server.tool()
    def worktree_cleanup(project_root: str) -> dict:
        """Remove stale worktrees (their branches were merged or deleted)."""
        return run("git-worktree", "cleanup", project_root).to_dict()

    @server.tool()
    def worktree_auto_merge(project_root: str, dry_run: bool = False) -> dict:
        """Find worktrees whose tickets are in done/ and merge them."""
        args = [project_root]
        if dry_run:
            args.append("--dry-run")
        else:
            args.append("--quiet")
        return run("worktree-merger", *args).to_dict()
