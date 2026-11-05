#!/usr/bin/env python3
"""Classify a finished scout run so run.sh knows whether to retry it.

    python3 outcome.py <run.json> [<run.stderr>]

Prints one line, tab-separated:

    ok                                  the run finished normally
    done    <reason>                    it failed, but don't retry: it already
                                        spent real money (budget cap, turn cap,
                                        or an unknown error that cost > $0.05)
    retry   <epoch>  <reason>           it failed before doing any work
                                        (network, usage limit, overload, login);
                                        try again at or after <epoch>

Retrying is only for failures that cost about nothing, so a retry can never
double a day's spend.
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

FREE = 0.05  # a failed run that cost more than this did real work: don't redo it

RESET = re.compile(r"resets\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b(?:\s*\(([^)]+)\))?", re.I)


def reset_epoch(text: str, now: float) -> float | None:
    """'resets 3:40am (America/Phoenix)' -> the next such moment, as an epoch."""
    m = RESET.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    minute = int(m.group(2) or 0)
    tz = None
    if m.group(4):
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(m.group(4).strip())
        except Exception:
            tz = None
    base = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now)
    at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at.timestamp() <= now:
        at += timedelta(days=1)
    return at.timestamp() + 5 * 60  # a few minutes' slack after the reset


WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def classify(result: dict | None, stderr: str = "", now: float | None = None) -> tuple:
    now = time.time() if now is None else now
    if not result:
        # claude crashed or never started: nothing was spent
        why = (stderr.strip().splitlines() or ["no result written"])[-1][:160]
        return ("retry", int(now), f"no result ({why})")
    if not result.get("is_error") and result.get("subtype") == "success":
        # A run whose saves were refused finished, but its notes or proposals may only be in its log: not "ok".
        refused = [d for d in result.get("permission_denials") or []
                   if isinstance(d, dict) and d.get("tool_name") in WRITE_TOOLS]
        if refused:
            where = str((refused[0].get("tool_input") or {}).get("file_path", ""))[-80:]
            return ("done", f"{len(refused)} file save(s) refused (e.g. {where or '?'}); what it wrote is only in its "
                            "JSON log in techwatch/logs/. Check the permission rules (python3 techwatch/scripts/"
                            "techwatch.py doctor)")
        return ("ok",)
    msg = str(result.get("result") or result.get("subtype") or "")
    status = result.get("api_error_status")
    kind = str(result.get("api_error") or "")
    cost = float(result.get("total_cost_usd") or 0)
    short = re.sub(r"\s+", " ", msg)[:160]

    if result.get("subtype", "").startswith("error_max_"):
        return ("done", f"stopped at a cap ({result['subtype']}); partial output kept")
    if status == 429 or "usage_limit" in kind or re.search(r"(session|usage|weekly) limit", msg, re.I):
        at = reset_epoch(msg, now) or now + 3600
        return ("retry", int(at), f"usage limit: {short}")
    if cost > FREE:
        return ("done", f"error after spending ${cost:.2f}: {short}")
    if re.search(r"EAI_AGAIN|ENOTFOUND|ECONNRE|ETIMEDOUT|reach the API|network|DNS", msg, re.I):
        return ("retry", int(now), f"network: {short}")
    if re.search(r"not logged in|/login|authenticat|401", msg, re.I) or status == 401:
        return ("retry", int(now), f"LOGIN NEEDED (run `claude` and /login): {short}")
    # overloaded, 5xx, anything else that failed before spending: try again next hour
    return ("retry", int(now), f"error: {short}")


def main(argv: list[str]) -> int:
    try:
        result = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    except Exception:
        result = None
    stderr = ""
    if len(argv) > 1:
        try:
            stderr = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    print("\t".join(str(x) for x in classify(result, stderr)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
