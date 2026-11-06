"""backlog.py: the item list, progress and done records, and when an unattended run may start (fake usage)."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


class TestBacklog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ("scripts", "templates", "my", "workspace", "techwatch/state"):
            (self.tmp / d).mkdir(parents=True)
        for f in ("backlog.py", "backlog_prompt.md", "style.py"):
            shutil.copy(REPO / "scripts" / f, self.tmp / "scripts")
        shutil.copy(REPO / "templates" / "backlog.md", self.tmp / "my" / "backlog.md")   # on, with the defaults
        spec = importlib.util.spec_from_file_location("backlog_t", self.tmp / "scripts" / "backlog.py")
        self.B = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.B)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("CLAUDECODE", None)
        os.environ.pop("AI_WELFARE_BACKLOG_RUN", None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp)

    def cli(self, *args):
        return self.B.main(list(args))

    def test_settings_defaults_from_template(self):
        s = self.B.settings()
        self.assertTrue(self.B.on(s))
        self.assertEqual(self.B.num(s, "start below weekly usage (%)"), 85)
        self.assertEqual(self.B.num(s, "stop at weekly usage (%)"), 90)
        self.assertTrue(s["claude adds items"].startswith("yes"))
        self.assertTrue(s["tech watch and evals first"].startswith("yes"))

    def test_add_ids_progress_done(self):
        self.cli("add", "Check whether the 2023 shrimp study was replicated", "--size", "small")
        os.environ["CLAUDECODE"] = "1"
        self.cli("add", "Second angle on the op-ed", "--details", "Try the farmers' view.")
        items = self.B.parse()
        self.assertEqual([i["id"] for i in items], ["B1", "B2"])
        self.assertEqual([i["by"] for i in items], ["me", "claude"])
        self.cli("progress", "B1", "--note", "found 1 of 3 sources", "--path", "workspace/drafts/x.md")
        self.assertIn("B1 (small, has progress)", "\n".join(self.B.short()))
        self.cli("done", "B1", "--result", "workspace/backlog/results/B1.md")
        self.assertEqual([i["id"] for i in self.B.parse()], ["B2"])
        self.assertIn("B1 · done", self.B.DONE.read_text())
        self.cli("add", "Third")                       # ids never reuse a finished one
        self.assertEqual(self.B.parse()[-1]["id"], "B3")

    def test_hand_written_items_get_ids(self):
        self.B.ITEMS.parent.mkdir(parents=True)
        self.B.ITEMS.write_text("# Backlog\n\n## Look into fish stunning costs\nSome details.\n", encoding="utf-8")
        items = self.B.parse()
        self.assertEqual(items[0]["id"], "B1")
        self.assertEqual(items[0]["details"], "Some details.")
        self.assertIn("## B1 · Look into fish stunning costs", self.B.ITEMS.read_text())

    def test_suggest_only_blocks_claude_but_not_the_person(self):
        f = self.tmp / "my" / "backlog.md"
        f.write_text(f.read_text().replace("**Claude adds items:** yes", "**Claude adds items:** suggest only"))
        os.environ["CLAUDECODE"] = "1"
        with self.assertRaises(SystemExit):
            self.cli("add", "An idea of Claude's")
        os.environ.pop("CLAUDECODE")
        self.cli("add", "My own idea")
        self.assertEqual(len(self.B.parse()), 1)

    def test_old_items_wait_for_keep(self):
        self.cli("add", "Old idea")
        items = self.B.parse()
        items[0]["added"] = (date.today() - timedelta(days=40)).isoformat()
        self.B.write(items)
        self.assertTrue(self.B.stale(self.B.parse()[0]))
        self.assertIn("still wanted?", self.B.short()[0])
        self.cli("keep", "B1")
        self.assertFalse(self.B.stale(self.B.parse()[0]))

    def test_when_a_run_starts(self):
        self.cli("add", "Something to run")
        s, now = self.B.settings(), datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
        u = lambda week, hours, sess=10: {"seven_day": week, "five_hour": sess,
                                          "week_resets_at": (now + timedelta(hours=hours)).isoformat()}
        self.B.trusted = lambda: True
        with mock.patch.object(self.B, "others_busy", return_value=None):
            self.assertIsNone(self.B.why_not(s, u(60, 10), now))
            self.assertIn("hours away", self.B.why_not(s, u(60, 30), now))
            self.assertIn("weekly usage is at 85", self.B.why_not(s, u(85, 10), now))
            self.assertIn("5-hour session", self.B.why_not(s, u(60, 10, sess=95), now))
            self.assertIn("can't be read", self.B.why_not(s, None, now))
        with mock.patch.object(self.B, "others_busy", return_value="tech watch is running"):
            self.assertEqual(self.B.why_not(s, u(60, 10), now), "tech watch is running")
            f = self.tmp / "my" / "backlog.md"
            f.write_text(f.read_text().replace("**Tech watch and evals first:** yes", "**Tech watch and evals first:** no"))
            self.assertIsNone(self.B.why_not(self.B.settings(), u(60, 10), now))

    def test_untrusted_folder_never_runs(self):
        self.cli("add", "Something to run")
        self.B.trusted = lambda: False
        now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
        u = {"seven_day": 10, "five_hour": 1, "week_resets_at": (now + timedelta(hours=2)).isoformat()}
        with mock.patch.object(self.B, "others_busy", return_value=None):
            self.assertIn("doesn't trust this folder", self.B.why_not(self.B.settings(), u, now))

    def test_reset_from_setting_when_unreadable(self):
        f = self.tmp / "my" / "backlog.md"
        f.write_text(f.read_text().replace("**Weekly reset (if it can't be read):**", "**Weekly reset (if it can't be read):** Thursday 09:00"))
        r = self.B.week_reset({"seven_day": 50}, self.B.settings())
        self.assertEqual((r.weekday(), r.hour, r.minute), (3, 9, 0))
        self.assertGreater(r, datetime.now().astimezone())

    def test_tech_watch_goes_first(self):
        tw = self.tmp / "techwatch" / "state"
        (tw / "run.lock").write_text(f"{os.getpid()} {time.time():.0f}")
        self.assertEqual(self.B.others_busy(), "tech watch is running")
        (tw / "run.lock").unlink()
        (self.tmp / "my" / "techwatch.md").write_text("**Tech watch runs:** automatically\n")
        (tw / "last_auto").write_text((date.today() - timedelta(days=1)).isoformat())
        self.assertIn("today's run", self.B.others_busy())
        (tw / "last_auto").write_text(date.today().isoformat())
        self.assertIsNone(self.B.others_busy())

    def test_off_means_nothing(self):
        f = self.tmp / "my" / "backlog.md"
        f.write_text(f.read_text().replace("**Backlog:** on", "**Backlog:** off"))
        self.assertIn("off", self.B.why_not(self.B.settings(), {"seven_day": 1}))

    def test_prompt_carries_progress_and_politeness(self):
        self.cli("add", "Draft a letter to the council", "--details", "About the octopus farm.")
        self.cli("progress", "B1", "--note", "an outline exists", "--path", "workspace/drafts/outline.md")
        p = self.B.prompt_for(self.B.parse()[0])
        self.assertIn("an outline exists", p)
        self.assertIn("You're welcome to decline", p)
        self.assertIn("Don't try to send, post, share", p)
        self.assertNotIn("{", p.split("**When you finish:**")[0])


if __name__ == "__main__":
    unittest.main()
