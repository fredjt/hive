"""hive llm stats — show small-model usage from .company/logs/llm-tiny.log."""

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


def handle(args) -> int:
    cd = _company_dir()
    log_path = cd / "logs" / "llm-tiny.log"
    if not log_path.is_file():
        print(f"No llm-tiny.log at {log_path}.", file=sys.stderr)
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

    print(f"llm-tiny usage — log at {log_path}")
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
    print("  classify-ticket    → inbox-write.sh (when --type=auto or --dept=auto)")
    print("  prioritize-backlog → sprint-fill.sh (only on tied-priority backlog)")
    print("  narrate-status     → company-start.sh pulse loop (every 5 min, per dept)")
    print("                       meeting-runner.sh standup (per dept pre-summary)")
    print("  summarize-thread   → company-start.sh pulse loop (every 5 min, leadership digest)")
    print("                       meeting-runner.sh standup (per channel pre-summary)")
    return 0
