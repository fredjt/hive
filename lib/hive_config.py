"""Hive configuration — the ONE TOML parser and precedence owner.

Precedence (highest first):

1. ``HIVE_CONFIG`` env — explicit user-config file (tests, operators).
2. ``~/.config/hive/config.toml`` — the user config.
3. ``etc/config.defaults.toml`` — the install defaults.

A top-level section present in the user config replaces the defaults
section wholesale (shallow merge — the same semantics ``hive config``
has always had). No bash script may parse these files with awk/sed:
bash reads config through ``lib/tools/config-get.sh``, which execs this
module.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import tomllib  # stdlib (3.11+; the 0.15 floor)
except ImportError:  # a parser-less interpreter — _toml_loads fails loudly
    tomllib = None


def _lib_root() -> Path:
    """The lib/ directory this module lives in."""
    return Path(__file__).resolve().parent


def _hive_root() -> Path:
    """The install root (parent of lib/)."""
    return _lib_root().parent


def user_config_path(env: dict | None = None) -> Path:
    """The effective user-config file (HIVE_CONFIG override honored)."""
    env = env if env is not None else os.environ
    override = env.get("HIVE_CONFIG")
    if override:
        return Path(override)
    home = (env.get("HOME") or os.path.expanduser("~")).strip()
    return Path(home) / ".config" / "hive" / "config.toml"


def defaults_path() -> Path:
    return _hive_root() / "etc" / "config.defaults.toml"


def _toml_loads(text: str) -> dict:
    """Parse with the first available parser: stdlib tomllib (3.11+),
    then tomli — the package packaging/requirements.txt installs for
    older interpreters (``tomli>=2.0; python_version < "3.11"``) — and
    only then the legacy ``toml`` package. A parser-less interpreter
    fails loudly: a config reader that silently returned an empty
    config would turn ``hive config get`` into a lie, and
    config-get.sh must not emit defaults when the real config is
    merely unreadable."""
    if tomllib is not None:
        return tomllib.loads(text)
    # T4 (0.15): the 3.10 surface (the tomli/toml fallback chain) is
    # retired — stdlib tomllib is the only parser under the 3.11 floor.
    raise ImportError(
        "no TOML parser available — this interpreter has no stdlib "
        "tomllib (requires Python >= 3.11; see packaging/pyproject.toml)"
    )


def load_config(hive_root: str | Path | None = None,
                env: dict | None = None) -> dict:
    """Merge defaults with the user config (shallow, per section)."""
    env = env if env is not None else dict(os.environ)
    root = Path(hive_root) if hive_root else _hive_root()
    config: dict = {}
    defaults = root / "etc" / "config.defaults.toml"
    if defaults.is_file():
        config = _toml_loads(defaults.read_text(encoding="utf-8")) or {}
    user_file = user_config_path(env)
    if user_file.is_file():
        try:
            user_cfg = _toml_loads(user_file.read_text(encoding="utf-8")) or {}
        except Exception:
            user_cfg = {}
        for section, value in user_cfg.items():
            config[section] = value
    return config


def get(section: str, key: str, default=None,
        hive_root: str | Path | None = None,
        env: dict | None = None):
    """One value from the merged config (None/`default` when absent)."""
    data = load_config(hive_root, env)
    sec = data.get(section)
    if not isinstance(sec, dict):
        return default
    return sec.get(key, default)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in ("get", "path"):
        print("usage: python -m hive_config get <section> <key>",
              file=sys.stderr)
        print("       python -m hive_config path", file=sys.stderr)
        return 2
    if args[0] == "path":
        print(user_config_path())
        return 0
    if len(args) < 3:
        print("usage: python -m hive_config get <section> <key>",
              file=sys.stderr)
        return 2
    value = get(args[1], args[2])
    if value is None:
        return 0
    if isinstance(value, bool):
        print("true" if value else "false")
    else:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
