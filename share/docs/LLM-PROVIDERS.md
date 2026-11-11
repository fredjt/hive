# LLM Providers

The agent harness (`lib/core/agent-harness.py`) talks to LLM provider APIs
directly. Provider selection is **explicit**: name the provider, and the
harness speaks that provider's native wire format. Base-URL detection
exists only as a fallback for setups that predate the `[llm]` config
section.

## Config surface and precedence

Per value, the precedence is:

```
HIVE_* env vars  >  ~/.config/hive/config.toml [llm]  >  install defaults  >  provider defaults
```

The `[llm]` section (defaults in `etc/config.defaults.toml`):

```toml
[llm]
provider         = ""   # openai, anthropic, groq, openrouter, ollama, google, unsloth
model            = ""   # model name (provider default when empty, if any)
base_url         = ""   # endpoint base URL (provider default when empty, if any)
api_key          = ""   # API key (per-provider env fallback when empty)
api_style        = ""   # unsloth only: "openai" or "anthropic"
verification_model = "" # model for verification agents (stage 5: verify)
```

`hive doctor` reports the resolved provider/model and where the key came
from (env var name or config) — never the key itself, on every
surface: `hive config show`/`get` mask it, the dashboard strips it
from `/api/config`, and only the source label is ever printed.

## Registered providers

| Provider | Wire format | Default base URL | Key env fallback | Key required |
|---|---|---|---|---|
| `openai` | OpenAI `/chat/completions` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | yes |
| `anthropic` | Anthropic `/v1/messages` | `https://api.anthropic.com` | `ANTHROPIC_API_KEY` | yes |
| `groq` | OpenAI `/chat/completions` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | yes |
| `openrouter` | OpenAI `/chat/completions` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | yes |
| `ollama` | OpenAI `/chat/completions` | `http://127.0.0.1:11434/v1` | — | no |
| `google` | Gemini `:generateContent` | `https://generativelanguage.googleapis.com/v1beta` | `GEMINI_API_KEY` | yes |
| `unsloth` | OpenAI **or** Anthropic (you choose) | — (operator-supplied) | `UNSLOTH_API_KEY` | yes |

Key resolution per value: `HIVE_API_KEY` > `[llm].api_key` > the
provider's env var.

## unsloth

unsloth is a first-class named provider for its hosted/fine-tuned-model
endpoints, which speak either the OpenAI-style or the Anthropic-style
API. `provider = "unsloth"` plus `api_style` names the wire format;
`base_url` and the key are always operator-supplied.

OpenAI-style endpoint:

```toml
[llm]
provider  = "unsloth"
api_style = "openai"
base_url  = "https://your-unsloth-endpoint.example/v1"
model     = "your-model-id"
api_key   = "sk-..."
```

Anthropic-style endpoint:

```toml
[llm]
provider  = "unsloth"
api_style = "anthropic"
base_url  = "https://your-unsloth-endpoint.example"
model     = "your-model-id"
api_key   = "sk-..."
```

Equivalently via env — for ONE-SHOT runs, export them in your
interactive shell (the exports cover that shell and its children
only):

```bash
export HIVE_PROVIDER=unsloth
export HIVE_API_STYLE=anthropic        # or openai
export HIVE_API_BASE=https://your-unsloth-endpoint.example
export HIVE_MODEL=your-model-id
export UNSLOTH_API_KEY=sk-...          # HIVE_API_KEY also works
```

`~/.config/hive/env.sh` is for shell-level daemon env only (PATH
additions, GITHUB_TOKEN). Do not place LLM settings there: every
resolver reads [llm] from config.toml, and doctor's D1 check
warns when a retired LLM var sits in env.sh (the
unconfigured-provider diagnostic cross-references it).

If `api_style` is missing or invalid, the harness warns and the resolved
style is left to detection — set it explicitly.

## Verification model

Verification agents (stage 5: verify) do the most cognitively demanding
work — reviewing diffs, checking logic, assessing correctness. Point a
stronger model at that stage with `verification_model` (or
`HIVE_VERIFICATION_MODEL`); every other agent keeps the default model.

## Failure behavior

- No provider configured: the harness exits immediately with one
  actionable line naming what to set and where.
- Configured but unreachable: the same, with the URL and HTTP status.
- Provider failures report the provider, the base URL attempted, the
  HTTP status, and a truncated response body.
