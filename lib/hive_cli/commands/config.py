"""hive config ..."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


HIVE_ROOT = Path(os.environ.get("HIVE_ROOT", Path(__file__).resolve().parents[3]))
USER_CONFIG = Path.home() / ".config" / "hive" / "config.toml"
DEFAULT_CONFIG = HIVE_ROOT / "etc" / "config.defaults.toml"


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
        # Simple line-based merge into ~/.config/hive/config.toml
        USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        if not USER_CONFIG.is_file():
            USER_CONFIG.write_text("# User overrides for ~/.local/share/hive/etc/config.defaults.toml\n")
        # NOTE: simplistic - appends a "[section]\nkey = value" if the key has a dot.
        # For robust editing, the user can `hive config edit`.
        if "." in args.key:
            section, k = args.key.rsplit(".", 1)
            with USER_CONFIG.open("a") as f:
                f.write(f"\n[{section}]\n{k} = \"{args.value}\"\n")
        else:
            with USER_CONFIG.open("a") as f:
                f.write(f"\n{args.key} = \"{args.value}\"\n")
        print(f"Wrote {args.key} = {args.value} to {USER_CONFIG}")
        return 0
    if args.action == "edit":
        editor = os.environ.get("EDITOR", "vi")
        USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        if not USER_CONFIG.is_file():
            USER_CONFIG.write_text("# User overrides for hive config\n")
        return subprocess.call([editor, str(USER_CONFIG)])
    return 1
