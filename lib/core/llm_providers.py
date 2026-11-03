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

# Registry of known providers (B.2). "unsloth" is added in B.3 (it carries
# no default base_url: the operator always supplies one). api_style names the
# wire dialect: "openai" (POST {base}/chat/completions), "anthropic"
# (POST {base}/v1/messages), "gemini" (POST {base}/models/{model}:generateContent).
PROVIDERS = {
    "openai": {"api_style": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "key_env": "OPENAI_API_KEY", "needs_key": True},
    "anthropic": {"api_style": "anthropic", "base_url": "https://api.anthropic.com", "model": "", "key_env": "ANTHROPIC_API_KEY", "needs_key": True},
    "groq": {"api_style": "openai", "base_url": "https://api.groq.com/openai/v1", "model": "", "key_env": "GROQ_API_KEY", "needs_key": True},
    "openrouter": {"api_style": "openai", "base_url": "https://openrouter.ai/api/v1", "model": "", "key_env": "OPENROUTER_API_KEY", "needs_key": True,
                   "extra_headers": {"HTTP-Referer": "https://github.com/fredjt/hive", "X-Title": "Hive Agent Harness"}},
    "ollama": {"api_style": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "", "key_env": "", "needs_key": False},
    "google": {"api_style": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta", "model": "", "key_env": "GEMINI_API_KEY", "needs_key": True},
    # B.3: first-class named provider. The endpoint is operator-supplied
    # (no default base_url) and speaks EITHER the OpenAI-style or the
    # Anthropic-style API — [llm].api_style (or HIVE_API_STYLE) says which.
    "unsloth": {"api_style": "", "base_url": "", "model": "", "key_env": "UNSLOTH_API_KEY", "needs_key": True},
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


def style_for_provider(provider: str) -> str:
    """Map a provider name to its wire dialect ("openai" for unknown)."""
    return (PROVIDERS.get(provider) or {}).get("api_style", "openai")


def endpoint_url(base_url: str, api_style: str, model: str = "") -> str:
    """Full request URL for a wire dialect."""
    b = (base_url or "").rstrip("/")
    if not b:
        return ""
    if api_style == "anthropic":
        return b + "/messages" if b.endswith("/v1") else b + "/v1/messages"
    if api_style == "gemini":
        if not model:
            return ""
        return f"{b}/models/{model}:generateContent"
    return b + "/chat/completions"


def build_headers(api_style: str, api_key: str | None = None,
                  provider: str = "") -> dict:
    """Request headers for a wire dialect (auth header only if a key exists)."""
    headers = {"Content-Type": "application/json"}
    if api_style == "anthropic":
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif api_style == "gemini":
        if api_key:
            headers["x-goog-api-key"] = api_key
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for k, v in (PROVIDERS.get(provider) or {}).get("extra_headers", {}).items():
        headers[k] = v
    return headers


def build_payload(api_style: str, messages: list[dict], model: str,
                  temperature: float = 0.7, max_tokens: int = 8192) -> dict:
    """Request body in the wire dialect's native shape."""
    if api_style == "anthropic":
        system_text = ""
        conv = []
        for m in messages:
            if m["role"] == "system":
                system_text += ("\n" if system_text else "") + m["content"]
            else:
                conv.append({"role": "user" if m["role"] == "user" else "assistant",
                             "content": m["content"]})
        return {"model": model, "system": system_text,
                "messages": conv, "temperature": temperature,
                "max_tokens": max_tokens}
    if api_style == "gemini":
        system_text = ""
        contents = []
        for m in messages:
            if m["role"] == "system":
                system_text += ("\n" if system_text else "") + m["content"]
            else:
                contents.append({"role": "user" if m["role"] == "user" else "model",
                                 "parts": [{"text": m["content"]}]})
        payload = {"contents": contents,
                   "generationConfig": {"temperature": temperature,
                                        "maxOutputTokens": max_tokens}}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        return payload
    # openai style
    return {"model": model, "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens}


def parse_response_body(api_style: str, data: dict) -> dict:
    """Parse a provider response body into {ok, content, model, usage, error}."""
    if api_style == "anthropic":
        content = "".join(
            block.get("text", "") for block in data.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text")
        usage = data.get("usage", {})
        return {"ok": True, "content": content, "model": data.get("model", "anthropic"),
                "usage": {"prompt_tokens": usage.get("input_tokens", 0),
                          "completion_tokens": usage.get("output_tokens", 0),
                          "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)}}
    if api_style == "gemini":
        candidates = data.get("candidates", [])
        text = ""
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        meta = data.get("usageMetadata", {})
        return {"ok": bool(candidates), "content": text,
                "model": data.get("modelVersion", "gemini"),
                "usage": {"prompt_tokens": meta.get("promptTokenCount", 0),
                          "completion_tokens": meta.get("candidatesTokenCount", 0),
                          "total_tokens": meta.get("totalTokenCount", 0)}}
    # openai style
    choices = data.get("choices", [])
    if not choices:
        return {"ok": False, "error": "No choices in response"}
    message = choices[0].get("message", {})
    usage = data.get("usage", {})
    total_cost_usd = 0.0
    if usage:
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        # Rough cost estimation (varies by model)
        if "gpt-4" in data.get("model", "").lower():
            total_cost_usd = (prompt_tokens * 0.00003 + completion_tokens * 0.00006) / 1000
        elif "gpt-3.5" in data.get("model", "").lower():
            total_cost_usd = (prompt_tokens * 0.0000015 + completion_tokens * 0.000002) / 1000
        elif any(m in data.get("model", "").lower() for m in ["anthropic"]):
            total_cost_usd = (prompt_tokens * 0.000003 + completion_tokens * 0.000015) / 1000
    return {"ok": True, "content": message.get("content", "") or "",
            "model": data.get("model", "unknown"),
            "usage": {"prompt_tokens": usage.get("prompt_tokens", 0),
                      "completion_tokens": usage.get("completion_tokens", 0),
                      "total_tokens": usage.get("total_tokens", 0),
                      "total_cost_usd": total_cost_usd}}


@dataclass
class Resolved:
    """The effective LLM configuration after env > config > defaults."""
    provider: str = ""
    api_style: str = "openai"
    model: str = ""
    verification_model: str = ""  # C.3: distinct endpoint for stage 5 (verify)
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
        if not self.base_url and self.provider in PROVIDERS \
                and not PROVIDERS[self.provider]["base_url"]:
            return False  # no default endpoint — an explicit base_url is mandatory
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
    # C.3: verification fast path — env > config; empty means "use the
    # main model" (agent-spawn.sh injects --model only when set).
    verification_model = pick("HIVE_VERIFICATION_MODEL", "verification_model")

    provider = (env.get("HIVE_PROVIDER") or "").strip() or (config.get("provider") or "").strip()
    if provider:
        if provider not in PROVIDERS:
            return Resolved(
                provider=provider, api_style="openai", model=model,
                verification_model=verification_model, base_url=base_url,
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
        if provider == "unsloth":
            # B.3: the wire style is operator-chosen (the endpoint speaks
            # one dialect or the other), never inferred.
            style = ((env.get("HIVE_API_STYLE") or "").strip()
                     or (config.get("api_style") or "").strip())
            if style not in ("openai", "anthropic"):
                api_style = ""
                warnings.append(
                    "unsloth requires api_style = \"openai\" or \"anthropic\" "
                    "(set [llm].api_style in config.toml or HIVE_API_STYLE)")
            else:
                api_style = style
            if not base_url:
                warnings.append(
                    "unsloth requires base_url (the endpoint URL in "
                    "[llm].base_url or HIVE_API_BASE)")
    else:
        if not base_url:
            return Resolved(model=model, verification_model=verification_model,
                            warnings=["No LLM provider configured"])
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
        provider=provider, api_style=api_style, model=model,
        verification_model=verification_model, base_url=base_url,
        api_key=api_key, needs_key=needs_key, key_from_env=key_from_env,
        key_in_config=key_in_config, warnings=warnings,
    )
