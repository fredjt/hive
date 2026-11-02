#!/usr/bin/env python3
"""Regenerate share/docs/MCP-TOOLS.md from the live MCP server.

The doc lists every registered tool (name, description, args). It is
GENERATED — do not hand-edit it. Run:

    python3 packaging/gen-mcp-tools-doc.py            # rewrite the doc
    python3 packaging/gen-mcp-tools-doc.py --stdout   # print, don't write

tests/test_mcp_tools.py asserts the checked-in doc matches what this
script produces, so drift fails CI.
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from hive_mcp.server import build_server  # noqa: E402


def doc_type(ann) -> str:
    if ann is None or ann is inspect.Parameter.empty:
        return "string"
    s = str(ann)
    if "list" in s:
        return "list of string"
    if s in ("str", "str | None"):
        return "string"
    if s in ("int", "int | None"):
        return "integer"
    if s in ("bool", "bool | None"):
        return "boolean"
    return s


def first_para(doc: str) -> str:
    if not doc:
        return ""
    doc = doc.strip()
    m = re.search(r"\n\s*\n|\n\s*Args:", doc)
    if m:
        doc = doc[: m.start()]
    return " ".join(doc.split())


def build(server) -> str:
    tools = server._tool_manager._tools
    lines = [
        "# Hive MCP Tools",
        "",
        f"**{len(tools)} tools registered.** Most wrap bash scripts in "
        "`lib/tools/`; a few read `.company/` state directly.",
        "",
    ]
    for name in sorted(tools):
        fn = tools[name].fn
        lines.append(f"### `{name}`")
        lines.append("")
        lines.append(first_para(inspect.getdoc(fn)))
        lines.append("")
        sig = inspect.signature(fn)
        lines.append("Args:")
        for p in sig.parameters.values():
            if p.kind not in (p.POSITIONAL_OR_KEYWORD,):
                continue
            req = " (required)" if p.default is inspect.Parameter.empty else ""
            lines.append(f"  - `{p.name}`: {doc_type(p.annotation)}{req}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    to_stdout = "--stdout" in sys.argv
    doc = build(build_server())
    if to_stdout:
        sys.stdout.write(doc)
    else:
        out = ROOT / "share" / "docs" / "MCP-TOOLS.md"
        out.write_text(doc)
        print(f"wrote {out.relative_to(ROOT)} ({doc.count(chr(10) + '### ')} tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
