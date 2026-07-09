"""Small-model classification helpers (llm-tiny family)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from hive_mcp.shell import run


def register(server: FastMCP) -> None:
    @server.tool()
    def llm_tiny(
        prompt: str,
        choices: str | None = None,
        yesno: bool = False,
        json_output: bool = False,
    ) -> dict:
        """Run a prompt against the small CPU model (llm-tiny).

        At most one of `choices`, `yesno`, `json_output` may be set.
          - `choices`: pipe-separated options (e.g. "low|medium|high|critical")
          - `yesno`: returns "yes" or "no"
          - `json_output`: returns parsed JSON in `data`
        Otherwise returns free-form text in `stdout`.
        """
        args: list[str] = []
        if choices:
            args += ["--choice", choices]
        elif yesno:
            args.append("--yesno")
        elif json_output:
            args.append("--json")
        args.append(prompt)
        return run("llm-tiny", *args).to_dict()

    @server.tool()
    def classify_ticket(request: str, company_dir: str | None = None) -> dict:
        """Classify a free-text user request into (type, department) via small model.

        Returns: {"type": "bug|feature|feedback", "department": "...", "confidence": "...", "source": "..."}
        Falls back to {"type": "feature", "department": "unknown"} if small model is unreachable.
        """
        return run("classify-ticket", request, company_dir=company_dir).to_dict()

    @server.tool()
    def triage_test_failure(test_output: str) -> dict:
        """Classify a test failure as flaky | real | unknown via small model.

        Used by verification short-circuit logic to skip spawning a verification
        IC for genuinely flaky failures.
        """
        return run("triage-test-failure", stdin=test_output).to_dict()

    @server.tool()
    def narrate_status(dept: str, company_dir: str | None = None) -> dict:
        """Turn a department's derived status.json into a one-sentence narrative."""
        return run("narrate-status", dept, company_dir=company_dir).to_dict()

    @server.tool()
    def summarize_thread(
        channel: str,
        last: int = 20,
        company_dir: str | None = None,
    ) -> dict:
        """Condense recent activity in a chat channel into a 2-sentence digest."""
        return run(
            "summarize-thread", channel, "--last", str(last), company_dir=company_dir
        ).to_dict()
