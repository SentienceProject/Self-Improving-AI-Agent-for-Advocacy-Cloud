"""Tests for .claude/hooks/save_returns.py (the SubagentStop log hook)."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

if os.name == "nt":   # they use Linux and macOS file features (O_NOFOLLOW, fcntl); so does hardened mode
    raise unittest.SkipTest("hardened mode's scripts need Linux, macOS or WSL2, not native Windows")

HERE = Path(__file__).resolve().parent
CANDIDATES = [HERE.parent / ".claude" / "hooks" / "save_returns.py", HERE / "save_returns.py"]
spec = importlib.util.spec_from_file_location("save_returns", next(p for p in CANDIDATES if p.exists()))
save_returns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(save_returns)


class SaveReturnsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.logs = self.tmp / "logs"
        self.main = self.tmp / "sess.jsonl"
        self.main.write_text("")

    def event(self, agent_type, agent_id, prompt, ret, meta=None):
        sub = self.tmp / "sess" / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        rec = {"type": "user", "agentId": agent_id, "message": {"role": "user", "content": prompt}}
        (sub / f"agent-{agent_id}.jsonl").write_text(json.dumps(rec) + "\n")
        if meta is not None:
            (sub / f"agent-{agent_id}.meta.json").write_text(meta)
        return {"session_id": "sess", "transcript_path": str(self.main), "agent_type": agent_type,
                "agent_id": agent_id, "last_assistant_message": ret}

    def test_researcher_return_goes_to_findings_verbatim(self):
        ret = "Finding 1: **quoted** text\n\nSources: https://x.org/a"
        run = save_returns.main(self.event("researcher", "a1", "Run ID: 2026-10-05-02. ONE question", ret), self.logs)
        self.assertEqual(run, "2026-10-05-02")
        self.assertIn(ret, (self.logs / "2026-10-05-02.findings.md").read_text())
        self.assertIn("Run ID: 2026-10-05-02. ONE question", (self.logs / "2026-10-05-02.prompts.md").read_text())

    def test_ideator_goes_to_ideation(self):
        save_returns.main(self.event("ideator", "a2", "run 2026-10-05-02", "angles"), self.logs)
        self.assertIn("angles", (self.logs / "2026-10-05-02.ideation.md").read_text())

    def test_drafter_logs_prompt_only(self):
        save_returns.main(self.event("drafter", "a3", "run 2026-10-05-02", "draft done"), self.logs)
        self.assertFalse((self.logs / "2026-10-05-02.findings.md").exists())
        self.assertTrue((self.logs / "2026-10-05-02.prompts.md").exists())

    def test_refire_is_idempotent_but_continuation_appends(self):
        ev = self.event("researcher", "a4", "run 2026-10-05-02", "first")
        save_returns.main(ev, self.logs)
        save_returns.main(ev, self.logs)
        f = self.logs / "2026-10-05-02.findings.md"
        self.assertEqual(f.read_text().count("first"), 1)
        ev["last_assistant_message"] = "follow-up"
        save_returns.main(ev, self.logs)
        self.assertIn("follow-up", f.read_text())

    def test_no_run_id_writes_nothing(self):
        self.assertIsNone(save_returns.main(self.event("researcher", "a5", "no id here", "x"), self.logs))
        # Looking up the run ID may create an empty run registry; nothing else may be written.
        written = [p for p in self.logs.rglob("*") if p.is_file()] if self.logs.exists() else []
        self.assertEqual([p for p in written if not (p.name == ".runs.json" and not p.read_text().strip())], [])

    def test_named_teammate_uses_spawned_type(self):
        # Shape of the real meta file from run 2026-10-07-02.
        meta = json.dumps({"agentType": "translator", "name": "translator", "taskKind": "in_process_teammate",
                           "customAgentType": "ideator", "spawnedAgentType": "ideator"})
        prompt = '<teammate-message teammate_id="team-lead">\nRun ID: 2026-10-07-02\n'
        ev = self.event("translator", "atranslator-6b50", prompt, "dry run angles", meta)
        save_returns.main(ev, self.logs)
        text = (self.logs / "2026-10-07-02.ideation.md").read_text()
        self.assertIn("dry run angles", text)
        self.assertIn("ideator 'translator' atranslator-6b50", text)
        ev["last_assistant_message"] = "counts"
        save_returns.main(ev, self.logs)
        self.assertIn("counts", (self.logs / "2026-10-07-02.ideation.md").read_text())

    def test_bad_or_missing_meta_falls_back_to_reported_type(self):
        save_returns.main(self.event("researcher", "a6", "run 2026-10-05-02", "r", "{not json"), self.logs)
        self.assertIn("r", (self.logs / "2026-10-05-02.findings.md").read_text())
        save_returns.main(self.event("researcher", "a7", "run 2026-10-05-03", "s", "[]"), self.logs)
        self.assertIn("s", (self.logs / "2026-10-05-03.findings.md").read_text())

    def add_lines(self, agent_id, *records):
        with open(self.tmp / "sess" / "subagents" / f"agent-{agent_id}.jsonl", "a") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    @staticmethod
    def handback(tid, msg):
        return {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": "SubagentHandback", "input": {"message": msg}}]}}

    @staticmethod
    def result(tid, error):
        return {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": error, "content": "x"}]}}

    def test_report_delivered_by_handback_is_saved(self):
        # Run 2026-11-07-04: the researcher reported with SubagentHandback, last_assistant_message was empty, and
        # its findings entry was saved blank.
        ev = self.event("researcher", "b1", "Run ID: 2026-11-07-04. question", "")
        self.add_lines("b1", self.handback("t1", "Title: Consciousness in AI (2023), Butlin"), self.result("t1", False))
        save_returns.main(ev, self.logs)
        self.assertIn("Consciousness in AI (2023), Butlin", (self.logs / "2026-11-07-04.findings.md").read_text())

    def test_refused_handback_is_skipped_for_the_final_text(self):
        ev = self.event("critic", "b2", "run 2026-11-07-04", "the plain-text report")
        self.add_lines("b2", self.handback("t1", "blocked attempt"), self.result("t1", True))
        save_returns.main(ev, self.logs)
        text = (self.logs / "2026-11-07-04.critique.md").read_text()
        self.assertIn("the plain-text report", text)
        self.assertNotIn("blocked attempt", text)

    def test_new_meta_shape_gives_the_type(self):
        meta = json.dumps({"agentType": "researcher", "isBuiltIn": False, "description": "x"})
        save_returns.main(self.event("smoke", "b3", "run 2026-11-07-04", "found it", meta), self.logs)
        self.assertIn("found it", (self.logs / "2026-11-07-04.findings.md").read_text())

    def test_unnamed_agent_heading_unchanged(self):
        meta = json.dumps({"agentType": "ideator", "spawnedAgentType": "ideator"})
        save_returns.main(self.event("ideator", "a8", "run 2026-10-05-02", "x", meta), self.logs)
        self.assertNotIn("'", (self.logs / "2026-10-05-02.ideation.md").read_text().split("## ")[1].split("\n")[0])


if __name__ == "__main__":
    unittest.main()
