"""backlog.py: the item list, progress and done records, and when an unattended run may start (fake usage)."""
import importlib.util
import json
import os
import shutil
import subprocess
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
        self.assertEqual(self.B.num(s, "weekly usage cap (%)"), 90)
        self.assertEqual(self.B.num(s, "most of each 5-hour session (%)"), 90)
        self.assertEqual(s["quiet hours"], "")
        self.assertTrue(s["ask me the day before the reset"].startswith("yes"))
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
            self.assertIsNone(self.B.why_not(s, u(89, 10), now))      # anywhere below the cap
            self.assertIn("weekly usage is at 90", self.B.why_not(s, u(90, 10), now))
            self.assertIn("5-hour session", self.B.why_not(s, u(60, 10, sess=95), now))
            self.assertIn("can't be read", self.B.why_not(s, None, now))
        with mock.patch.object(self.B, "others_busy", return_value="tech watch is running"):
            self.assertEqual(self.B.why_not(s, u(60, 10), now), "tech watch is running")
            f = self.tmp / "my" / "backlog.md"
            f.write_text(f.read_text().replace("**Tech watch and evals first:** yes", "**Tech watch and evals first:** no"))
            self.assertIsNone(self.B.why_not(self.B.settings(), u(60, 10), now))

    def setting(self, old, new):
        f = self.tmp / "my" / "backlog.md"
        f.write_text(f.read_text().replace(old, new, 1))

    def test_quiet_hours(self):
        local = lambda h, m=0: datetime(2026, 11, 6, h, m).astimezone()
        s = self.B.settings()
        self.assertIsNone(self.B.quiet(s))
        self.assertTrue(self.B.in_quiet(s, local(14)))             # none given: any hour
        self.setting("**Quiet hours:**", "**Quiet hours:** 00:00-06:00")
        s = self.B.settings()
        self.assertTrue(self.B.in_quiet(s, local(3)))
        self.assertFalse(self.B.in_quiet(s, local(6)))
        self.setting("**Quiet hours:** 00:00-06:00", "**Quiet hours:** 23:30 - 6")   # past midnight
        s = self.B.settings()
        self.assertTrue(self.B.in_quiet(s, local(23, 45)))
        self.assertTrue(self.B.in_quiet(s, local(5, 59)))
        self.assertFalse(self.B.in_quiet(s, local(23, 0)))

    def test_quiet_hours_session_share_and_answers(self):
        self.cli("add", "Something to run")
        self.B.trusted = lambda: True
        self.setting("**Quiet hours:**", "**Quiet hours:** 00:00-06:00")
        self.setting("**Most of each 5-hour session (%):** 90", "**Most of each 5-hour session (%):** 50")
        s = self.B.settings()
        night, day = datetime(2026, 11, 6, 2).astimezone(), datetime(2026, 11, 6, 14).astimezone()
        u = lambda now, week=60, sess=10: {"seven_day": week, "five_hour": sess,
                                           "week_resets_at": (now + timedelta(hours=10)).isoformat()}
        with mock.patch.object(self.B, "others_busy", return_value=None):
            self.assertIsNone(self.B.why_not(s, u(night), night, st={}))
            self.assertIn("quiet hours", self.B.why_not(s, u(day), day, st={}))
            self.assertIn("uses up to 50%", self.B.why_not(s, u(night, sess=55), night, st={}))
            key = self.B.week_key(night + timedelta(hours=10))
            allst = {"answer": {"for": self.B.week_key(day + timedelta(hours=10)), "choice": "all"}}
            self.assertIsNone(self.B.why_not(s, u(day, week=95, sess=55), day, st=allst))   # any hour, up to 98%
            self.assertIn("98%", self.B.why_not(s, u(day, week=98), day, st=allst))
            skip = {"answer": {"for": key, "choice": "skip"}}
            self.assertIn("skip", self.B.why_not(s, u(night), night, st=skip))
            old = {"answer": {"for": "2020-01-01", "choice": "skip"}}           # last cycle's answer doesn't count
            self.assertIsNone(self.B.why_not(s, u(night), night, st=old))

    def test_asks_once_a_cycle_outside_quiet_hours(self):
        self.cli("add", "Something to run")
        self.setting("**Quiet hours:**", "**Quiet hours:** 00:00-06:00")
        s = self.B.settings()
        tomorrow = datetime.now() + timedelta(days=1)    # in the future, so the session start still sees it
        night, day = (tomorrow.replace(hour=h, minute=0, second=0, microsecond=0).astimezone() for h in (2, 14))
        reset = (day + timedelta(hours=20)).isoformat()
        sent = []
        fake = type("N", (), {"load": staticmethod(lambda: {}), "ready": staticmethod(lambda cfg: True),
                              "send": staticmethod(lambda *a, **k: sent.append((a, k)))})
        with mock.patch.dict(sys.modules, {"notify": fake}):
            st = {}
            self.assertFalse(self.B.ask(s, {"seven_day": 60, "week_resets_at": (night + timedelta(hours=20)).isoformat()}, st, night))
            self.assertFalse(self.B.ask(s, {"seven_day": 95, "week_resets_at": reset}, st, day))   # nothing left
            self.assertFalse(self.B.ask(s, {"seven_day": 60, "week_resets_at": (day + timedelta(hours=40)).isoformat()}, st, day))
            self.assertTrue(self.B.ask(s, {"seven_day": 60, "week_resets_at": reset}, st, day))
            self.assertFalse(self.B.ask(s, {"seven_day": 60, "week_resets_at": reset}, st, day))   # once
        self.assertEqual(len(sent), 1)
        self.assertIn("00:00-06:00", sent[0][0][1])
        self.assertEqual([r for _, r in sent[0][1]["actions"]], ["backlog:all", "backlog:skip", "backlog:normal"])
        q = self.B.question_for_session(s)                  # the session start passes it on once
        self.assertIn("answer all", q)
        self.assertIsNone(self.B.question_for_session(s))

    def test_answer_command(self):
        self.setting("**Weekly reset (if it can't be read):**", "**Weekly reset (if it can't be read):** Thursday 09:00")
        self.B.usage = lambda: None
        self.cli("answer", "skip")
        r = self.B.week_reset(None, self.B.settings())
        self.assertEqual(self.B.answer(self.B.state(), r), "skip")
        os.environ["AI_WELFARE_BACKLOG_RUN"] = "B1"
        with self.assertRaises(SystemExit):
            self.cli("answer", "all")
        self.assertEqual(self.B.answer(self.B.state(), r), "skip")

    def test_old_settings_file_stop_line_becomes_the_cap(self):
        f = self.tmp / "my" / "backlog.md"
        f.write_text(f.read_text().replace("**Weekly usage cap (%):** 90",
                                           "**Start below weekly usage (%):** 70\n\n**Stop at weekly usage (%):** 80"))
        self.assertEqual(self.B.limits(self.B.settings(), "normal"), (80, 90))

    def test_a_running_item_stops_at_the_cap(self):
        self.B.CHECK_EVERY = 0.2
        readings = iter([{"seven_day": 85, "five_hour": 10}, None, {"seven_day": 91, "five_hour": 10}])
        self.B.usage = lambda tries=3: next(readings, {"seven_day": 91, "five_hour": 10})
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.PIPE, text=True,
                             start_new_session=os.name != "nt")
        t0 = time.time()
        out, why = self.B.watch(p, (90, 90))
        self.assertIn("weekly usage is at 91%", why)
        self.assertLess(time.time() - t0, 20)
        self.assertIsNotNone(p.poll())
        p = subprocess.Popen([sys.executable, "-c", "print('{}')"], stdout=subprocess.PIPE, text=True)
        self.assertEqual(self.B.watch(p, (90, 90)), ("{}\n", None))   # finishing on its own

    @unittest.skipIf(os.name == "nt", "the fake claude is a shell script")
    def test_capped_item_keeps_its_place(self):
        self.cli("add", "A long item")
        fake = self.tmp / "claude"
        fake.write_text(f"#!/bin/sh\nexec {sys.executable} -c 'import time; time.sleep(60)'\n")
        fake.chmod(0o755)
        self.B.claude_bin = lambda: str(fake)
        self.B.CHECK_EVERY = 0.2
        self.B.usage = lambda tries=3: {"seven_day": 92, "five_hour": 10}
        self.assertEqual(self.B.run_item(self.B.parse()[0], self.B.settings()), "capped")
        self.assertIn("stopped at the usage cap", self.B.PROGRESS.read_text())
        self.assertIn("partway", next(self.B.RESULTS.glob("B1-*-reply.md")).read_text())
        self.assertFalse(list(self.B.DIR.glob(".prompt-*")))
        self.assertEqual([i["id"] for i in self.B.parse()], ["B1"])        # still open

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
