""".claude/hooks/rating.py: saves a 1-10 reply to the rating question and keeps it from Claude; nothing else."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUESTION = ("How useful was this, 1 to 10? (the number first, then a word on why if you like, ending with *; "
            "I won't see it) `workspace/outbox/2026-11-18-x.md`")


def assistant(text, uuid="r1"):
    return {"type": "assistant", "uuid": uuid,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


class RatingHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        (self.ws / ".claude" / "hooks").mkdir(parents=True)
        (self.ws / "scripts").mkdir()
        shutil.copy(ROOT / ".claude" / "hooks" / "rating.py", self.ws / ".claude" / "hooks")
        shutil.copy(ROOT / "scripts" / "feedback.py", self.ws / "scripts")
        self.log = self.ws / "workspace" / "feedback.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, prompt, entries):
        t = self.ws / "t.jsonl"
        t.write_text("".join(json.dumps(e) + "\n" for e in entries))
        r = subprocess.run([sys.executable, str(self.ws / ".claude" / "hooks" / "rating.py")],
                           input=json.dumps({"prompt": prompt, "transcript_path": str(t), "session_id": "s"}),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        return r.stdout

    def saved(self):
        try:
            return [json.loads(line) for line in self.log.read_text().splitlines()]
        except OSError:
            return []

    def test_saves_rating_reason_and_draft_and_blocks(self):
        out = json.loads(self.run_hook("8/10 - solid, a bit long", [user("draft it"), assistant("Done.\n\n" + QUESTION)]))
        [e] = self.saved()
        self.assertEqual((e["rating"], e["draft"], e["why"], e["by"], e["reply"]),
                         (8, "workspace/outbox/2026-11-18-x.md", "solid, a bit long", "hook", "r1"))
        self.assertEqual(out["decision"], "block")
        self.assertIn("8/10", out["reason"])
        self.assertIn(str(self.log), out["reason"])

    def test_star_ends_the_reason(self):
        out = json.loads(self.run_hook("7 good sources, **too** long* also fix the title", [assistant(QUESTION)]))
        self.assertEqual(self.saved()[0]["why"], "good sources, **too** long")
        self.assertIn("also fix the title", out["reason"])
        self.run_hook("9*", [assistant(QUESTION, "r2")])
        self.assertEqual((self.saved()[1]["rating"], self.saved()[1]["why"]), (9, ""))

    def test_each_reply_rated_once(self):
        # Blocked messages aren't in the transcript, so the next message looks like the first answer again.
        self.run_hook("8", [assistant(QUESTION)])
        self.assertEqual(self.run_hook("3 more things to fix", [assistant(QUESTION)]), "")
        self.assertEqual(len(self.saved()), 1)

    def test_current_prompt_already_in_transcript(self):
        self.run_hook("7.5", [assistant(QUESTION), user("7.5")])
        self.assertEqual(self.saved()[0]["rating"], 7.5)

    def test_falls_back_to_last_workspace_path(self):
        self.run_hook("9", [assistant("Saved to workspace/outbox/a.md.\n\nHow useful was this, 1 to 10?")])
        self.assertEqual(self.saved()[0]["draft"], "workspace/outbox/a.md")

    def test_ignores_non_ratings(self):
        for prompt in ("great, thanks", "0", "11", "10x better please", "2026 is the year", "one more thing"):
            self.assertEqual(self.run_hook(prompt, [assistant(QUESTION)]), "")
        self.assertEqual(self.saved(), [])

    def test_only_right_after_the_question(self):
        self.run_hook("5", [assistant("Here's what I found.")])
        self.run_hook("5", [assistant(QUESTION), user("thanks"), assistant("You're welcome.")])
        self.run_hook("5", [assistant(QUESTION), user("can you also check X")])
        self.assertEqual(self.saved(), [])

    def test_question_before_a_last_line(self):
        # Cloud sessions save after asking; Claude may add "Saved." after the question in the same turn.
        tool = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}}
        out = self.run_hook("9 helpful", [user("draft it"), assistant("Draft.\n\n" + QUESTION, "q"), tool,
                                          assistant("Saved to GitHub.", "s")])
        self.assertEqual(json.loads(out)["decision"], "block")
        self.assertEqual((self.saved()[0]["rating"], self.saved()[0]["reply"]), (9, "q"))
        self.assertEqual(self.run_hook("5", [assistant(QUESTION, "old"), user("more"), assistant("Saved.", "s2")]), "")

    def test_tool_results_dont_count_as_prompts(self):
        tool = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}}
        self.run_hook("6", [tool, assistant(QUESTION), tool])
        self.assertEqual(len(self.saved()), 1)

    def test_bad_input_never_fails(self):
        r = subprocess.run([sys.executable, str(self.ws / ".claude" / "hooks" / "rating.py")], input="not json",
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.run_hook("8", []), "")


if __name__ == "__main__":
    unittest.main()
