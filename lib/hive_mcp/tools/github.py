"""GitHub integration tools.

Each tool shells to the matching gh-*.sh under lib/tools/. They no-op
gracefully when no remote is configured (so MCP callers always get a
structured response, never an exception).

Note: GitHub Issues mirroring has been removed. PR operations still work.
"""

from __future__ import annotations


from hive_mcp.shell import run


def register(server: Server) -> None:
    @server.tool()
    def gh_status(company_dir: str | None = None) -> dict:
        """Report current GitHub integration health: gh installed?, authed?, remote URL, default branch."""
        out = {"installed": False, "authed": False, "remote": None, "default_branch": None}
        import shutil, subprocess
        if not shutil.which("gh"):
            return out
        out["installed"] = True
        try:
            subprocess.run(["gh", "auth", "status"], check=True, capture_output=True, timeout=5)
            out["authed"] = True
        except Exception:
            pass
        try:
            r = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                out["remote"] = r.stdout.strip()
        except Exception:
            pass
        try:
            r = subprocess.run(["gh", "repo", "view", "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                out["default_branch"] = r.stdout.strip()
        except Exception:
            pass
        return out

    @server.tool()
    def gh_pr_open(branch: str, ticket_id: str, project_root: str,
                   title: str | None = None, body: str | None = None,
                   base: str | None = None, company_dir: str | None = None) -> dict:
        """Open a PR for a feature branch (idempotent)."""
        args = [branch, ticket_id, project_root]
        if title: args += ["--title", title]
        if body:  args += ["--body",  body]
        if base:  args += ["--base",  base]
        return run("gh-pr-open", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def gh_pr_merge(branch: str, project_root: str, method: str = "merge",
                    company_dir: str | None = None) -> dict:
        """Merge an open PR via gh (verification IC's GO path)."""
        return run("gh-pr-merge", branch, project_root, "--method", method,
                   company_dir=company_dir).to_dict()

