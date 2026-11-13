"""Sprint management tools."""

from __future__ import annotations

from hive_mcp.shell import run


def register(server: Server) -> None:
    @server.tool()
    def sprint_fill(
        min_active: int = 3,
        dept: str | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Promote backlog tickets into sprint-current to maintain `min_active` per dept.

        Uses `prioritize_backlog` (deterministic priority+age ordering) to
        pick tickets when there are ties at the same priority. Idempotent and
        safe to run frequently.
        """
        args = ["--min", str(min_active)]
        if dept:
            args += ["--dept", dept]
        return run("sprint-fill", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def sprint_progress(
        archive_done: bool = False,
        company_dir: str | None = None,
    ) -> dict:
        """Show sprint ticket progression. With `archive_done=True`, also moves
        done tickets into archive/."""
        args = []
        if archive_done:
            args.append("--archive")
        args.append("--json")
        return run("sprint-progress", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def prioritize_backlog(
        dept: str,
        limit: int = 1,
        company_dir: str | None = None,
    ) -> dict:
        """Return the top N backlog tickets for a department, smart-prioritized.

        Deterministic priority+age ordering (ties broken by age) — no
        external model involved (the small-model path was retired).
        """
        return run(
            "prioritize-backlog",
            dept,
            "--limit",
            str(limit),
            company_dir=company_dir,
        ).to_dict()
