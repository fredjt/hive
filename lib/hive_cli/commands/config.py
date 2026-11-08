"""hive config ..."""

from __future__ import annotations

import os
import re
import subprocess
import sys
# T1 (0.13) throwaway red demo — DO NOT MERGE: breaks the TOML parser
# import (the version-sensitive tomli-fallback class) to prove on CI
# that EVERY matrix row catches it via TestConfigSetUpsert (full on
# 3.12, subset on 3.10 + 3.13) without running the full suite on the
# subset rows.
raise ImportError(
    "T1 red demo: the tomli fallback is broken (throwaway PR — do not "
    "merge)")
from pathlib import Path


from hive_paths import install_root

HIVE_ROOT = install_root()
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
    """Render a CLI-supplied value as TOML: bools/ints/floats bare,
    strings quoted."""
    if raw in ("true", "false"):
        return raw
    if re.fullmatch(r"-?\d+", raw):
        return raw
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return raw
    escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_list(raw: str) -> str:
    """F1: render a CLI-supplied value for a list-typed key as a TOML
    array. A JSON array wins (['a', 'b'] as written on the CLI); a
    comma-separated value falls back to one item per field; anything
    else is a single-item list. Raises ValueError for items TOML cannot
    hold — the caller surfaces it instead of writing the file."""
    import json as _json
    text = raw.strip()
    try:
        parsed = _json.loads(text)
    except Exception:
        parsed = None
    if not isinstance(parsed, list):
        if "," in text:
            parsed = [p.strip().strip("'\"") for p in text.split(",")]
        else:
            parsed = [text]
    if not all(isinstance(i, (str, int, float, bool)) for i in parsed):
        raise ValueError("list items must be strings, numbers or booleans")
    items = []
    for i in parsed:
        if isinstance(i, bool):
            items.append("true" if i else "false")
        else:
            items.append(_toml_value(str(i)))
    return "[" + ", ".join(items) + "]"


def _default_type(key: str):
    """F1: the type of `key` in the defaults schema — 'list' when
    etc/config.defaults.toml types the key as a list, else None
    (unknown keys keep the current scalar rendering)."""
    core = HIVE_ROOT / "lib" / "core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from llm_providers import load_defaults
    schema = load_defaults(HIVE_ROOT)
    cur = schema
    for p in key.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return "list" if isinstance(cur, list) else None


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
        if _default_type(args.key) == "list":
            # F1: list-typed keys take a JSON array (or comma-separated
            # value) and are written as a real TOML array, so keys like
            # tdd.applies_to_agents stop being edit-only.
            try:
                value = _toml_list(args.value)
            except ValueError as e:
                print(f"error: {e} — expected a JSON array "
                      f"(e.g. [\"ic-engineer\"]) or a comma-separated value",
                      file=sys.stderr)
                return 1
        else:
            value = _toml_value(args.value)
        new_text = _upsert(base, section, k, value)
        try:
            tomllib.loads(new_text)
        except Exception as e:
            print(f"error: refusing to write invalid TOML ({e}); "
                  f"file left unchanged", file=sys.stderr)
            return 1
        user_cfg_path.write_text(new_text)
        shown = value if value != args.value else args.value
        print(f"Wrote {section}.{k} = {shown} to {user_cfg_path}")
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
