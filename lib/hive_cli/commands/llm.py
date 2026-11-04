"""hive llm — LLM usage (stats/tail) + endpoint test.

Usage lives in .company/logs/llm-usage.log (B2: the retired llm-tiny.log
was renamed; readers accept both names for one release).
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _company_dir() -> Path:
    cd = os.environ.get("COMPANY_DIR")
    if cd:
        return Path(cd)
    return Path.cwd() / ".company"


def _key_source_label(resolved) -> str:
    """B1: how the key was obtained — a LABEL only, never the value."""
    if resolved.key_from_env:
        return f"from env {resolved.key_from_env}"
    if resolved.key_in_config:
        return "from config [llm].api_key"
    return "none (keyless endpoint)" if not resolved.needs_key else "none"


_TEST_RECORD: dict = {}


def _save_llm_test(rc: int) -> int:
    """C2: persist the latest `hive llm test` result to
    .company/state/llm-test.json so the dashboard's Config panel can show
    the last run's outcome + timestamp. Best-effort: never changes rc."""
    rec = dict(_TEST_RECORD)
    rec["ok"] = (rc == 0)
    try:
        from datetime import datetime, timezone
        rec["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        company = Path(os.environ.get("COMPANY_DIR") or (Path.cwd() / ".company"))
        if company.is_dir():
            state = company / "state"
            state.mkdir(parents=True, exist_ok=True)
            import json as _json
            (state / "llm-test.json").write_text(_json.dumps(rec, indent=2))
    except Exception:
        pass
    return rc


def _llm_test() -> int:
    """B1: one small round trip through the exact resolver path the harness
    uses (llm_providers.resolve + its wire builders). Prints provider,
    model, base_url, the key's source label (never the key), latency, and
    the model's first reply line; exits 0/1. A dead endpoint is a 2-second
    discovery instead of a 20-agent-turn one."""
    import json as _json
    import time
    import urllib.error
    import urllib.request
    core = Path(__file__).resolve().parents[2] / "core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    import llm_providers

    env = dict(os.environ)
    resolved = llm_providers.resolve(env=env)
    if not resolved.is_configured:
        print("llm test: LLM unconfigured — "
              + llm_providers.unconfigured_hint(env=env))
        _TEST_RECORD.clear()
        _TEST_RECORD.update(provider="", model="", base_url="",
                            key_source="", latency_ms=None, reply="",
                            error="LLM unconfigured")
        return 1
    for w in resolved.warnings:
        print(f"  warning: {w}")

    url = llm_providers.endpoint_url(resolved.base_url, resolved.api_style,
                                     resolved.model)
    if not url:
        print(f"llm test: FAIL — cannot derive request URL "
              f"(api_style={resolved.api_style or '?'}, "
              f"base_url={resolved.base_url or '?'}, "
              f"model={resolved.model or '?'})")
        _TEST_RECORD.clear()
        _TEST_RECORD.update(provider=resolved.provider or "", model=resolved.model or "",
                            base_url=resolved.base_url or "",
                            key_source=_key_source_label(resolved),
                            latency_ms=None, reply="",
                            error="cannot derive request URL")
        return 1
    headers = llm_providers.build_headers(resolved.api_style, resolved.api_key,
                                          provider=resolved.provider)
    payload = llm_providers.build_payload(
        resolved.api_style, [{"role": "user", "content": "Reply with the single word OK."}],
        resolved.model, temperature=0.0, max_tokens=16)

    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            url, data=_json.dumps(payload).encode(), headers=headers,
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8", "replace")
        latency_ms = (time.monotonic() - t0) * 1000
        print(f"llm test: FAIL — HTTP {status} from {resolved.base_url}")
        print(f"  response: {body[:300]}")
        _TEST_RECORD.clear()
        _TEST_RECORD.update(provider=resolved.provider or "", model=resolved.model or "",
                            base_url=resolved.base_url or "",
                            key_source=_key_source_label(resolved),
                            latency_ms=round(latency_ms), reply="",
                            error=f"HTTP {status}: {body[:200]}")
        return 1
    except Exception as e:
        latency_ms = (time.monotonic() - t0) * 1000
        print(f"llm test: FAIL — {resolved.base_url}: {type(e).__name__}: {e}")
        _TEST_RECORD.clear()
        _TEST_RECORD.update(provider=resolved.provider or "", model=resolved.model or "",
                            base_url=resolved.base_url or "",
                            key_source=_key_source_label(resolved),
                            latency_ms=round(latency_ms), reply="",
                            error=f"{resolved.base_url}: {type(e).__name__}: {e}")
        return 1
    latency_ms = (time.monotonic() - t0) * 1000

    print("LLM test — one-shot round trip")
    print(f"  provider: {resolved.provider or '(detected)'}")
    print(f"  model:    {resolved.model or '(default)'}")
    print(f"  base_url: {resolved.base_url}")
    print(f"  key:      {_key_source_label(resolved)}")
    print(f"  latency:  {latency_ms:.0f} ms (HTTP {status})")

    try:
        data = _json.loads(body)
    except _json.JSONDecodeError:
        print(f"llm test: FAIL — endpoint at {resolved.base_url} returned "
              f"non-JSON: {body[:200]}")
        _TEST_RECORD.clear()
        _TEST_RECORD.update(provider=resolved.provider or "", model=resolved.model or "",
                            base_url=resolved.base_url or "",
                            key_source=_key_source_label(resolved),
                            latency_ms=round(latency_ms), reply="",
                            error=f"non-JSON response: {body[:200]}")
        return 1
    parsed = llm_providers.parse_response_body(resolved.api_style, data)
    if not parsed.get("ok"):
        print(f"llm test: FAIL — {parsed.get('error', 'no content in response')}")
        _TEST_RECORD.clear()
        _TEST_RECORD.update(provider=resolved.provider or "", model=resolved.model or "",
                            base_url=resolved.base_url or "",
                            key_source=_key_source_label(resolved),
                            latency_ms=round(latency_ms), reply="",
                            error=parsed.get("error", "no content in response"))
        return 1
    first_line = (parsed.get("content") or "").strip().splitlines() or [""]
    print(f"  reply:    {first_line[0][:200]}")
    print("RESULT: PASS")
    _TEST_RECORD.clear()
    _TEST_RECORD.update(provider=resolved.provider or "", model=resolved.model or "",
                        base_url=resolved.base_url or "",
                        key_source=_key_source_label(resolved),
                        latency_ms=round(latency_ms),
                        reply=first_line[0][:200], error="")
    return 0


def _usage_log_path(cd: Path) -> Path | None:
    """B2: the usage log is llm-usage.log; a legacy llm-tiny.log (pre-rename
    workspaces) is still read for one release — new name wins if both exist."""
    new = cd / "logs" / "llm-usage.log"
    if new.is_file():
        return new
    legacy = cd / "logs" / "llm-tiny.log"
    if legacy.is_file():
        return legacy
    return None


def handle(args) -> int:
    if args.action == "test":
        return _save_llm_test(_llm_test())
    cd = _company_dir()
    log_path = _usage_log_path(cd)
    if log_path is None:
        print(f"No llm-usage.log at {cd / 'logs' / 'llm-usage.log'} "
              f"(legacy llm-tiny.log looked for too).", file=sys.stderr)
        print("Either no calls have happened yet, or you're not in a hive project root.", file=sys.stderr)
        return 1

    entries = []
    with log_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if args.action == "tail":
        for e in entries[-args.n:]:
            ts = e.get("ts", "?")
            caller = e.get("caller", "unknown")
            mode = e.get("mode", "?")
            resp = (e.get("response", "") or "")[:80]
            print(f"{ts:<22} {caller:<40} {mode:<8} → {resp}")
        return 0

    # stats
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_1h = now - timedelta(hours=1)

    by_caller_24h: Counter = Counter()
    by_caller_1h: Counter = Counter()
    by_mode_24h: Counter = Counter()
    last_call_per_caller: dict = {}

    for e in entries:
        ts_str = e.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        caller = e.get("caller", "unknown")
        mode = e.get("mode", "?")
        if ts > cutoff_24h:
            by_caller_24h[caller] += 1
            by_mode_24h[mode] += 1
        if ts > cutoff_1h:
            by_caller_1h[caller] += 1
        if caller not in last_call_per_caller or ts > last_call_per_caller[caller]:
            last_call_per_caller[caller] = ts

    print(f"llm usage — log at {log_path}")
    print(f"  total calls logged: {len(entries)}")
    print(f"  in last 24h:        {sum(by_caller_24h.values())}")
    print(f"  in last 1h:         {sum(by_caller_1h.values())}")
    print()

    if by_caller_24h:
        print("By caller (last 24h):")
        for caller, n in by_caller_24h.most_common():
            last = last_call_per_caller.get(caller)
            ago = ""
            if last:
                d = (now - last).total_seconds()
                ago = f"{int(d)}s ago" if d < 60 else f"{int(d/60)}m ago" if d < 3600 else f"{int(d/3600)}h ago"
            print(f"  {n:>4}  {caller:<45} (last {ago})")
        print()

    if by_mode_24h:
        print("By mode (last 24h):")
        for mode, n in by_mode_24h.most_common():
            print(f"  {n:>4}  {mode}")
        print()

    print("Wires currently configured:")
    print("  classify-ticket → ticket-route-expedited.sh (when --type=auto or --dept=auto)")
    print("  narrate-status     → company-start.sh pulse loop (every 5 min, per dept)")
    print("  summarize-thread   → company-start.sh pulse loop (every 5 min, leadership digest)")
    return 0
