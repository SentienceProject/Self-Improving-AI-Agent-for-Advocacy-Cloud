#!/usr/bin/env python3
"""PreToolUse hook for the browsers' tools: the agent Firefox's (mcp__firefox__*, set up by scripts/browser_setup.py)
and, in cloud sessions, the Playwright browser's (mcp__cloud-browser__*, scripts/cloud_browser.py). Two jobs:

1. Readable permission prompts. The browser tools name page elements by snapshot IDs ("e31"), which tell you
   nothing. For clicking, typing, keys, dragging, uploads and dialogs this looks the ID up in the caller's latest
   page snapshot and puts a description at the front of the call, as an "action" field, which the permission
   prompt shows: Click button "Search" (in form "Search") on <page>. (Claude Code doesn't display a hook's reason
   in the prompt, so it goes into the input; the browser server ignores the extra field.) Labels are what the page
   says an element is; a hostile page can mislabel its buttons.

   The Playwright browser names elements by snapshot references ("e31") too, next to a description Claude writes
   itself; the page's own description is used when the reference is in the last snapshot.

2. Checkpoints for the browsing agents (browser-haiku, browser-sonnet, browser-opus, and cloud-browser-haiku,
   -sonnet and -opus in cloud sessions). After a set number of
   interactions or page visits (CADENCE below; reading a page doesn't count) the agent's next browser call is refused and it's asked to
   hand back to the main session with a short report. It can't use the browser again until the main session has
   replied (continued it with SendMessage). Every few checkpoints the report is marked PAGE CHECK DUE: the main
   session then reads the page itself before continuing.

Fails closed for the browsing agents (an error refuses the call), open for the main session (whose ask rules still
apply). State is kept per agent in ~/.local/state/agent-firefox/checkpoints/.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# agent type: (interactions, page visits) allowed between checkpoints, and PAGE CHECK DUE every n-th checkpoint
# (0 = never). Interactions (clicking, typing, keys, forms, dialogs) are what can act on your accounts, so they're
# held tighter; visiting a page is lower risk. Reading a page counts as neither.
CADENCE = {"browser-haiku": (3, 10, 2), "browser-sonnet": (6, 20, 2), "browser-opus": (12, 40, 0)}
CADENCE.update({f"cloud-{k}": v for k, v in CADENCE.items()})
INTERACT = {"click_by_uid", "hover_by_uid", "fill_by_uid", "fill_form_by_uid", "type_text", "press_key",
            "drag_by_uid_to_uid", "upload_file_by_uid", "accept_dialog", "dismiss_dialog", "set_download_behavior"}
VISIT = {"navigate_page", "new_page", "close_page", "navigate_history"}
DESCRIBE = {"click_by_uid", "fill_by_uid", "fill_form_by_uid", "type_text", "press_key", "drag_by_uid_to_uid",
            "upload_file_by_uid", "accept_dialog"}
# The cloud sessions' Playwright browser (scripts/cloud_browser.py)
PW_INTERACT = {"browser_click", "browser_hover", "browser_type", "browser_press_key", "browser_fill_form",
               "browser_select_option", "browser_drag", "browser_drop", "browser_file_upload",
               "browser_handle_dialog", "browser_evaluate", "browser_run_code_unsafe"}
PW_VISIT = {"browser_navigate", "browser_navigate_back", "browser_tabs", "browser_close"}
PW_DESCRIBE = {"browser_click", "browser_type", "browser_press_key", "browser_fill_form", "browser_select_option",
               "browser_drag", "browser_drop", "browser_file_upload", "browser_handle_dialog", "browser_evaluate",
               "browser_run_code_unsafe"}
PREFIXES = {"mcp__firefox__": (INTERACT, VISIT, DESCRIBE), "mcp__cloud-browser__": (PW_INTERACT, PW_VISIT, PW_DESCRIBE)}
STATE = Path.home() / ".local" / "state" / "agent-firefox" / "checkpoints"
TAIL = 8 << 20   # read at most the last 8 MB of a transcript


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def clean(s, n=80):
    s = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(s))).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def transcript(d):
    main = Path(d.get("transcript_path", ""))
    if d.get("agent_id"):
        return main.with_suffix("") / "subagents" / f"agent-{d['agent_id']}.jsonl"
    return main


def entries_backwards(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL))
            lines = f.read().splitlines()
    except OSError:
        return
    for line in reversed(lines):
        try:
            yield json.loads(line)
        except ValueError:
            continue


def results(entry):
    """Text of the tool results in a transcript entry."""
    content = (entry.get("message") or {}).get("content")
    if entry.get("type") != "user" or not isinstance(content, list):
        return
    for block in content:
        if block.get("type") == "tool_result":
            c = block.get("content")
            yield "\n".join(b.get("text", "") for b in c if isinstance(b, dict)) if isinstance(c, list) else str(c)


def page_context(path):
    """(latest snapshot text, latest known page URL) from a transcript."""
    snap = url = None
    for e in entries_backwards(path):
        for text in results(e):
            if snap is None and (text.lstrip().startswith("Snapshot") or "[ref=" in text):
                snap = text
            if url is None:
                m = (re.search(r"^>\[\d+\] .*\((\S+)\)\s*$", text, re.M) or re.search(r"→ (\S+)", text)
                     or re.search(r"^- Page URL: (\S+)", text, re.M))
                url = m.group(1) if m else None
        if snap and url:
            break
    return snap, url


def element(snap, uid):
    """'button "Search" (in form "Search")' for a snapshot ID, or None."""
    if not snap or not uid:
        return None
    rows = [(len(m.group(1)), m.group(2), m.group(3)) for m in re.finditer(r"^( *)uid=(\S+) (.*)$", snap, re.M)]
    for i, (indent, u, rest) in enumerate(rows):
        if u != uid:
            continue
        rest = re.sub(r'\s(src|tag)=\S+', "", rest)
        name = re.match(r'\S+ ("[^"]*")', rest)
        if name:   # text="Search" only repeats the name
            rest = rest.replace(f" text={name.group(1)}", "")
        desc = clean(rest, 90)
        for j in range(i - 1, -1, -1):   # nearest named ancestor, for context
            if rows[j][0] < indent and '"' in rows[j][2]:
                desc += f" (in {clean(rows[j][2].split(' href=')[0], 50)})"
                break
            if rows[j][0] < indent:
                indent = rows[j][0]
        return desc
    return None


def bare(line):
    """A Playwright snapshot line without its [ref=..] [cursor=..] tags and trailing colon."""
    return re.sub(r"\s*\[[a-z]+=[^\]]*\]", "", line).rstrip(":")


def pw_element(snap, ref):
    """'button "Search" (in form "Search")' for a Playwright snapshot reference, or None."""
    if not snap or not ref:
        return None
    rows = [(len(m.group(1)), m.group(2)) for m in re.finditer(r"^( *)- (.*)$", snap, re.M)]
    for i, (indent, rest) in enumerate(rows):
        if f"[ref={ref}]" not in rest:
            continue
        desc = clean(bare(rest), 90)
        for j in range(i - 1, -1, -1):   # nearest named ancestor, for context
            if rows[j][0] < indent and '"' in rows[j][1]:
                desc += f" (in {clean(bare(rows[j][1]), 50)})"
                break
            if rows[j][0] < indent:
                indent = rows[j][0]
        return desc
    return None


def pw_describe(tool, args, snap):
    def el(target, label):
        found = pw_element(snap, target)
        if found:
            return found
        if label:
            return f'"{clean(label, 60)}" (Claude\'s description; not in the last snapshot)'
        return f"element {clean(target, 40)} (not in the last snapshot: the page may have changed)"
    if tool == "browser_click":
        return ("Double-click " if args.get("doubleClick") else "Click ") + el(args.get("target"), args.get("element"))
    if tool == "browser_type":
        then = ", then press Enter" if args.get("submit") else ""
        return f'Type "{clean(args.get("text"))}" into {el(args.get("target"), args.get("element"))}{then}'
    if tool == "browser_press_key":
        return f"Press {clean(args.get('key'), 30)}"
    if tool == "browser_fill_form":
        return "Fill a form: " + "; ".join(f'{el(f.get("target"), f.get("name"))} = "{clean(f.get("value"), 40)}"'
                                           for f in args.get("fields", [])[:8])
    if tool == "browser_select_option":
        return (f"Choose {', '.join(clean(v, 30) for v in args.get('values', [])[:5])} in "
                + el(args.get("target"), args.get("element")))
    if tool == "browser_drag":
        return (f"Drag {el(args.get('startTarget'), args.get('startElement'))} onto "
                f"{el(args.get('endTarget'), args.get('endElement'))}")
    if tool == "browser_drop":
        what = ", ".join(clean(p, 60) for p in args.get("paths", [])[:3]) or "data"
        return f"Drop {what} onto {el(args.get('target'), args.get('element'))}"
    if tool == "browser_file_upload":
        return "Upload " + (", ".join(clean(p, 80) for p in args.get("paths", [])[:3]) or "nothing (cancel the chooser)")
    if tool == "browser_handle_dialog":
        return ("Accept the page's dialog (OK / confirm)" + (f', answering "{clean(args["promptText"])}"'
                                                            if args.get("promptText") else "")
                if args.get("accept") else "Dismiss the page's dialog")
    if tool == "browser_evaluate":
        return f"Run JavaScript on the page: {clean(args.get('function'), 120)}"
    if tool == "browser_run_code_unsafe":
        return ("Run Playwright code, which can do anything in the browser: "
                + clean(args.get("code") or f"from the file {args.get('filename')}", 120))
    return tool


def describe(tool, args, snap):
    def el(uid):
        return element(snap, uid) or f"element {uid} (not in the last snapshot: the page may have changed)"
    if tool == "click_by_uid":
        return ("Double-click " if args.get("dblClick") else "Click ") + el(args.get("uid"))
    if tool == "fill_by_uid":
        return f'Fill {el(args.get("uid"))} with "{clean(args.get("value"))}"'
    if tool == "fill_form_by_uid":
        return "Fill a form: " + "; ".join(f'{el(f.get("uid"))} = "{clean(f.get("value"), 40)}"'
                                           for f in args.get("elements", [])[:8])
    if tool == "type_text":
        where = f" into {el(args['uid'])}" if args.get("uid") else " into the focused field"
        then = f", then press {clean(args['submitKey'], 20)}" if args.get("submitKey") else ""
        return f'Type "{clean(args.get("text"))}"{where}{then}'
    if tool == "press_key":
        return f"Press {clean(args.get('key'), 30)}"
    if tool == "drag_by_uid_to_uid":
        return f"Drag {el(args.get('fromUid'))} onto {el(args.get('toUid'))}"
    if tool == "upload_file_by_uid":
        return f"Upload the file {clean(args.get('filePath'), 100)} through {el(args.get('uid'))}"
    if tool == "accept_dialog":
        return "Accept the page's dialog (OK / confirm)"
    return tool


def decide(decision, reason, updated=None):
    out = {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}
    if updated is not None:
        out["updatedInput"] = updated
    print(json.dumps({"hookSpecificOutput": out}))
    sys.exit(0)


def resumed_since(path, stamp):
    """Has the main session sent the agent a message (a plain user turn) since stamp?"""
    for e in entries_backwards(path):
        ts = e.get("timestamp") or ""
        if ts and ts <= stamp:
            return False
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "user" and (isinstance(content, str) or (
                isinstance(content, list) and any(b.get("type") == "text" for b in content))):
            return True
    return False


def checkpoint(d, tool, path, interact=INTERACT, visit=VISIT):
    max_acts, max_visits, check = CADENCE[d["agent_type"]]
    STATE.mkdir(parents=True, exist_ok=True)
    week_ago = datetime.now().timestamp() - 7 * 86400
    for old in STATE.glob("*.json"):   # finished agents' records
        if old.stat().st_mtime < week_ago:
            old.unlink(missing_ok=True)
    f = STATE / f"{re.sub(r'[^A-Za-z0-9_-]', '', d['agent_id'])}.json"
    st = {"acts": 0, "visits": 0, "checkpoint": 0, "blocked_at": None,
          **(json.loads(f.read_text()) if f.exists() else {})}
    if st["blocked_at"]:
        if not resumed_since(path, st["blocked_at"]):
            decide("deny", f"Still waiting for the main session's reply to checkpoint {st['checkpoint']}. Please "
                           "end your turn now with your report (about two sentences, and the current URL).")
        st.update(blocked_at=None, acts=0, visits=0)
    kind = "acts" if tool in interact else "visits" if tool in visit else None
    if kind:
        if st[kind] >= (max_acts if kind == "acts" else max_visits):
            st["checkpoint"] += 1
            st["blocked_at"] = now()
            f.write_text(json.dumps(st))
            due = check and st["checkpoint"] % check == 0
            decide("deny", f"Checkpoint {st['checkpoint']}: since the last check-in you've made {st['acts']} "
                           f"interactions and visited {st['visits']} pages. Please stop here and end your turn "
                           "with your report to the main session"
                           + (', starting with the words "PAGE CHECK DUE"' if due else "")
                           + ": in about two sentences, what you did since the last checkpoint and what you'd do "
                           "next, and the current page's URL. Please don't call browser tools again until the "
                           "main session replies; it will continue you, take over, or stop. Thank you!")
        st[kind] += 1
    f.write_text(json.dumps(st))


def main():
    d = json.load(sys.stdin)
    name = d.get("tool_name", "")
    prefix = next((p for p in PREFIXES if name.startswith(p)), "mcp__firefox__")
    interact, visit, described = PREFIXES[prefix]
    tool = name.removeprefix(prefix)
    args = d.get("tool_input") or {}
    path = transcript(d)
    agent = d.get("agent_type") if d.get("agent_type") in CADENCE else None
    try:
        if agent:
            checkpoint(d, tool, path, interact, visit)
        if tool in described:
            snap, url = page_context(path)
            if not snap and agent:   # the agent may be acting on a snapshot the main session took
                snap = page_context(Path(d.get("transcript_path", "")))[0]
            who = f"{agent} agent" if agent else "Claude"
            what = ((pw_describe if prefix == "mcp__cloud-browser__" else describe)(tool, args, snap)
                    + (f" on {clean(url, 100)}" if url else ""))
            decide("ask", f"Browser ({who}): {what}", updated={"action": what, **args})
    except SystemExit:
        raise
    except Exception as e:   # noqa: BLE001
        if agent:
            decide("deny", f"The browser guard failed ({clean(e, 120)}), so this call was refused. Please stop and "
                           "report to the main session.")
        return


if __name__ == "__main__":
    main()
