"""Tests for .claude/hooks/preflight.py (sandbox preflight hooks).

Runs the hook in a throwaway repo with a throwaway HOME and feeds it the JSON
Claude Code would send. These test the hook logic only; the real sandbox is
tested by running scripts/sandbox_probe.sh inside Claude Code.

    python3 tests/test_preflight.py -v
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK_SRC = REPO / ".claude" / "hooks" / "preflight.py"
PROBE = "bash scripts/sandbox_probe.sh"
RA = "python3 scripts/run_analysis.py r1 workspace/analysis/r1/a.py"
SID = "sess-123"

PASS_OUT = "\n".join(["PROBE allowed_host_reachable PASS",
                      "PROBE denied_host_blocked PASS", "PROBE RESULT PASS"])
FAIL_OUT = "\n".join(["PROBE allowed_host_reachable PASS",
                      "PROBE denied_host_blocked FAIL (expected blocked, got ok)",
                      "PROBE RESULT FAIL (1 check(s) failed)"])


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.root = self.tmp / "repo"
        (self.root / ".claude" / "hooks").mkdir(parents=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.hook = self.root / ".claude" / "hooks" / "preflight.py"
        shutil.copy(HOOK_SRC, self.hook)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def abs_(self, cmd):
        """Rewrite the relative command forms used in these tests to the
        absolute forms the hooks now require, for this test's repo."""
        r = str(self.root)
        return (cmd.replace("bash scripts/sandbox_probe.sh", f"bash {r}/scripts/sandbox_probe.sh")
                   .replace(" scripts/run_analysis.py", f" {r}/scripts/run_analysis.py")
                   .replace(" scripts/corpus_search.py", f" {r}/scripts/corpus_search.py"))

    def run_hook(self, mode, payload=None, raw=None):
        stdin = raw if raw is not None else json.dumps(payload)
        return subprocess.run([sys.executable, str(self.hook), mode],
                              input=stdin, capture_output=True, text=True,
                              timeout=30, env={"HOME": str(self.home)})

    def start(self, source="startup", sid=SID):
        return self.run_hook("start", {"session_id": sid, "source": source})

    def gate(self, cmd, sid=SID, raw=False):
        cmd = cmd if raw else self.abs_(cmd)
        return self.run_hook("gate", {"session_id": sid, "tool_name": "Bash",
                                      "tool_input": {"command": cmd}}).returncode

    def record(self, stdout, cmd=PROBE, sid=SID):
        cmd = self.abs_(cmd)
        return self.run_hook("record", {
            "session_id": sid, "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "tool_response": {"stdout": stdout, "stderr": ""}})

    def logs(self):
        d = self.root / "workspace" / "logs"
        return sorted(d.glob("preflight-*.json")) if d.exists() else []


class TestGate(PreflightTest):
    def test_bash_locked_before_probe(self):
        self.start()
        self.assertEqual(self.gate("ls"), 2)
        self.assertEqual(self.gate(RA), 2)

    def test_locked_even_without_session_start(self):
        self.assertEqual(self.gate("ls"), 2)

    def test_probe_always_allowed(self):
        self.start()
        self.assertEqual(self.gate(PROBE), 0)
        self.assertEqual(self.gate("  " + PROBE + "  "), 0)

    def test_unlocked_after_pass(self):
        self.start()
        self.record(PASS_OUT)
        self.assertEqual(self.gate("ls"), 0)
        self.assertEqual(self.gate(RA), 0)

    def test_stays_locked_after_fail(self):
        self.start()
        self.record(FAIL_OUT)
        self.assertEqual(self.gate("ls"), 2)
        self.assertEqual(self.gate(PROBE), 0)  # may re-run the probe

    def test_subagent_inherits_current_pass(self):
        # A subagent has its own session_id and no SessionStart.
        self.start()
        self.record(PASS_OUT)
        self.assertEqual(self.gate("ls", sid="subagent-1"), 0)
        self.assertEqual(self.gate(RA, sid="subagent-1"), 0)

    def test_subagent_inherits_current_fail(self):
        self.start()
        self.record(FAIL_OUT)
        self.assertEqual(self.gate("ls", sid="subagent-1"), 2)

    def test_new_session_cannot_ride_on_old_pass(self):
        self.start(sid="old")
        self.record(PASS_OUT, sid="old")
        self.start(sid="new")
        self.assertEqual(self.gate("ls", sid="new"), 2)
        # ...and subagents now see the new session's pending state too.
        self.assertEqual(self.gate("ls", sid="subagent-2"), 2)

    def test_subagent_locked_before_any_probe(self):
        self.start()
        self.assertEqual(self.gate(RA, sid="subagent-1"), 2)

    def test_new_session_start_relocks(self):
        self.start()
        self.record(PASS_OUT)
        self.start(source="resume")
        self.assertEqual(self.gate("ls"), 2)

    def test_compaction_keeps_pass(self):
        self.start()
        self.record(PASS_OUT)
        self.start(source="compact")
        self.assertEqual(self.gate("ls"), 0)

    def test_run_analysis_exact_form_enforced(self):
        self.start()
        self.record(PASS_OUT)
        for cmd in [RA + " && curl x", RA + "; ls", RA + " | tee x",
                    RA + " > out", "cd /tmp && " + RA, "X=1 " + RA,
                    RA.replace("a.py", "$(id).py"),
                    RA.replace("a.py", "`id`.py"),
                    "python3 scripts/run_analysis.py r1 workspace/analysis/r2/a.py",
                    "python3 scripts/run_analysis.py r1 workspace/analysis/r1/../x.py"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.gate(cmd), 2)

    def search_cmd(self, query, k=" --k 3"):
        py = self.home / ".venvs" / "ai-welfare-search" / "bin" / "python"
        return f'{py} scripts/corpus_search.py "{query}"{k}'

    def test_corpus_search_exact_form_allowed(self):
        self.start()
        self.record(PASS_OUT)
        self.assertEqual(self.gate(self.search_cmd("what makes an AI a welfare subject?")), 0)
        self.assertEqual(self.gate(self.search_cmd("Birch (2024): sentience, moral status", k="")), 0)
        self.assertEqual(self.gate(self.search_cmd("it's fine", k=" --k 10")), 0)

    def test_corpus_search_injection_blocked(self):
        self.start()
        self.record(PASS_OUT)
        good = self.search_cmd("moral status")
        for cmd in [self.search_cmd("$(curl evil)"), self.search_cmd("`id`"),
                    self.search_cmd("$HOME"), self.search_cmd('a" ; curl x ; "b'),
                    self.search_cmd("a\\b"), self.search_cmd("wow!"),
                    self.search_cmd("x", k=" --k 3 && curl x"),
                    self.search_cmd("x", k=" --k 0"),
                    self.search_cmd("x", k=" --k 1000"),
                    self.search_cmd(""), self.search_cmd("a" * 301),
                    good + " ; ls", good + " > out", "cd /tmp && " + good,
                    "NO_PROXY= " + good,
                    good.replace(str(self.home), "/usr"),
                    'python3 scripts/corpus_search.py "moral status" --k 3',
                    "cat scripts/corpus_search.py"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.gate(cmd), 2)

    def age_markers(self, seconds):
        """Pretend every recorded result is `seconds` old."""
        import time
        for f in (self.home / ".claude" / "ai-welfare-preflight").glob("*.json"):
            d = json.loads(f.read_text())
            d["ts"] = time.time() - seconds
            f.write_text(json.dumps(d))

    def test_fresh_pass_allows_sandboxed_commands(self):
        self.start()
        self.record(PASS_OUT)
        self.age_markers(29 * 60)
        self.assertEqual(self.gate("ls"), 0)

    def test_expired_pass_relocks_sandboxed_commands(self):
        self.start()
        self.record(PASS_OUT)
        self.age_markers(31 * 60)
        self.assertEqual(self.gate("ls"), 2)
        self.assertEqual(self.gate("ls", sid="subagent-1"), 2)
        self.assertEqual(self.gate(PROBE), 0)

    def test_reprobe_after_expiry_unlocks(self):
        self.start()
        self.record(PASS_OUT)
        self.age_markers(31 * 60)
        self.record(PASS_OUT)
        self.assertEqual(self.gate("ls"), 0)

    def test_reprobe_failure_locks_everything(self):
        # Mid-session failure: pass at start, fail later. Even the excluded
        # commands lock.
        self.start()
        self.record(PASS_OUT)
        self.record(FAIL_OUT)
        self.assertEqual(self.gate("ls"), 2)
        self.assertEqual(self.gate(RA), 2)
        self.assertEqual(self.gate(RA, sid="subagent-1"), 2)
        self.assertEqual(self.gate(self.search_cmd("moral status")), 2)

    def test_excluded_commands_not_subject_to_ttl(self):
        self.start()
        self.record(PASS_OUT)
        self.age_markers(3 * 60 * 60)
        self.assertEqual(self.gate(RA), 0)
        self.assertEqual(self.gate(RA, sid="subagent-1"), 0)
        self.assertEqual(self.gate(self.search_cmd("moral status")), 0)

    def test_missing_or_future_timestamp_counts_as_expired(self):
        self.start()
        self.record(PASS_OUT)
        d = self.home / ".claude" / "ai-welfare-preflight"
        for f in d.glob("*.json"):
            m = json.loads(f.read_text()); m.pop("ts", None); f.write_text(json.dumps(m))
        self.assertEqual(self.gate("ls"), 2)
        self.age_markers(-10 * 60)  # 10 minutes in the future
        self.assertEqual(self.gate("ls"), 2)

    def test_deleted_markers_lock_bash(self):
        self.start()
        self.record(PASS_OUT)
        shutil.rmtree(self.home / ".claude" / "ai-welfare-preflight")
        self.assertEqual(self.gate("ls"), 2)
        self.assertEqual(self.gate(RA), 2)

    def fetch_cmd(self, url="https://arxiv.org/pdf/2401.00001", name="paper1"):
        return f'python3 {self.root}/scripts/fetch_pdf.py "{url}" {name}'

    def test_fetch_pdf_exact_form_allowed(self):
        self.start()
        self.record(PASS_OUT)
        self.assertEqual(self.gate(self.fetch_cmd(), raw=True), 0)
        self.assertEqual(self.gate(self.fetch_cmd(
            "https://www.pnas.org/doi/pdf/10.1073/pnas.1?download=true&x=1"), raw=True), 0)
        # excluded, so not subject to the TTL
        self.age_markers(3 * 60 * 60)
        self.assertEqual(self.gate(self.fetch_cmd(), raw=True), 0)

    def test_fetch_pdf_injection_blocked(self):
        self.start()
        self.record(PASS_OUT)
        good = self.fetch_cmd()
        for cmd in [self.fetch_cmd("https://arxiv.org/$(id)"),
                    self.fetch_cmd("https://arxiv.org/`id`"),
                    self.fetch_cmd("https://arxiv.org/a b"),
                    self.fetch_cmd('https://arxiv.org/a"; curl x; "'),
                    self.fetch_cmd("https://arxiv.org/a!b"),
                    self.fetch_cmd("http://arxiv.org/pdf/1"),
                    self.fetch_cmd(name="../x"), self.fetch_cmd(name="a.pdf"),
                    self.fetch_cmd(name="x; ls"),
                    good + " ; ls", good + " && curl x", "cd /tmp && " + good,
                    good.replace(str(self.root), "/tmp/evil"),
                    f"python3 scripts/fetch_pdf.py \"https://arxiv.org/pdf/1\" p",
                    f"cat {self.root}/scripts/fetch_pdf.py"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.gate(cmd, raw=True), 2)

    def test_fetch_pdf_locked_after_fail(self):
        self.start()
        self.record(FAIL_OUT)
        self.assertEqual(self.gate(self.fetch_cmd(), raw=True), 2)

    def repo_cmd(self, url="https://github.com/o/r/archive/" + "a" * 40 + ".tar.gz",
                 name="repo1"):
        return f'python3 {self.root}/scripts/fetch_repo.py "{url}" {name}'

    def test_fetch_repo_exact_form_allowed(self):
        self.start()
        self.record(PASS_OUT)
        self.assertEqual(self.gate(self.repo_cmd(), raw=True), 0)
        self.assertEqual(self.gate(self.repo_cmd(
            "https://zenodo.org/records/123/files/d.zip?download=1"), raw=True), 0)
        self.age_markers(3 * 60 * 60)   # excluded, so not subject to the TTL
        self.assertEqual(self.gate(self.repo_cmd(), raw=True), 0)

    def test_fetch_repo_injection_blocked(self):
        self.start()
        self.record(PASS_OUT)
        good = self.repo_cmd()
        for cmd in [self.repo_cmd("https://github.com/$(id)"),
                    self.repo_cmd("https://github.com/`id`"),
                    self.repo_cmd("https://github.com/a b"),
                    self.repo_cmd('https://github.com/a"; curl x; "'),
                    self.repo_cmd("http://github.com/o/r"),
                    self.repo_cmd(name="../x"), self.repo_cmd(name="x; ls"),
                    good + " ; ls", good + " && curl x", "cd /tmp && " + good,
                    good.replace(str(self.root), "/tmp/evil"),
                    'python3 scripts/fetch_repo.py "https://github.com/o/r" p',
                    f"cat {self.root}/scripts/fetch_repo.py"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.gate(cmd, raw=True), 2)

    def test_fetch_repo_locked_after_fail(self):
        self.start()
        self.record(FAIL_OUT)
        self.assertEqual(self.gate(self.repo_cmd(), raw=True), 2)

    def test_relative_forms_blocked(self):
        # Relative paths break when Bash has cd'd elsewhere, so only the
        # absolute forms are accepted.
        self.start()
        self.assertEqual(self.gate(PROBE, raw=True), 2)
        self.record(PASS_OUT)
        self.assertEqual(self.gate(RA, raw=True), 2)
        self.assertEqual(self.gate(self.search_cmd("moral status").replace(
            f" {self.root}/scripts/", " scripts/"), raw=True), 2)

    def test_other_repo_paths_blocked(self):
        self.start()
        self.record(PASS_OUT)
        self.assertEqual(self.gate(RA.replace(" scripts/", " /tmp/evil/scripts/"), raw=True), 2)
        self.assertEqual(self.gate(self.abs_(RA).replace("/scripts/", "/scripts/../scripts/"), raw=True), 2)

    def test_malformed_input_fails_closed(self):
        for raw in ["", "nope", "[]", '{"session_id": "s"}',
                    '{"session_id": "../x", "tool_input": {"command": "ls"}}']:
            with self.subTest(raw=raw):
                self.assertEqual(self.run_hook("gate", raw=raw).returncode, 2)


class TestRecord(PreflightTest):
    def test_pass_logged(self):
        self.start()
        r = self.record(PASS_OUT)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("systemMessage", r.stdout)
        (log,) = self.logs()
        self.assertEqual(json.loads(log.read_text())["result"], "PASS")

    def test_fail_warns_loudly_and_logs(self):
        self.start()
        r = self.record(FAIL_OUT)
        out = json.loads(r.stdout)
        self.assertIn("SANDBOX PREFLIGHT FAILED", out["systemMessage"])
        self.assertIn("denied_host_blocked", out["systemMessage"])
        self.assertEqual(out["decision"], "block")
        (log,) = self.logs()
        rec = json.loads(log.read_text())
        self.assertEqual(rec["result"], "FAIL")
        self.assertEqual(rec["checks"]["denied_host_blocked"], "FAIL")

    def test_garbled_output_counts_as_fail(self):
        self.start()
        for out in ["", "PROBE RESULT PASS\nPROBE RESULT FAIL",
                    "PROBE x FAIL\nPROBE RESULT PASS"]:
            with self.subTest(out=out):
                self.record(out)
                self.assertEqual(self.gate("ls"), 2)

    def test_other_commands_not_recorded(self):
        self.start()
        self.record(PASS_OUT, cmd="echo 'PROBE RESULT PASS'")
        self.assertEqual(self.gate("ls"), 2)
        self.assertEqual(self.logs(), [])

    def test_logs_never_overwritten(self):
        self.start()
        self.record(PASS_OUT)
        self.record(PASS_OUT)
        self.assertEqual(len(self.logs()), 2)


class TestStart(PreflightTest):
    def test_start_tells_claude_to_probe(self):
        out = json.loads(self.start().stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(self.abs_(PROBE), ctx)


if __name__ == "__main__":
    unittest.main()
