"""hive release ..."""

from __future__ import annotations

import json

from hive_cli import mcp_client, present
from hive_cli.commands import company_dir as _company_dir


def _print(obj):
    print(json.dumps(obj, indent=2))


def handle(args) -> int:
    cd = _company_dir()
    base = {"company_dir": cd} if cd else {}

    if args.action in ("start", "publish", "abort"):
        # release_start / release_publish / release_abort were removed under
        # the sprint-staged lifecycle (release runs as stages 6-8 of every
        # sprint, driven by sprint-lifecycle.sh). Releases are not human-initiated.
        print(f"hive release {args.action}: removed. The release pipeline runs")
        print("automatically at the end of every sprint (stages 6-8). To set the")
        print("release version, edit .company/sprints/current.json's release.version")
        print("field before stage 6 (build-and-test) runs.")
        return 1

    if args.action == "status":
        payload = dict(base)
        if getattr(args, "version", None): payload["version"] = args.version
        res = mcp_client.call_tool("release_status", payload)
        if getattr(args, "json", False):
            print(present.envelope(res))
        else:
            print(present.render_release_status(res))
        return 0

    if args.action == "list":
        res = mcp_client.call_tool("release_history",
            {**base, "limit": getattr(args, "limit", 20)})
        if getattr(args, "json", False):
            print(present.envelope(res))
        else:
            print(present.render_release_history(res))
        return 0

    print(f"unknown release action: {args.action}")
    return 1
