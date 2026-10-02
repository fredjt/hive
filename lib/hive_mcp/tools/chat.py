"""Chat / message-board tools."""

from __future__ import annotations

from hive_mcp.server_compat import Server

from hive_mcp.shell import run


def register(server: Server) -> None:
    @server.tool()
    def chat_send(
        channel: str,
        author: str,
        department: str,
        message: str,
        company_dir: str | None = None,
    ) -> dict:
        """Post a message to a chat channel.

        Channels: general | engineering | leadership | incidents | dept-<name>
        """
        return run(
            "chat-send", channel, author, department, message, company_dir=company_dir
        ).to_dict()

    @server.tool()
    def chat_read(
        channel: str,
        last: int | None = None,
        since: str | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Read messages from a channel.

        Options: `last` (most recent N), `since` (ISO 8601 timestamp).
        """
        args = [channel]
        if last:
            args += ["--last", str(last)]
        if since:
            args += ["--since", since]
        args.append("--json")
        return run("chat-read", *args, company_dir=company_dir).to_dict()
