"""scripts/notify.py and scripts/followups.py: what's sent, when a brief is due, and that nothing goes out when off."""
import http.server
import importlib.util
import sys
import threading
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


notify = load("notify")
followups = load("followups")
TOPIC = "welfare-0123456789abcdef01234567"


class Catch(http.server.BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Catch.got.append((self.path, dict(self.headers), self.rfile.read(n).decode("utf-8")))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


class TestNotify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), Catch)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.cfg = {"on": True, "topic": TOPIC, "server": f"http://127.0.0.1:{cls.srv.server_port}"}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Catch.got.clear()

    def test_sends_title_and_body_to_the_topic(self):
        self.assertTrue(notify.send("Follow-ups ✓ today", "- reply to Ana\n- consultation closes Friday", cfg=self.cfg))
        path, headers, body = Catch.got[0]
        self.assertEqual(path, f"/{TOPIC}")
        self.assertEqual(headers["Title"], "Follow-ups today")      # ASCII only in the header
        self.assertIn("consultation closes Friday", body)

    def test_nothing_sent_when_off_or_without_a_good_topic(self):
        for cfg in ({**self.cfg, "on": False}, {**self.cfg, "topic": "short"}, {**self.cfg, "topic": "../x" * 6}, {}):
            self.assertFalse(notify.send("t", "b", cfg=cfg))
        self.assertEqual(Catch.got, [])

    def test_long_messages_are_cut(self):
        notify.send("t", "x" * 10000, cfg=self.cfg)
        self.assertLessEqual(len(Catch.got[0][2]), notify.MAX_BODY)


class TestDue(unittest.TestCase):
    cfg = {"on": True, "topic": TOPIC, "followups_every_days": 3}

    def test_first_brief_is_due_after_the_morning_hour(self):
        self.assertTrue(followups.due(self.cfg, {}, datetime(2026, 11, 2, 9)))
        self.assertIsNone(followups.due(self.cfg, {}, datetime(2026, 11, 2, 6)))

    def test_waits_the_chosen_number_of_days(self):
        s = {"last_ok_date": "2026-11-02"}
        self.assertIsNone(followups.due(self.cfg, s, datetime(2026, 11, 4, 12)))
        self.assertTrue(followups.due(self.cfg, s, datetime(2026, 11, 5, 9)))

    def test_gives_up_for_the_day_after_three_tries(self):
        self.assertIsNone(followups.due(self.cfg, {"tries": {"2026-11-02": 3}}, datetime(2026, 11, 2, 12)))

    def test_never_due_when_notifications_are_off(self):
        self.assertIsNone(followups.due({**self.cfg, "on": False}, {}, datetime(2026, 11, 2, 12)))
        self.assertIsNone(followups.due({**self.cfg, "followups_every_days": 0}, {}, datetime(2026, 11, 2, 12)))

    def test_phone_block_is_split_off_the_brief(self):
        brief, phone = followups.split_text("# Follow-ups\n## Quiet\nAll quiet.\nTEXT:\n- reply to Ana\nEND TEXT\n")
        self.assertEqual(phone, "- reply to Ana")
        self.assertNotIn("TEXT:", brief)
        self.assertIn("All quiet.", brief)

    def test_brief_can_only_read(self):
        p = followups.settings()["permissions"]
        for tool in ("Bash", "Edit", "Write", "WebFetch", "WebSearch"):
            self.assertIn(tool, p["deny"])
        self.assertTrue(any(r.endswith("my/notify.json)") for r in p["deny"]))


if __name__ == "__main__":
    unittest.main()
