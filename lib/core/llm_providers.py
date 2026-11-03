"""Provider registry + LLM config resolution for the agent harness.

Plain stdlib Python: importable by the harness (its own directory is on
sys.path when it runs as a script) and by the CLI (hive doctor) via a
path insert. No third-party imports.

Resolution precedence (per value):

    HIVE_* env var  >  [llm] config section  >  provider default

Provider selection:
    explicit (HIVE_PROVIDER / [llm].provider)  -> that provider
    else base_url present                      -> detected from the URL
    else                                       -> unconfigured
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Registry of known providers. "unsloth" is added in B.3 (it carries no
# default base_url: the operator always supplies one).
PROVIDERS = {
    "openai": {"api_style": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "key_env": "OPENAI_API_KEY", "needs_key": True},
    "anthropic": {"api_style": "anthropic", "base_url": "https://api.anthropic.com", "model": "", "key_env": "ANTHROPIC_API_KEY", "needs_key": True},
    "groq": {"api_style": "openai", "base_url": "https://api.groq.com/openai/v1", "model": "", "key_env": "GROQ_API_KEY", "needs_key": True},
    "openrouter": {"api_style": "openai", "base_url": "https://openrouter.ai/api/v1", "model": "", "key_env": "OPENROUTER_API_KEY", "needs_key": True},
    "ollama": {"api_style": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "", "key_env": "", "needs_key": False},
}


def _toml_loads(text: str) -> dict:
    try:
        import tomllib
    except ImportError:  # Python 3.10/3.11
        import tomli as tomllib
    return tomllib.loads(text)


def load_config(hive_root: str | Path | None = None,
                env: dict | None = None) -> dict:
    """Merge etc/config.defaults.toml with ~/.config/hive/config.toml.

    env (defaults to os.environ) supplies HOME so callers — and tests —
    can redirect the user config location without touching the real one.
    """
    env = env if env is not None else dict(os.environ)
    root = (Path(hive_root) if hive_root
            else Path(__file__).resolve().parent.parent.parent)
    config = {}
    defaults = root / "etc" / "config.defaults.toml"
    if defaults.is_file():
        config = _toml_loads(defaults.read_text()) or {}
    home = (env.get("HOME") or os.path.expanduser("~")).strip()
    user_file = Path(home) / ".config" / "hive" / "config.toml"
    user_cfg = {}
    if user_file.is_file():
        try:
            user_cfg = _toml_loads(user_file.read_text()) or {}
        except Exception:
            user_cfg = {}
    # Shallow merge: a top-level section in the user config replaces the
    # defaults section wholesale (same semantics as hive_cli config).
    for section, value in user_cfg.items():
        config[section] = value
    return config


def detect_provider(base_url: str) -> str:
    """Best-effort provider detection from a base URL (fallback path
    only — the config/env path is explicit)."""
    u = (base_url or "").lower()
    if "openai.com" in u:
        return "openai"
    if "anthropic.com" in u:
        return "anthropic"
    if "groq" in u:
        return "groq"
    if "openrouter" in u:
        return "openrouter"
    if "googleapis.com" in u or "generativelanguage" in u:
        return "google"
    if "11434" in u or "ollama" in u:
        return "ollama"
    return "custom"


@dataclass
class Resolved:
    """The effective LLM configuration after env > config > defaults."""
    provider: str = ""
    api_style: str = "openai"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    needs_key: bool = False
    key_from_env: str = ""
    key_in_config: bool = False
    warnings: list = field(default_factory=list)

    @property
    def is_configured(self) -> bool:
        """True if a model is selected and (if needed) a key is present."""
        if not self.model and not self.base_url:
            return False
        if self.needs_key and not self.api_key:
            return False
        return True


def resolve(env: dict | None = None, hive_root: str | Path | None = None) -> Resolved:
    """Resolve the effective LLM configuration.

    Precedence per value: HIVE_* env > [llm] config > provider default.
    Provider: explicit (env HIVE_PROVIDER / config llm.provider) wins;
    otherwise the base_url (env or config) is detected with a warning;
    otherwise the result is unconfigured.
    """
    env = env if env is not None else dict(os.environ)
    config = load_config(hive_root, env=env).get("llm", {}) or {}

    def pick(env_key: str, cfg_key: str) -> str:
        v = (env.get(env_key) or "").strip()
        if v:
            return v
        return (config.get(cfg_key) or "").strip()

    base_url = pick("HIVE_API_BASE", "base_url")
    model = pick("HIVE_MODEL", "model")

    provider = (env.get("HIVE_PROVIDER") or "").strip() or (config.get("provider") or "").strip()
    if provider:
        if provider not in PROVIDERS:
            return Resolved(
                provider=provider, api_style="openai", model=model, base_url=base_url,
                needs_key=True,
                warnings=[f"Unknown provider '{provider}' — not in registry; "
                          "using generic OpenAI-compatible handling"],
            )
        meta = dict(PROVIDERS[provider])
        if not base_url:
            base_url = meta.get("base_url", "")
        if not model:
            model = meta.get("model", "")
        warnings = []
        api_style = meta.get("api_style", "openai")
        key_env = meta.get("key_env", "")
    else:
        if not base_url:
            return Resolved(model=model, warnings=["No LLM provider configured"])
        provider = detect_provider(base_url)
        if provider in PROVIDERS:
            meta = PROVIDERS[provider]
            api_style = meta.get("api_style", "openai")
            key_env = meta.get("key_env", "")
            if not model:
                model = meta.get("model", "")
            warnings = [f"Provider not explicit — detected '{provider}' from base URL "
                        "(set HIVE_PROVIDER or [llm].provider to silence this warning)"]
        else:
            meta = {}
            api_style = "openai"
            key_env = ""
            warnings = ["Custom endpoint (unrecognized base URL) — OpenAI-compatible "
                        "request/response assumed"]

    # API key: HIVE_API_KEY env > [llm].api_key config > per-provider env.
    api_key = (env.get("HIVE_API_KEY") or "").strip()
    key_from_env = ""
    key_in_config = False
    if api_key:
        key_from_env = "HIVE_API_KEY"
    else:
        cfg_key = (config.get("api_key") or "").strip()
        if cfg_key:
            api_key = cfg_key
            key_in_config = True
        elif key_env and (env.get(key_env) or "").strip():
            api_key = env[key_env].strip()
            key_from_env = key_env

    needs_key = bool(meta.get("needs_key", True))
    return Resolved(
        provider=provider, api_style=api_style, model=model, base_url=base_url,
        api_key=api_key, needs_key=needs_key, key_from_env=key_from_env,
        key_in_config=key_in_config, warnings=warnings,
    )
