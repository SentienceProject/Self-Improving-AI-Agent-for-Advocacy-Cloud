"""Tests for .claude/hooks/run_log.py: run IDs, run logs, and naming a run's log files like its draft."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if os.name == "nt":   # they use Linux and macOS file features (fcntl); so does hardened mode
    raise unittest.SkipTest("hardened mode's scripts need Linux, macOS or WSL2, not native Windows")

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / ".claude" / "hooks"


def load(name):
    spec = importlib.util.spec_from_file_location(name, next(p for p in (HOOKS / f"{name}.py", HERE / f"{name}.py")
                                                               if p.exists()))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_log = load("run_log")
save_returns = load("save_returns")


def operator(text):
    return {"type": "user", "message": {"role": "user", "content": text}, "timestamp": "2026-11-24T10:00:00Z"}


def writes(path, text="x"):
    return {"type": "assistant", "message": {"id": f"m{abs(hash(path))}", "role": "assistant", "model": "opus",
                                             "content": [{"type": "tool_use", "id": "t", "name": "Write",
                                                          "input": {"file_path": path, "content": text}}]}}


class RunLogNamingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.logs = self.tmp / "workspace" / "logs"
        self.outbox = self.tmp / "workspace" / "outbox"
        self.outbox.mkdir(parents=True)
        self.transcript = self.tmp / "sess.jsonl"
        self.records = []
        self.rid = run_log.run_id_for("sess", create=True, logs=self.logs)
        self.haiku = mock.patch.object(run_log, "haiku_slug", return_value="compare-model-prices").start()
        self.addCleanup(mock.patch.stopall)

    def add(self, *recs):
        self.records += recs
        self.transcript.write_text("".join(json.dumps(r) + "\n" for r in self.records))

    def stop(self):
        run_log.main("stop", {"session_id": "sess", "transcript_path": str(self.transcript)}, logs=self.logs)

    def draft(self, slug, ext="md"):
        p = self.outbox / f"{self.rid}-{slug}.{ext}"
        p.write_text("draft")
        self.add(writes(str(p)))

    def names(self):
        return sorted(p.name for p in self.logs.iterdir() if not p.name.startswith("."))

    def subagent(self, agent_type, agent_id, ret):
        sub = self.tmp / "sess" / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"agent-{agent_id}.jsonl").write_text(json.dumps(
            {"type": "user", "message": {"role": "user", "content": "Please research X. Thank you!"}}) + "\n")
        save_returns.main({"session_id": "sess", "transcript_path": str(self.transcript), "agent_type": agent_type,
                           "agent_id": agent_id, "last_assistant_message": ret}, self.logs)

    def test_run_with_a_draft_takes_its_slug(self):
        self.add(operator("Please write an op-ed on heat stress in factory farms."))
        self.draft("animal-heat-injects")
        self.stop()
        self.assertEqual(self.names(), [f"{self.rid}-animal-heat-injects.log.md"])
        self.haiku.assert_not_called()

    def test_run_without_a_draft_is_named_by_haiku_once(self):
        self.add(operator("What do Bedrock models cost now?"))
        self.stop()
        self.stop()
        self.assertEqual(self.names(), [f"{self.rid}-compare-model-prices.log.md"])
        self.assertEqual(self.haiku.call_count, 1)

    def test_words_when_haiku_fails(self):
        self.haiku.return_value = None
        self.add(operator("Hi! Can you please compare Bedrock model prices for me?"))
        self.stop()
        self.assertEqual(self.names(), [f"{self.rid}-compare-bedrock-model-prices.log.md"])

    def test_later_draft_replaces_haikus_name_and_renames_everything(self):
        self.add(operator("Let's talk about heat stress first."))
        self.stop()
        self.subagent("researcher", "a1", "Finding 1")
        log = self.logs / f"{self.rid}-compare-model-prices.log.md"
        log.write_text(log.read_text() + "\nmy note")
        self.draft("animal-heat-injects")
        self.subagent("critic", "a2", "too long")
        self.stop()
        base = f"{self.rid}-animal-heat-injects"
        self.assertEqual(self.names(), [f"{base}.critique.md", f"{base}.findings.md", f"{base}.log.md",
                                        f"{base}.prompts.md"])
        self.assertIn("my note", (self.logs / f"{base}.log.md").read_text())
        self.assertIn("Finding 1", (self.logs / f"{base}.findings.md").read_text())
        self.assertEqual((self.logs / f"{base}.prompts.md").read_text().count("Prompt to"), 2)

    def test_first_draft_wins(self):
        self.add(operator("Two drafts please."))
        self.draft("short-version", "html")
        self.stop()
        self.draft("long-version")
        self.stop()
        self.assertEqual(self.names(), [f"{self.rid}-short-version.log.md"])

    def test_returns_before_any_name_use_the_bare_run_id(self):
        self.subagent("researcher", "a1", "Finding 1")
        self.assertEqual(self.names(), [f"{self.rid}.findings.md", f"{self.rid}.prompts.md"])

    def test_slug_ending_in_a_kind_stays_a_run_log(self):
        self.add(operator("Peer review this."))
        self.draft("peer-review")
        self.stop()
        self.subagent("verifier", "a1", "checked")
        self.assertEqual(self.names(), [f"{self.rid}-peer-review.log.md", f"{self.rid}-peer-review.prompts.md",
                                        f"{self.rid}-peer-review.review.md"])

    def test_files_from_before_the_change_move_to_the_new_names(self):
        rid = self.rid
        (self.logs / f"{rid}.md").write_text(f"# Run {rid}\n\n{run_log.NOTES}\nold note\n")
        (self.logs / f"{rid}-findings.md").write_text("old finding\n")
        (self.logs / f"{rid}.findings.md").write_text("new finding\n")
        (self.logs / f"{rid}-wild-1.md").write_text("not a hook's file\n")
        (self.logs / f"{rid}-exec-1.json").write_text("{}")
        self.add(operator("Carry on."))
        self.draft("carry-on")
        self.stop()
        base = f"{rid}-carry-on"
        self.assertEqual(self.names(), sorted([f"{rid}-exec-1.json", f"{rid}-wild-1.md", f"{base}.findings.md",
                                               f"{base}.log.md"]))
        findings = (self.logs / f"{base}.findings.md").read_text()
        self.assertIn("old finding", findings)
        self.assertIn("new finding", findings)
        self.assertIn("old note", (self.logs / f"{base}.log.md").read_text())

    def test_other_runs_are_left_alone(self):
        other = self.logs / f"{self.rid}0.md"          # run ...-100 when this is ...-10, say
        other.write_text("x")
        older = self.logs / "2026-10-01-01-review.md"
        older.write_text("x")
        self.add(operator("Hello"))
        self.draft("hello")
        self.stop()
        self.assertTrue(other.exists() and older.exists())


class HaikuSlugTest(unittest.TestCase):
    def reply(self, text, code=0):
        return subprocess.CompletedProcess([], code, stdout=json.dumps({"result": text}), stderr="")

    def call(self, reply):
        with mock.patch.object(run_log, "claude_bin", return_value="/bin/claude"), \
             mock.patch.object(run_log.subprocess, "run", return_value=reply) as run:
            return run_log.haiku_slug("Compare Bedrock prices"), run

    def test_reply_becomes_a_slug(self):
        slug, run = self.call(self.reply("`Compare-Bedrock-Model-Prices`"))
        self.assertEqual(slug, "compare-bedrock-model-prices")
        cmd, kw = run.call_args.args[0], run.call_args.kwargs
        self.assertIn("haiku", cmd)
        self.assertIn("--setting-sources", cmd)
        self.assertEqual(kw["env"][run_log.NAMER_ENV], "1")
        self.assertIn("please", kw["input"].lower())
        self.assertIn("decline", kw["input"])

    def test_decline_failure_and_one_word_give_none(self):
        self.assertIsNone(self.call(self.reply("decline"))[0])
        self.assertIsNone(self.call(self.reply("x", code=1))[0])
        self.assertIsNone(self.call(self.reply("prices"))[0])

    def test_not_called_inside_the_namer(self):
        with mock.patch.dict(os.environ, {run_log.NAMER_ENV: "1"}), \
             mock.patch.object(run_log.subprocess, "run") as run:
            self.assertIsNone(run_log.haiku_slug("x"))
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
