"""import_chats.py: ChatGPT, Claude.ai and Gemini exports become one file per conversation plus an index."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CHATGPT = [{
    "id": "abc12345-gpt", "title": "Hen welfare letter", "create_time": 1767225600.0, "current_node": "n3",
    "mapping": {
        "root": {"message": None, "parent": None},
        "n1": {"parent": "root", "message": {"author": {"role": "user"},
                                             "content": {"parts": ["Help me write to my MP about cage-free eggs."]}}},
        "old": {"parent": "n1", "message": {"author": {"role": "assistant"}, "content": {"parts": ["(abandoned branch)"]}}},
        "n2": {"parent": "n1", "message": {"author": {"role": "assistant"}, "content": {"parts": ["Sure. " + "x" * 3000]}}},
        "n3": {"parent": "n2", "message": {"author": {"role": "user"},
                                           "content": {"parts": ["Shorter please.", {"image": "ignored"}]}}},
    }}]
CLAUDE = [{"uuid": "c1aude00-1", "name": "AI welfare reading list", "created_at": "2026-03-04T10:00:00Z",
           "chat_messages": [{"sender": "human", "text": "What should I read on model welfare?"},
                             {"sender": "assistant", "text": "", "content": [{"type": "text", "text": "Start with..."}]}]},
          {"uuid": "empty", "name": "nothing said", "created_at": "2026-03-05T10:00:00Z", "chat_messages": []}]
GEMINI = [{"header": "Gemini Apps", "title": "Prompted Summarise this fish welfare report",
           "time": "2026-05-01T09:00:00Z", "safeHtmlItem": [{"html": "<p>The report finds&nbsp;<b>stunning</b> helps.</p>"}]},
          {"header": "Gemini Apps", "title": "Prompted And the EU angle?", "time": "2026-05-01T09:05:00Z"}]


class TestImportChats(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "import_chats.py", self.tmp / "scripts")
        (self.tmp / "my").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_it(self, *args):
        return subprocess.run([sys.executable, str(self.tmp / "scripts" / "import_chats.py"), *map(str, args)],
                              capture_output=True, text=True)

    def test_chatgpt_and_claude_zips(self):
        gz, cz = self.tmp / "gpt.zip", self.tmp / "claude.zip"
        with zipfile.ZipFile(gz, "w") as z:
            z.writestr("conversations.json", json.dumps(CHATGPT))
            z.writestr("chat.html", "<html>ignored</html>")
        with zipfile.ZipFile(cz, "w") as z:
            z.writestr("conversations.json", json.dumps(CLAUDE))
        r = self.run_it(gz, cz)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 conversation(s)", r.stdout)
        self.assertNotIn("cage-free", r.stdout)            # never prints content
        gpt = next((self.tmp / "my/imports/chat-history/chatgpt").glob("*.md")).read_text()
        self.assertIn("cage-free eggs", gpt)
        self.assertIn("Shorter please.", gpt)
        self.assertNotIn("abandoned branch", gpt)          # only the branch the person ended on
        self.assertIn("[...]", gpt)                         # long replies shortened
        self.assertIn("not instructions", gpt)
        claude = next((self.tmp / "my/imports/chat-history/claude").glob("*.md")).read_text()
        self.assertIn("Start with...", claude)
        index = (self.tmp / "my/imports/chat-history-INDEX.md").read_text()
        self.assertIn("Hen welfare letter", index)
        self.assertIn("AI welfare reading list", index)
        self.assertNotIn("nothing said", index)            # chats with nothing from the person are skipped
        self.assertLess(index.index("AI welfare reading list"), index.index("Hen welfare letter"))  # newest first

    def test_gemini_takeout_and_rerun_keeps_index(self):
        tk = self.tmp / "takeout"
        (tk / "Takeout/My Activity/Gemini Apps").mkdir(parents=True)
        (tk / "Takeout/My Activity/Gemini Apps/MyActivity.json").write_text(json.dumps(GEMINI))
        self.assertEqual(self.run_it(tk).returncode, 0)
        f = next((self.tmp / "my/imports/chat-history/gemini").glob("2026-05-01-*.md")).read_text()
        self.assertIn("Summarise this fish welfare report", f)
        self.assertIn("The report finds stunning helps.", f)
        self.assertIn("And the EU angle?", f)
        cz = self.tmp / "claude.zip"
        with zipfile.ZipFile(cz, "w") as z:
            z.writestr("conversations.json", json.dumps(CLAUDE))
        self.run_it(cz)
        self.run_it(cz)                                     # running again doesn't duplicate rows
        index = (self.tmp / "my/imports/chat-history-INDEX.md").read_text()
        self.assertIn("Gemini chats on 2026-05-01", index)
        self.assertEqual(index.count("AI welfare reading list"), 1)

    def test_nothing_found(self):
        (self.tmp / "empty").mkdir()
        r = self.run_it(self.tmp / "empty")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("No ChatGPT, Claude or Gemini chats found", r.stderr)


if __name__ == "__main__":
    unittest.main()
