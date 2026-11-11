"""hive config ...

Two faces (0.18 T1): the default `show` is the human table (SECTION /
KEY / VALUE / SOURCE — which layer WON per key, the documented
precedence made visible); `show --json` is the merged document with
the secrets masked. `get` on a secret key prints the masked
placeholder, not the value — the config CLI was the ONE surface in
the system that printed the key in plaintext (doctor, `llm test`, and
the dashboard all deliberately name only the source); 0.18 closes it.
Exit codes unchanged: show 0 (unknown keys included, as before); get 0
when set, 1 with `(unset: <key>)` on stderr when unset.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib  # stdlib (3.11+; the 0.15 floor — the 3.10 tomli backport is retired)
from pathlib import Path


from hive_paths import install_root
from hive_cli import present

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
    """Parse the user config file ({} when missing/unparseable) — the
    EFFECTIVE path (HIVE_CONFIG wins, per A2's single spelling), so a
    redirected config is seen by every layer that reads the user
    layer."""
    path = path or _user_config_path()
    if path.is_file():
        try:
            return tomllib.loads(path.read_text()) or {}
        except Exception:
            return {}
    return {}


# The canonical HIVE_* LLM env vars — the same mapping doctor's D1
# list carries (doctor.py): per key, env wins over BOTH file layers.
_ENV_LLM_KEYS = {
    "HIVE_PROVIDER": "llm.provider",
    "HIVE_MODEL": "llm.model",
    "HIVE_API_BASE": "llm.base_url",
    "HIVE_API_KEY": "llm.api_key",
    "HIVE_API_STYLE": "llm.api_style",
    "HIVE_VERIFICATION_MODEL": "llm.verification_model",
}


def _env_var_for(section: str, key: str) -> str | None:
    """The HIVE_* env var that overrides `section.key`, if any."""
    for var, dotted in _ENV_LLM_KEYS.items():
        if dotted == f"{section}.{key}":
            return var
    return None


def _show_rows() -> list[dict]:
    """The rows for the human config table (0.18 T1): every key with
    the layer that WON named as SOURCE — `env <HIVE_*>` (the env value
    is shown; env wins over both file layers), `user config`, or
    `default`. Secret values arrive already masked."""
    merged = _load()
    user = _user_cfg()
    rows = []
    for section, body in merged.items():
        if section == "_unknown_keys" or not isinstance(body, dict):
            continue
        for key, value in body.items():
            env_var = _env_var_for(section, key)
            if env_var:
                env_val = (os.environ.get(env_var) or "").strip()
                if env_val:
                    # The env value wins — and a secret's env value is
                    # masked exactly like the file layers' (the key's
                    # bytes never reach stdout from any layer).
                    rows.append({"section": section, "key": key,
                                 "value": present.mask_secrets(
                                     {key: env_val})[key],
                                 "source": f"env {env_var}"})
                    continue
            if isinstance(user.get(section), dict) and key in user[section]:
                source = "user config"
            else:
                source = "default"
            rows.append({"section": section, "key": key,
                         "value": present.mask_secrets({key: value})[key],
                         "source": source})
    return rows


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
        if getattr(args, "json", False):
            # The machine face: the 0.17.0 merged document with the
            # secrets masked — nothing else in the document moves.
            merged = _load()
            unknowns = _unknown_keys()
            if unknowns:
                merged["_unknown_keys"] = unknowns
                for u in unknowns:
                    print(f"warning: unknown key {u} — edit {user_cfg_path}",
                          file=sys.stderr)
            print(json.dumps(present.mask_secrets(merged), indent=2))
            return 0
        # The human face: SECTION / KEY / VALUE / SOURCE (the layer
        # that WON per key); secrets masked; the unknown-key warnings
        # stay on stderr.
        unknowns = _unknown_keys()
        for u in unknowns:
            print(f"warning: unknown key {u} — edit {user_cfg_path}",
                  file=sys.stderr)
        print(present.render_config_show(_show_rows()))
        return 0
    if args.action == "get":
        v = _walk(_load(), args.key)
        if v is None:
            print(f"(unset: {args.key})", file=sys.stderr)
            return 1
        leaf = args.key.rsplit(".", 1)[-1].lower()
        # A secret that IS set: the answer to 'is it set?' is yes —
        # the value is not the answer (0.18 T1: the key never reaches
        # stdout). An empty value is not a secret — it prints as set
        # to empty, exactly as 0.17 did.
        if leaf in present.SECRET_KEYS and v not in (None, "", []):
            print(present.MASKED)
            return 0
        print(v if not isinstance(v, (dict, list)) else json.dumps(v))
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
