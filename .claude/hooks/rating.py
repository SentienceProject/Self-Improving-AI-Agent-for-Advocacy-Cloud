#!/usr/bin/env python3
"""UserPromptSubmit: save the person's 1-10 rating of a draft without Claude ever seeing it.

When Claude's last reply ended with "How useful was this, 1 to 10? ..." and the person's next message starts with a
number from 1 to 10 ("8", "8/10 good sources, too long*"), this saves it to workspace/feedback.jsonl through
scripts/feedback.py and blocks the message, so it never reaches Claude: no turn runs, nothing goes into the
conversation, and Claude can't react to it, remember it, or be thanked or apologised to. The reason is everything
after the number, up to a `*` if there is one. Claude Code lets a hook block a message or add to it, not rewrite
it, so text after the `*` can't be passed on; the note shows it so the person can send it again.

The draft path comes from the question line (the path in backticks), or failing that the last workspace/ path in
the reply. Only the first message after the question counts, and each reply is rated once (blocked messages don't
show up in the transcript, so the entry keeps the reply's id). A message like "3 more things to fix" right after
the question would be taken as a 3; the note quotes what was saved and where to fix it.
Never blocks anything else: any error just means the message goes through as usual.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUESTION = re.compile(r"useful.*\b1\s*(?:to|-|–|—)\s*10\b", re.I)
ANSWER = re.compile(r"\s*(10|[1-9](?:\.\d)?)\s*(?:/\s*10)?(?=$|[\s.,;:!)*\-–—])[\s.,;:!)\-–—]*(.*)", re.S)
STAR = re.compile(r"(?<!\*)\*(?!\*)")   # a lone *, not markdown bold
PATH = re.compile(r"workspace/[^\s`'\"()\[\]<>*]+\.\w+")


def prompt_text(content):
    """A real prompt's text, or None for tool results and other non-prompt user entries."""
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content and all(c.get("type") == "text" for c in content):
        return " ".join(c.get("text", "") for c in content)
    return None


def last_reply(transcript, prompt):
    """(id, text) of Claude's last reply, if the person hasn't sent anything since except (perhaps) this prompt."""
    reply, rid, since = None, "", []
    with open(transcript, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("isSidechain") or d.get("isMeta") or d.get("isCompactSummary"):
                continue
            content = (d.get("message") or {}).get("content")
            if d.get("type") == "assistant" and isinstance(content, list):
                text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text").strip()
                if text:
                    reply, rid, since = text, str(d.get("uuid", "")), []
            elif d.get("type") == "user" and reply is not None:
                t = prompt_text(content)
                if t is not None:
                    since.append(t.strip())
    if reply is None or any(t != prompt.strip() for t in since):
        return None
    return rid, reply


def main():
    try:
        data = json.loads(sys.stdin.read())
        prompt = data.get("prompt") or ""
        m = ANSWER.fullmatch(prompt)
        if not m or not data.get("transcript_path"):
            return
        found = last_reply(data["transcript_path"], prompt)
        if not found:
            return
        rid, reply = found
        tail = [line for line in reply.splitlines() if line.strip()][-3:]
        asked = next((line for line in reversed(tail) if QUESTION.search(line)), None)
        if not asked:
            return
        sys.path.insert(0, str(ROOT / "scripts"))
        import feedback
        rating = feedback.parse_rating(m.group(1))
        if rating is None or (rid and any(e.get("reply") == rid for e in feedback.entries())):
            return
        parts = STAR.split(m.group(2), maxsplit=1)
        why, rest = " ".join(parts[0].split()), (parts[1] if len(parts) > 1 else "").strip()
        quoted = re.findall(r"`([^`]+)`", asked)
        paths = PATH.findall(reply)
        draft = quoted[-1] if quoted else (paths[-1] if paths else "")
        feedback.record(rating, draft, why, by="hook", reply=rid)
        note = (f"Saved your rating, {rating}/10{f' for {draft}' if draft else ''}"
                f"{f': “{why}”' if why else ''}. Claude didn't see it. If that's wrong, edit or delete the last "
                f"line of {feedback.LOG}.")
        if rest:
            note += ("\nNot sent to Claude either (a hook can't pass on part of a message); send it again if you "
                     f"meant it:\n{rest}")
        print(json.dumps({"decision": "block", "reason": note,
                          "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "suppressOriginalPrompt": True}}))
    except Exception:   # noqa: BLE001 - never get in the way of the person's message
        return


if __name__ == "__main__":
    main()
