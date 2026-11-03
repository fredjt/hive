"""hive config ..."""

from __future__ import annotations

import os
import re
import subprocess
import sys
try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Python 3.10 backport
from pathlib import Path


HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", Path(__file__).resolve().parents[3]))
USER_CONFIG = Path.home() / ".config" / "hive" / "config.toml"
DEFAULT_CONFIG = HIVE_ROOT / "etc" / "config.defaults.toml"
TEMPLATE = HIVE_ROOT / "etc" / "config.template.toml"


def _template_seed() -> str:
    """Seed text for a new user config: the tracked template, so a fresh
    file carries every key with its working default (C1)."""
    if TEMPLATE.is_file():
        return TEMPLATE.read_text()
    return "# User overrides for hive config\n"


def _toml_value(raw: str) -> str:
    """Render a CLI-supplied value as TOML: bools/ints bare, strings quoted."""
    if raw in ("true", "false"):
        return raw
    if re.fullmatch(r"-?\d+", raw):
        return raw
    escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _upsert(text: str, section: str, key: str, value: str) -> str:
    """Insert or replace `key = value` inside [section] — never a duplicate
    table header (C2). Returns the new file text."""
    lines = text.splitlines(keepends=True)
    header_re = re.compile(r"^\[" + re.escape(section) + r"\]\s*(?:#.*)?$")
    key_re = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    section_idx = None
    in_section = False
    for i, ln in enumerate(lines):
        if header_re.match(ln):
            in_section = True
            section_idx = i
            continue
        if in_section:
            if ln.strip().startswith("["):
                in_section = False
            elif key_re.match(ln):
                indent = ln[: len(ln) - len(ln.lstrip())]
                lines[i] = f"{indent}{key} = {value}\n"
                return "".join(lines)
    if section_idx is not None:
        lines.insert(section_idx + 1, f"{key} = {value}\n")
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"\n[{section}]\n{key} = {value}\n")
    return "".join(lines)


def _load() -> dict:
    config = {}
    if DEFAULT_CONFIG.is_file():
        config = tomllib.loads(DEFAULT_CONFIG.read_text())
    if USER_CONFIG.is_file():
        user = tomllib.loads(USER_CONFIG.read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(config.get(k), dict):
                config[k].update(v)
            else:
                config[k] = v
    return config


def _walk(d: dict, key: str):
    parts = key.split(".")
    cur = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def handle(args) -> int:
    if args.action == "show":
        import json as _json
        print(_json.dumps(_load(), indent=2))
        return 0
    if args.action == "get":
        v = _walk(_load(), args.key)
        if v is None:
            print(f"(unset: {args.key})", file=sys.stderr)
            return 1
        print(v if not isinstance(v, (dict, list)) else __import__('json').dumps(v))
        return 0
    if args.action == "set":
        if "." not in args.key or not args.key.partition(".")[0] or not args.key.partition(".")[2]:
            print(f"error: key must be 'section.key' (e.g. llm.provider), "
                  f"got {args.key!r}", file=sys.stderr)
            return 1
        section, k = args.key.rsplit(".", 1)
        USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        base = USER_CONFIG.read_text() if USER_CONFIG.is_file() else _template_seed()
        new_text = _upsert(base, section, k, _toml_value(args.value))
        try:
            tomllib.loads(new_text)
        except Exception as e:
            print(f"error: refusing to write invalid TOML ({e}); "
                  f"file left unchanged", file=sys.stderr)
            return 1
        USER_CONFIG.write_text(new_text)
        print(f"Wrote {section}.{k} = {args.value} to {USER_CONFIG}")
        return 0
    if args.action == "edit":
        editor = os.environ.get("EDITOR", "vi")
        USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        if not USER_CONFIG.is_file():
            # Seed from the tracked template so the editor shows every key
            # with its working default — not a blank comment (C2).
            USER_CONFIG.write_text(_template_seed())
        return subprocess.call([editor, str(USER_CONFIG)])
    return 1
