#!/usr/bin/env bash
set -euo pipefail

# llm-tiny.sh - Wrapper around the small/fast CPU model for rule-based decisions.
#
# This is intentionally NOT a Claude Code agent invocation - it makes a direct
# HTTP call to a separate llama-server instance running a small model. Used by
# scripts to make simple classifications/decisions without burning a main-model
# turn (which costs 10-30 minutes of queue time).
#
# Configuration via env vars:
#   LLM_TINY_URL      - OpenAI-compatible chat completions endpoint
#                       default: http://localhost:8081/v1/chat/completions
#   LLM_TINY_MODEL    - model name to send in the request
#                       default: qwen2.5-3b-instruct
#   LLM_TINY_TIMEOUT  - curl timeout in seconds (default: 30)
#   LLM_TINY_RETRIES  - number of retries on failure (default: 2)
#   LLM_TINY_MAX_TOKENS - cap on output length (default: 64 - keep dumb model dumb)
#   LLM_TINY_TEMP     - temperature (default: 0.0 for deterministic classification)
#
# Usage:
#   llm-tiny.sh "<prompt>"                              - send prompt, get raw response
#   llm-tiny.sh --choice "<options>" "<prompt>"         - constrain output to one of options
#   llm-tiny.sh --yesno "<prompt>"                      - return "yes" or "no"
#   llm-tiny.sh --json "<prompt>"                       - request JSON output
#
# Exit codes:
#   0 = success (response on stdout)
#   1 = invalid usage
#   2 = HTTP/network error after retries
#   3 = response did not match constraints (e.g. --choice options)

LLM_TINY_URL="${LLM_TINY_URL:-http://localhost:8081/v1/chat/completions}"
LLM_TINY_MODEL="${LLM_TINY_MODEL:-qwen}"
LLM_TINY_TIMEOUT="${LLM_TINY_TIMEOUT:-30}"
LLM_TINY_RETRIES="${LLM_TINY_RETRIES:-2}"
LLM_TINY_MAX_TOKENS="${LLM_TINY_MAX_TOKENS:-64}"
LLM_TINY_TEMP="${LLM_TINY_TEMP:-0.0}"
# Qwen3+ are reasoning models by default; disable thinking for fast classification.
# Set to "true" only if you want chain-of-thought (much slower, much longer output).
LLM_TINY_THINKING="${LLM_TINY_THINKING:-false}"

MODE="raw"
CHOICES=""

while [ $# -gt 0 ]; do
  case "$1" in
    --choice)
      MODE="choice"
      CHOICES="$2"
      shift 2
      ;;
    --yesno)
      MODE="yesno"
      shift
      ;;
    --json)
      MODE="json"
      shift
      ;;
    --help|-h)
      sed -n '3,/^$/p' "$0" >&2
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

if [ $# -lt 1 ]; then
  echo "Usage: llm-tiny.sh [--choice 'a|b|c' | --yesno | --json] '<prompt>'" >&2
  exit 1
fi

USER_PROMPT="$1"

# Build system prompt based on mode
SYSTEM_PROMPT=""
case "$MODE" in
  choice)
    SYSTEM_PROMPT="You are a classifier. Output exactly one of these options and nothing else: $CHOICES. No explanation, no punctuation, just the chosen option."
    ;;
  yesno)
    SYSTEM_PROMPT="Answer with exactly 'yes' or 'no'. No explanation, no punctuation, just the word."
    ;;
  json)
    SYSTEM_PROMPT="Output valid JSON only. No prose, no markdown code fences, just the JSON object."
    ;;
  raw)
    SYSTEM_PROMPT="Be concise. One sentence maximum unless asked otherwise."
    ;;
esac

# Build request payload via jq for safe escaping
PAYLOAD=$(jq -n \
  --arg model "$LLM_TINY_MODEL" \
  --arg sys "$SYSTEM_PROMPT" \
  --arg user "$USER_PROMPT" \
  --argjson max "$LLM_TINY_MAX_TOKENS" \
  --argjson temp "$LLM_TINY_TEMP" \
  --argjson thinking "$LLM_TINY_THINKING" \
  '{
    model: $model,
    messages: [
      {role: "system", content: $sys},
      {role: "user", content: $user}
    ],
    max_tokens: $max,
    temperature: $temp,
    stream: false,
    chat_template_kwargs: {enable_thinking: $thinking}
  }')

