"""Chat / message-board tools."""

from __future__ import annotations

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

    @server.tool()
    def chat_channels(
        company_dir: str | None = None,
    ) -> dict:
        """List every chat channel with its message count and the
        timestamp of its last message (0.17). An empty board is
        visible, not silent: channels exist from the first send, and
        a company with no chat dir at all returns ok with zero
        channels (the CLI says so)."""
        import re
        from os import environ
        from pathlib import Path

        cd = (company_dir or environ.get("COMPANY_DIR")
              or str(Path.cwd() / ".company"))
        chat_dir = Path(cd) / "chat"
        block = re.compile(
            r"\*\*\[([^\]]*)\]\s*([^\(]*)\(([^)]*)\)\*\*", re.DOTALL)
        channels = []
        if chat_dir.is_dir():
            for f in sorted(chat_dir.glob("*.md")):
                try:
                    text = f.read_text()
                except OSError:
                    continue
                stamps = block.findall(text)
                channels.append({
                    "channel": f.stem,
                    "messages": len(stamps),
                    "last_at": stamps[-1][0].strip() if stamps else None,
                })
        return {"ok": True, "data": {"channels": channels,
                                     "total_messages": sum(
                                         c["messages"] for c in channels)}}
