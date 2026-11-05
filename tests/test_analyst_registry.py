"""Registry test: the analyst subagent's definition is exactly as designed.

Checks the tool list is exactly Read, Grep, Glob, Write, Bash, that no
frontmatter field widens it, and that the fail-closed guard is wired to
every tool call. Standard library only (no YAML parser needed).

    python3 tests/test_analyst_registry.py -v
"""

import os
import re
import stat
import unittest
from pathlib import Path

if os.name == "nt":   # they use Linux and macOS file features (O_NOFOLLOW, fcntl); so does hardened mode
    raise unittest.SkipTest("hardened mode's scripts need Linux, macOS or WSL2, not native Windows")

REPO = Path(__file__).resolve().parents[1]
AGENT = REPO / ".claude" / "agents" / "analyst.md"
GUARD = REPO / ".claude" / "hooks" / "analyst_guard.py"

EXPECTED_TOOLS = {"Read", "Grep", "Glob", "Write", "Bash"}
# skills: preloads a skill's instructions (fetch-sources); it grants no tool or access.
ALLOWED_KEYS = {"name", "description", "tools", "model", "hooks", "skills"}


def frontmatter_lines():
    lines = AGENT.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---", "frontmatter must start on line 1"
    end = lines.index("---", 1)
    return lines[1:end]


def top_level(lines):
    out = {}
    for line in lines:
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


class TestAnalystRegistry(unittest.TestCase):
    def setUp(self):
        self.lines = frontmatter_lines()
        self.keys = top_level(self.lines)

    def test_name(self):
        self.assertEqual(self.keys.get("name"), "analyst")

    def test_tool_list_is_exact(self):
        tools = {t.strip() for t in self.keys["tools"].split(",")}
        self.assertEqual(tools, EXPECTED_TOOLS)

    def test_no_fields_that_widen_access(self):
        # e.g. permissionMode, mcpServers, skills, memory, isolation,
        # disallowedTools would all change what the analyst can do.
        self.assertEqual(set(self.keys) - ALLOWED_KEYS, set())

    def test_guard_wired_to_every_tool(self):
        text = "\n".join(self.lines)
        self.assertEqual(self.keys.get("hooks"), "")
        self.assertIn("  PreToolUse:", text)
        self.assertRegex(text, r'matcher:\s*"\*"')
        self.assertIn("type: command", text)
        self.assertIn(".claude/hooks/analyst_guard.py", text)

    def test_guard_exists_and_is_readable(self):
        self.assertTrue(GUARD.is_file())
        self.assertTrue(GUARD.stat().st_mode & stat.S_IRUSR)


if __name__ == "__main__":
    unittest.main()
