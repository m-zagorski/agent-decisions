"""Tests for the agent-decisions hook scripts. Standard library only: python3 -m unittest discover tests"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILTER = ROOT / "claude" / "hooks" / "filter_transcript.py"
LOOKUP = ROOT / "claude" / "hooks" / "decisions.py"


def entry(kind, content, **extra):
    return {"type": kind, "message": {"role": kind, "content": content}, **extra}


def tool_use(name, **inp):
    return {"type": "tool_use", "id": name, "name": name, "input": inp}


def write_jsonl(path, entries, trailing_garbage=True):
    lines = [json.dumps(e) for e in entries]
    if trailing_garbage:
        lines.append("half-written line {")
    path.write_text("\n".join(lines) + "\n")


class FilterTranscriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / "state"

    def run_filter(self, entries, session="S1"):
        t = self.tmp / f"{session}.jsonl"
        write_jsonl(t, entries)
        out = self.tmp / f"{session}.md"
        code = subprocess.run([sys.executable, str(FILTER), str(t), session, str(self.state), str(out)]).returncode
        return code, (out.read_text() if out.exists() else "")

    def test_keeps_conversation_edits_errors_and_drops_noise(self):
        code, out = self.run_filter([
            entry("user", "Use our own redirect, Amplify loses the session."),
            entry("assistant", [{"type": "thinking", "thinking": "SECRET THOUGHTS"},
                                {"type": "text", "text": "Building the redirect."},
                                tool_use("Read", file_path="/p/Auth.kt")]),
            entry("user", [{"type": "tool_result", "tool_use_id": "Read", "content": "BIG FILE BODY"}]),
            entry("assistant", [tool_use("Edit", file_path="/p/Auth.kt", old_string="signOut(intent)", new_string="signOut(redirect())")]),
            entry("user", [{"type": "tool_result", "tool_use_id": "x", "is_error": True, "content": "prompt=login rejected"}]),
            entry("user", "system caveat", isMeta=True),
        ])
        self.assertEqual(code, 0)
        self.assertIn("[you] Use our own redirect", out)
        self.assertIn("[claude] Building the redirect.", out)
        self.assertIn("signOut(redirect())", out)
        self.assertIn("[error] prompt=login rejected", out)
        for dropped in ("SECRET THOUGHTS", "BIG FILE BODY", "system caveat"):
            self.assertNotIn(dropped, out)

    def test_session_without_edits_is_skipped(self):
        code, _ = self.run_filter([entry("user", "What does this class do?"), entry("assistant", "It caches tokens.")])
        self.assertEqual(code, 3)

    def test_manual_capture_counts_as_captured(self):
        code, out = self.run_filter([
            entry("user", "first task"),
            entry("assistant", [tool_use("Edit", file_path="/p/A.kt", old_string="a", new_string="OLD_WORK")]),
            entry("user", "<command-name>/capture-decisions</command-name>"),
            entry("assistant", [tool_use("Write", file_path="/p/.context/decisions/0001-x.md", content="record")]),
            entry("user", "second task"),
            entry("assistant", [tool_use("Edit", file_path="/p/B.kt", old_string="b", new_string="NEW_WORK")]),
        ])
        self.assertEqual(code, 0)
        self.assertIn("NEW_WORK", out)
        self.assertNotIn("OLD_WORK", out)
        self.assertNotIn(".context/decisions", out)

    def test_only_context_edits_after_manual_capture_is_skipped(self):
        code, _ = self.run_filter([
            entry("assistant", [tool_use("Edit", file_path="/p/A.kt", old_string="a", new_string="b")]),
            entry("user", "/capture-decisions"),
            entry("assistant", [tool_use("Write", file_path="/p/.context/decisions/0001-x.md", content="r")]),
        ])
        self.assertEqual(code, 3)

    def test_incremental_after_success(self):
        base = [entry("assistant", [tool_use("Edit", file_path="/p/A.kt", old_string="a", new_string="FIRST")])]
        code, _ = self.run_filter(base)
        self.assertEqual(code, 0)
        (self.state / "S1.pending").rename(self.state / "S1.done")  # what capture.sh does on success
        code, _ = self.run_filter(base)
        self.assertEqual(code, 3, "nothing new since the last capture")
        code, out = self.run_filter(base + [entry("assistant", [tool_use("Edit", file_path="/p/B.kt", old_string="b", new_string="SECOND")])])
        self.assertEqual(code, 0)
        self.assertIn("SECOND", out)
        self.assertNotIn("FIRST", out)


def record(directory, rid, status, anchor, title, decision):
    (directory / f"{rid}-x.md").write_text(
        f'---\nid: "{rid}"\nstatus: {status}\nanchors:\n  - symbol: {anchor}\n---\n# {title}\n\n**Decision:** {decision}\n\n**Why:** because.\n')


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp())
        self.decisions = self.project / ".context" / "decisions"
        self.decisions.mkdir(parents=True)
        record(self.decisions, "0014", "active", "app/a/TokenManager.kt#TokenManager.fetch()", "Drop stale tokens", "Generation counter.")
        record(self.decisions, "0015", "draft", "app/a/TokenManager.kt", "Sign-outs go through SignOut", "Single entry point, 30 s timeout.")
        record(self.decisions, "0016", "needs-reason", "app/a/**", "Keep CUSTOM_AUTH", "Sign-in uses CUSTOM_AUTH.")
        record(self.decisions, "0003", "superseded", "app/a/TokenManager.kt", "Old polling rule", "Poll every 10s.")

    def run_lookup(self, *args, stdin=None):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.project)}
        return subprocess.run([sys.executable, str(LOOKUP), *args], input=stdin, capture_output=True, text=True, env=env).stdout

    def hook(self, path, session="S1"):
        out = self.run_lookup("context", stdin=json.dumps({"session_id": session, "hook_event_name": "PostToolUse",
                                                           "tool_input": {"file_path": str(self.project / path)}}))
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""

    def test_injects_exact_and_glob_anchors_but_not_superseded(self):
        ctx = self.hook("app/a/TokenManager.kt")
        self.assertIn("0014", ctx)
        self.assertIn("0015", ctx)
        self.assertIn("0016", ctx)
        self.assertNotIn("0003", ctx)
        self.assertLess(ctx.index("0014"), ctx.index("0015"), "active records come first")

    def test_each_record_once_per_session(self):
        self.assertTrue(self.hook("app/a/TokenManager.kt", "S1"))
        self.assertEqual(self.hook("app/a/TokenManager.kt", "S1"), "")
        self.assertTrue(self.hook("app/a/TokenManager.kt", "S2"), "a new session sees them again")

    def test_unrelated_and_context_files_get_nothing(self):
        self.assertEqual(self.hook("README.md"), "")
        self.assertEqual(self.hook(".context/decisions/0014-x.md"), "")

    def test_index_rebuilds_when_a_record_changes(self):
        self.hook("app/a/TokenManager.kt", "S1")
        time.sleep(0.05)
        record(self.decisions, "0020", "active", "app/b/New.kt", "New rule", "Something.")
        self.assertIn("0020", self.hook("app/b/New.kt", "S3"))

    def test_show_and_search(self):
        self.assertIn("0016", self.run_lookup("show", "app/a/Other.kt"))
        found = self.run_lookup("search", "sign-outs", "timeout")
        self.assertIn("0015", found)
        self.assertNotIn("0003", found)


OLD_LOOKUP = 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/ctx_lookup.py context'


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp())
        self.claude = self.project / ".claude"

    def install(self, *opts):
        subprocess.run(["bash", str(ROOT / "install.sh"), str(self.project), *opts], check=True, capture_output=True)

    def settings(self, name="settings.local.json"):
        return json.loads((self.claude / name).read_text())

    def commands(self, settings):
        return [h["command"] for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]

    def test_reinstall_does_not_duplicate(self):
        (self.project / "AGENTS.md").write_text("# Project rules\n")
        self.install("--with-ci")
        self.install()
        s = self.settings()
        self.assertEqual(len(self.commands(s)), 4)
        self.assertEqual(len(s["permissions"]["allow"]), 2)
        agents = (self.project / "AGENTS.md").read_text()
        self.assertTrue(agents.startswith("# Project rules"))
        self.assertEqual(agents.count("agent-decisions:start"), 1)
        self.assertTrue((self.project / ".github/workflows/decisions-check.yml").exists())
        self.assertEqual((self.project / ".gitignore").read_text().count(".context/.state/"), 1)

    def test_migrates_a_ctx_install(self):
        (self.claude / "hooks").mkdir(parents=True)
        (self.claude / "hooks" / "ctx_lookup.py").write_text("# old\n")
        old = {"permissions": {"allow": ["Bash(npm test)", "Bash(python3 .claude/hooks/ctx_lookup.py show:*)"]},
               "hooks": {"PostToolUse": [{"matcher": "Read|Edit|MultiEdit|Write",
                                          "hooks": [{"type": "command", "command": OLD_LOOKUP}]}],
                         "Stop": [{"hooks": [{"type": "command", "command": "./my-hook.sh"}]}]}}
        (self.claude / "settings.json").write_text(json.dumps(old))  # an old --shared install
        (self.project / "AGENTS.md").write_text("# Rules\n\n<!-- ctx:start -->\nold block\n<!-- ctx:end -->\n\nMore rules\n")
        self.install()

        self.assertFalse((self.claude / "hooks" / "ctx_lookup.py").exists())
        self.assertTrue((self.claude / "hooks" / "decisions.py").exists())
        shared = self.settings("settings.json")
        self.assertEqual(shared["permissions"]["allow"], ["Bash(npm test)"])
        self.assertEqual(self.commands(shared), ["./my-hook.sh"], "user hooks are kept")
        self.assertEqual(len([c for c in self.commands(self.settings()) if "decisions.py" in c]), 1)
        agents = (self.project / "AGENTS.md").read_text()
        self.assertNotIn("old block", agents)
        self.assertNotIn("ctx:start", agents)
        self.assertEqual(agents.count("agent-decisions:start"), 1)
        self.assertTrue(agents.rstrip().endswith("More rules"))


if __name__ == "__main__":
    unittest.main()
