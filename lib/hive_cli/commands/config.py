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


def _user_config_path() -> Path:
    """The effective user-config location (A2/A3): the HIVE_CONFIG env var
    (the one spelling doctor and the publish runner read) wins over the
    default ~/.config/hive/config.toml."""
    return Path(os.environ.get("HIVE_CONFIG") or USER_CONFIG)


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
    user_path = _user_config_path()
    if user_path.is_file():
        user = tomllib.loads(user_path.read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(config.get(k), dict):
                config[k].update(v)
            else:
                config[k] = v
    return config


def _user_cfg(path: Path | None = None) -> dict:
    """Parse the user config file ({} when missing/unparseable)."""
    path = path or _user_config_path()
    if path.is_file():
        try:
            return tomllib.loads(USER_CONFIG.read_text()) or {}
        except Exception:
            return {}
    return {}


def _unknown_keys() -> list:
    """A1: unknown sections/keys in the user config vs the defaults schema."""
    core = HIVE_ROOT / "lib" / "core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from llm_providers import unknown_config_keys, load_defaults
    schema = load_defaults(HIVE_ROOT)
    if not schema:
        return []
    return unknown_config_keys(_user_cfg(), schema)


def _walk(d: dict, key: str):
    parts = key.split(".")
    cur = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def handle(args) -> int:
    user_cfg_path = _user_config_path()
    if args.action == "path":
        # A3: the where-is-my-config question, one line.
        if user_cfg_path.is_file():
            print(f"{user_cfg_path} (created at install — "
                  f"or by 'hive config edit'; run 'hive config edit' to change it)")
        else:
            print(f"{user_cfg_path} (missing — run 'hive config edit' to create it)")
        return 0
    if args.action == "show":
        import json as _json
        merged = _load()
        unknowns = _unknown_keys()
        if unknowns:
            merged["_unknown_keys"] = unknowns
            for u in unknowns:
                print(f"warning: unknown key {u} — edit {user_cfg_path}",
                      file=sys.stderr)
        print(_json.dumps(merged, indent=2))
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
        user_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        base = user_cfg_path.read_text() if user_cfg_path.is_file() else _template_seed()
        new_text = _upsert(base, section, k, _toml_value(args.value))
        try:
            tomllib.loads(new_text)
        except Exception as e:
            print(f"error: refusing to write invalid TOML ({e}); "
                  f"file left unchanged", file=sys.stderr)
            return 1
        user_cfg_path.write_text(new_text)
        print(f"Wrote {section}.{k} = {args.value} to {user_cfg_path}")
        return 0
    if args.action == "edit":
        editor = os.environ.get("EDITOR", "vi")
        user_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        if not user_cfg_path.is_file():
            # Seed from the tracked template so the editor shows every key
            # with its working default — not a blank comment (C2).
            user_cfg_path.write_text(_template_seed())
        return subprocess.call([editor, str(user_cfg_path)])
    return 1
