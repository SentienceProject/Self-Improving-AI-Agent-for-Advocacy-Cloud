#!/usr/bin/env python3
"""Send a push notification to your phone with ntfy (https://ntfy.sh): free, no account needed.

    python3 scripts/notify.py "Title" "Message"     send one (e.g. from a hook, a cron job or your own script)
    python3 scripts/notify.py --test                send a test notification
    python3 scripts/notify.py --setup               turn phone notifications on (setup.py asks this for you)
    python3 scripts/notify.py --off                 turn them off (keeps your topic, in case you turn them on again)
    python3 scripts/notify.py --status              show whether they're on (never prints the topic)

Options when sending: --priority min|low|default|high|max, --tags "tag1,tag2" (ntfy emoji names, e.g. "warning").

Your settings are in my/notify.json: {"on": true, "server": "https://ntfy.sh", "topic": "...", ...}. The topic name
is the only secret: anyone who knows it can read or post to it. setup gives you a long random one; don't put it
anywhere public. Messages go through the ntfy server (ntfy.sh keeps them for about 12 hours so your phone can fetch
them), so keep anything confidential out of them, or run your own ntfy server and put its address in "server".

Plain code, no AI model involved: nothing is sent unless notifications are on and a topic is set. Eval runs never
send: the eval harness replaces my/ in its copies, so they have no topic.
"""
import json
import os
import re
import secrets
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
try:
    from style import S  # noqa: E402  (readable colours in a terminal; plain text otherwise)
except ImportError:      # an older copy without scripts/style.py
    class S:
        ok = warn = ask = b = staticmethod(str)
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CONFIG = HERE / "my" / "notify.json"
TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
PRIORITIES = ("min", "low", "default", "high", "max")
MAX_BODY = 3500   # bytes of UTF-8; ntfy's limit is 4096


def load():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(cfg):
    CONFIG.parent.mkdir(exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8")


def ready(cfg=None):
    cfg = load() if cfg is None else cfg
    return bool(cfg.get("on") and TOPIC_RE.fullmatch(str(cfg.get("topic", ""))))


def clean_header(s, n=120):
    """Plain ASCII on one line (HTTP headers can't carry anything else), cut to n characters."""
    s = re.sub(r"\s+", " ", re.sub(r"[^\x20-\x7E]", " ", str(s))).strip()
    return s if len(s) <= n else s[: n - 3].rstrip() + "..."


def send(title, body, priority="default", tags="", cfg=None):
    """True if the server accepted it. Never raises: a notification failing shouldn't break what called it."""
    try:
        return _send(title, body, priority, tags, cfg)
    except Exception as e:   # a malformed setting, say
        print(f"notify: not sent: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return False


def _send(title, body, priority, tags, cfg):
    if os.environ.get("AI_WELFARE_EVAL"):   # set by the eval harness for every agent run
        print("notify: not sent (this is an eval run)", file=sys.stderr)
        return False
    cfg = load() if cfg is None else cfg
    if not ready(cfg):
        print("notify: phone notifications are off or have no topic (python3 scripts/notify.py --setup)",
              file=sys.stderr)
        return False
    data = (str(body).strip() or "(no message)").encode("utf-8")
    if len(data) > MAX_BODY:
        data = data[: MAX_BODY - 3].decode("utf-8", "ignore").rstrip().encode("utf-8") + b"..."
    headers = {"Title": clean_header(title), "Priority": priority if priority in PRIORITIES else "default"}
    if tags := clean_header(tags, 60):
        headers["Tags"] = tags
    server = str(cfg.get("server") or "https://ntfy.sh").rstrip("/")
    req = urllib.request.Request(f"{server}/{cfg['topic']}", data=data, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception as e:  # no network, rate limit, server error
        print(f"notify: sending failed: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return False


def instructions(cfg):
    server = str(cfg.get("server") or "https://ntfy.sh").rstrip("/")
    return ("  To get them on your phone:\n"
            "    1. Install the free ntfy app (Android: Google Play or F-Droid; iPhone: App Store).\n"
            f"    2. In the app, tap + and subscribe to this topic: {cfg['topic']}\n"
            + ("" if server == "https://ntfy.sh" else f"       (on the server {server})\n")
            + "  The topic name works like a password: anyone who knows it can read your notifications, so keep it\n"
              "  private. It's saved in my/notify.json. On a computer, you can also open "
              f"{server}/{cfg['topic']} in a browser.")


def setup(days=None):
    """Turn notifications on, keeping an existing topic. Returns the config."""
    cfg = load()
    if not TOPIC_RE.fullmatch(str(cfg.get("topic", ""))):
        cfg["topic"] = "welfare-" + secrets.token_hex(12)
    cfg.setdefault("server", "https://ntfy.sh")
    cfg["on"] = True
    if days is not None:
        cfg["followups_every_days"] = days
    save(cfg)
    return cfg


def main(a):
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if a else 2
    if a[0] == "--status":
        cfg = load()
        print("Phone notifications: " + ("on" if ready(cfg) else "off")
              + (f"; follow-up briefs every {cfg['followups_every_days']} day(s)"
                 if ready(cfg) and cfg.get("followups_every_days") else ""))
        return 0
    if a[0] == "--setup":
        cfg = setup()
        print(S.ok("Phone notifications are on.") + "\n" + instructions(cfg))
        if not sys.stdin.isatty():
            print("Subscribe in the app, then send a test: python3 scripts/notify.py --test")
            return 0
        input("  " + S.ask("Press Enter once you've subscribed, and I'll send a test.") + " ")
        print("  " + S.ok("Test sent: check your phone.") if send("Welfare workspace", "Notifications work. You'll get them "
                                                   "here.", cfg=cfg) else "  " + S.warn("The test didn't go through (see above)."))
        print("  For follow-up briefs: python3 scripts/followups.py schedule on --days 3")
        return 0
    if a[0] == "--off":
        cfg = load()
        cfg["on"] = False
        save(cfg)
        print("Phone notifications are off.")
        return 0
    if a[0] == "--test":
        ok = send("Welfare workspace: test", "Notifications work.")
        print("Sent." if ok else "Not sent (see above).")
        return 0 if ok else 1
    opts, rest, i = {"priority": "default", "tags": ""}, [], 0
    while i < len(a):
        if a[i] in ("--priority", "--tags") and i + 1 < len(a):
            opts[a[i][2:]] = a[i + 1]
            i += 2
        else:
            rest.append(a[i])
            i += 1
    if len(rest) != 2:
        print('usage: python3 scripts/notify.py "Title" "Message"  (or --help)', file=sys.stderr)
        return 2
    return 0 if send(rest[0], rest[1], **opts) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
