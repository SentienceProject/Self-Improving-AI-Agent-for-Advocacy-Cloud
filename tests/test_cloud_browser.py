"""scripts/cloud_browser.py: Playwright's MCP server in cloud sessions; an MCP server with no tools anywhere else."""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
SCRIPT = HERE / "scripts" / "cloud_browser.py"
spec = importlib.util.spec_from_file_location("cloud_browser", SCRIPT)
cb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cb)


class Stub(unittest.TestCase):
    def talk(self, *msgs):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"}
        r = subprocess.run([sys.executable, str(SCRIPT)], input="".join(json.dumps(m) + "\n" for m in msgs),
                           capture_output=True, text=True, env=env, timeout=30)
        return [json.loads(line) for line in r.stdout.splitlines()]

    def test_no_tools_outside_the_cloud(self):
        out = self.talk({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "x"}})
        self.assertEqual([m["id"] for m in out], [1, 2, 3])   # notifications get no answer
        self.assertEqual(out[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(out[1]["result"]["tools"], [])
        self.assertIn("error", out[2])

    def test_cloud_command_is_pinned_and_headless(self):
        with mock.patch.object(cb.shutil, "which", return_value="/usr/bin/npx"):
            cmd = cb.cloud_command()
        self.assertIn(f"@playwright/mcp@{cb.VERSION}", cmd)
        self.assertNotIn("latest", " ".join(cmd))
        for flag in ("--headless", "--isolated", "--no-sandbox"):
            self.assertIn(flag, cmd)

    def test_mcp_json_and_settings_agree(self):
        servers = json.loads((HERE / ".mcp.json").read_text())["mcpServers"]
        settings = json.loads((HERE / ".claude" / "settings.json").read_text())
        self.assertEqual(settings["enabledMcpjsonServers"], list(servers))
        self.assertTrue(servers["cloud-browser"]["args"][0].endswith("scripts/cloud_browser.py"))


if __name__ == "__main__":
    unittest.main()
