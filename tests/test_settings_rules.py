"""Settings registry test: the security-relevant rules are actually present.

A rule that silently disappears from the settings is the easiest way for a
guarantee to vanish, so this checks the settings files themselves: the hardened
mode's effective settings, i.e. .claude/settings.json with layers/hardened.json
merged in exactly as setup.py --hardened does. It checks presence, not
behaviour; the probe and the live canary tests check behaviour.

    python3 tests/test_settings_rules.py -v
"""

import copy
import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROJECT = REPO / ".claude" / "settings.json"
LOCAL = REPO / ".claude" / "settings.local.json"
USER = Path.home() / ".claude" / "settings.json"
SEARCH_PY = str(Path.home() / ".venvs" / "ai-welfare-search" / "bin" / "python")

REQUIRED_DENY = {
    # Reads no agent may make (Read rules also cover Grep and Glob).
    "Read(~/ai-welfare-evals/**)",
    "Read(~/.ssh/**)",
    "Read(~/.aws/**)",
    "Read(**/.env)",
    "Read(**/.env.*)",
    # Frozen files and evidence.
    "Edit(/scripts/**)",
    "Edit(/sandbox/**)",
    "Edit(/.claude/hooks/**)",
    "Edit(/.claude/agents/**)",
    "Edit(/.claude/settings.json)",
    "Edit(/.claude/settings.local.json)",
    "Edit(/workspace/index/**)",
    "Edit(/workspace/logs/preflight-*.json)",
    "Edit(/workspace/logs/fetch-*.json)",
    "Edit(~/.claude/ai-welfare-preflight/**)",
    # Only run_analysis.py may reach Docker.
    "Bash(docker *)",
}
EXCLUDED = {
    f"python3 {REPO.as_posix()}/scripts/run_analysis.py *",
    f"{Path(SEARCH_PY).as_posix()} {REPO.as_posix()}/scripts/corpus_search.py *",
    f"python3 {REPO.as_posix()}/scripts/fetch_pdf.py *",
    f"python3 {REPO.as_posix()}/scripts/fetch_repo.py *",
    f"python3 {REPO.as_posix()}/scripts/ask_model.py *",   # outside models: their logins live outside the sandbox
}

_spec = importlib.util.spec_from_file_location("setup_mod", REPO / "setup.py")
SETUP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SETUP)


def hardened():
    """The hardened mode's effective settings: the base with the hardened layer merged in."""
    eff = copy.deepcopy(load(PROJECT))
    SETUP.merge(eff, SETUP.fill(load(REPO / "layers" / "hardened.json")))
    return eff


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class TestSettingsRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = hardened()
        cls.local = load(LOCAL)
        cls.user = load(USER)
        cls.sandbox = cls.project.get("sandbox", {})
        cls.deny = set()
        for s in (cls.project, cls.local):
            cls.deny |= set(s.get("permissions", {}).get("deny", []))

    def test_required_deny_rules_present(self):
        self.assertEqual(REQUIRED_DENY - self.deny, set())

    def test_sandbox_fails_closed(self):
        self.assertIs(self.sandbox.get("enabled"), True)
        self.assertIs(self.sandbox.get("failIfUnavailable"), True)
        self.assertIs(self.sandbox.get("allowUnsandboxedCommands"), False)

    def test_only_the_known_exclusions(self):
        self.assertEqual(set(self.sandbox.get("excludedCommands", [])), EXCLUDED)

    def test_localhost_not_allowed(self):
        allowed = self.sandbox.get("network", {}).get("allowedDomains", [])
        self.assertFalse([d for d in allowed if "localhost" in d or d.startswith("127.")])

    def test_no_saved_webfetch_domains(self):
        # These silently widen the Bash sandbox's network allowlist.
        for s in (self.project, self.local, self.user):
            allow = s.get("permissions", {}).get("allow", [])
            self.assertFalse([r for r in allow if r.startswith("WebFetch(domain:")])

    @unittest.skipUnless(USER.exists() and SETUP.current_mode() == "hardened",
                         "only hardened mode asks for this in your user settings")
    def test_strict_allowlist_in_user_settings(self):
        net = self.user.get("sandbox", {}).get("network", {})
        self.assertIs(net.get("strictAllowlist"), True)

    def test_preflight_hooks_wired(self):
        text = json.dumps(self.project.get("hooks", {}))
        for mode in ("preflight.py\\\" start", "preflight.py\\\" gate",
                     "preflight.py\\\" record"):
            self.assertIn(mode, text)


if __name__ == "__main__":
    unittest.main()