# Try the request with retries
ATTEMPT=0
RESPONSE=""
while [ "$ATTEMPT" -le "$LLM_TINY_RETRIES" ]; do
  if RESPONSE=$(curl -s --max-time "$LLM_TINY_TIMEOUT" \
      -H 'Content-Type: application/json' \
      -d "$PAYLOAD" \
      "$LLM_TINY_URL" 2>/dev/null); then
    if [ -n "$RESPONSE" ] && echo "$RESPONSE" | jq -e '.choices[0].message.content' >/dev/null 2>&1; then
      break
    fi
  fi
  ATTEMPT=$((ATTEMPT + 1))
  [ "$ATTEMPT" -le "$LLM_TINY_RETRIES" ] && sleep 1
done

if [ "$ATTEMPT" -gt "$LLM_TINY_RETRIES" ]; then
  echo "[llm-tiny] HTTP/network failure after $LLM_TINY_RETRIES retries to $LLM_TINY_URL" >&2
  exit 2
fi

# Extract content. Reasoning models may put output in reasoning_content if
# thinking is enabled - prefer .content but fall back to reasoning_content.
CONTENT=$(echo "$RESPONSE" | jq -r '.choices[0].message.content // ""' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
if [ -z "$CONTENT" ]; then
  CONTENT=$(echo "$RESPONSE" | jq -r '.choices[0].message.reasoning_content // ""' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
fi

# Telemetry: append one line per call so usage is visible. Best-effort.
if [ -n "${COMPANY_DIR:-}" ] && [ -d "${COMPANY_DIR:-/dev/null}/logs" ] 2>/dev/null; then
  TS_LOG=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  PROMPT_PREVIEW=$(printf '%s' "$USER_PROMPT" | head -c 80 | tr '\n\r' '  ')
  RESP_PREVIEW=$(printf '%s' "$CONTENT" | head -c 80 | tr '\n\r' '  ')
  jq -nc \
    --arg ts "$TS_LOG" \
    --arg mode "$MODE" \
    --arg caller "${LLM_TINY_CALLER:-unknown}" \
    --arg prompt "$PROMPT_PREVIEW" \
    --arg response "$RESP_PREVIEW" \
    '{ts:$ts, mode:$mode, caller:$caller, prompt:$prompt, response:$response}' \
    >> "$COMPANY_DIR/logs/llm-tiny.log" 2>/dev/null || true
fi

# Post-process based on mode
case "$MODE" in
  choice)
    # Lowercase the response and check it matches one of the choices
    LOWER=$(echo "$CONTENT" | tr '[:upper:]' '[:lower:]' | tr -d '.,;:!?"'"'"'')
    IFS='|' read -ra OPTS <<<"$CHOICES"
    for opt in "${OPTS[@]}"; do
      opt_lower=$(echo "$opt" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
      if [ "$LOWER" = "$opt_lower" ]; then
        echo "$opt_lower"
        exit 0
      fi
    done
    # Fuzzy match: response contains one of the choices
    for opt in "${OPTS[@]}"; do
      opt_lower=$(echo "$opt" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
      if echo "$LOWER" | grep -qw "$opt_lower"; then
        echo "$opt_lower"
        exit 0
      fi
    done
    echo "[llm-tiny] Response '$CONTENT' did not match any of: $CHOICES" >&2
    exit 3
    ;;
  yesno)
    LOWER=$(echo "$CONTENT" | tr '[:upper:]' '[:lower:]' | tr -d '.,;:!?"'"'"'')
    case "$LOWER" in
      yes|y|true|1)  echo "yes"; exit 0 ;;
      no|n|false|0)  echo "no";  exit 0 ;;
    esac
    # Fuzzy: contains yes/no
    if echo "$LOWER" | grep -qw yes; then echo "yes"; exit 0; fi
    if echo "$LOWER" | grep -qw no;  then echo "no";  exit 0; fi
    echo "[llm-tiny] Response '$CONTENT' was not yes/no" >&2
    exit 3
    ;;
  json)
    # Strip markdown code fences if model added them despite instructions
    STRIPPED=$(echo "$CONTENT" | sed 's/^```json//; s/^```//; s/```$//')
    if echo "$STRIPPED" | jq -e . >/dev/null 2>&1; then
      echo "$STRIPPED"
      exit 0
    fi
    echo "[llm-tiny] Response was not valid JSON: $CONTENT" >&2
    exit 3
    ;;
  raw)
    echo "$CONTENT"
    exit 0
    ;;
esac
