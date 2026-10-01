"""Local classification helpers (keyword heuristics, no external model).

Replaced the llm-tiny.sh small-model provider with local bash/python
implementations. These tools wrap the same scripts but now use keyword
heuristics instead of HTTP calls to an external tiny model.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from hive_mcp.shell import run


def register(server: FastMCP) -> None:
    @server.tool()
    def classify_ticket(request: str, company_dir: str | None = None) -> dict:
        """Classify a free-text user request into (type, department).

        Uses local keyword heuristics — no external model needed.
        Returns: {"type": "bug|feature|feedback", "department": "...", "confidence": "high|low"}
        """
        return run("classify-ticket", request, company_dir=company_dir).to_dict()

    @server.tool()
    def triage_test_failure(test_name: str, test_output: str) -> dict:
        """Classify a test failure as flaky | real | setup | skip.

        Uses local keyword heuristics — no external model needed.
        Used by verification short-circuit logic to skip spawning a verification
        IC for genuinely flaky failures.
        """
        return run("triage-test-failure", test_name, test_output).to_dict()

    @server.tool()
    def narrate_status(dept: str, company_dir: str | None = None) -> dict:
        """Turn a department's derived status.json into a one-sentence narrative.

        Uses templated summary — no external model needed.
        """
        return run("narrate-status", dept, company_dir=company_dir).to_dict()

    @server.tool()
    def summarize_thread(
        channel: str,
        last: int = 20,
        company_dir: str | None = None,
    ) -> dict:
        """Condense recent activity in a chat channel into a short summary.

        Uses last substantive messages — no external model needed.
        """
        return run(
            "summarize-thread", channel, "--last", str(last), company_dir=company_dir
        ).to_dict()
