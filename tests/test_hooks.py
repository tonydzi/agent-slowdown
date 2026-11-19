"""Fake homes with fake hook configs. Nothing here runs a hook."""
import json
import tempfile
import unittest
from pathlib import Path

from agent_slowdown import cli, hooks


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def nested(event_to_groups):
    return {"hooks": {e: [{"matcher": m, "hooks": [{"type": "command", "command": c} for c in cmds]}
                          for m, cmds in groups] for e, groups in event_to_groups.items()}}


class Counting(unittest.TestCase):
    def test_processes_counts_wrappers_and_segments(self):
        self.assertEqual(hooks.processes("python3 guard.py"), 1)
        self.assertEqual(hooks.processes('bash -c "python3 guard.py"'), 2)
        self.assertEqual(hooks.processes('cmd /c "bash -c \\"python x.py\\""'), 3)
        self.assertEqual(hooks.processes("python3 a.py && python3 b.py"), 2)
        self.assertEqual(hooks.processes('bash -c "pgrep -f python3"'), 2)
        self.assertEqual(hooks.processes("cat x | jq ."), 2)
        self.assertEqual(hooks.processes(""), 0)

    def test_interpreter(self):
        self.assertEqual(hooks.interpreter("/usr/bin/python3 x.py"), "python")
        self.assertEqual(hooks.interpreter("node x.js"), "node")
        self.assertEqual(hooks.interpreter('bash -c "x"'), "bash")
        self.assertEqual(hooks.interpreter("./guard.sh"), "bash")
        self.assertIsNone(hooks.interpreter("mybinary --flag"))

    def test_matcher(self):
        self.assertTrue(hooks.matches_bash(""))
        self.assertTrue(hooks.matches_bash("Bash|Edit"))
        self.assertFalse(hooks.matches_bash("Write|Edit"))


class Audit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.cwd = Path(self.tmp.name) / "proj"
        self.cwd.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_audit(self):
        return hooks.audit(self.home, self.cwd, measure=False)

    def test_before_cleanup_shape_nineteen_wrapped_guards(self):
        # The shape that made a 13-session machine crawl: 19 guards per Bash call,
        # each one a shell that starts Python.
        guards = ['bash -c "python3 ~/.claude/hooks/g%d.py"' % i for i in range(19)]
        write(self.home / ".claude/settings.json", nested({"PreToolUse": [("Bash", guards)]}))
        result = self.run_audit()
        cell = result["summary"]["claude"]["tool_call"]
        self.assertEqual(cell["hooks"], 19)
        self.assertEqual(cell["processes"], 38)
        self.assertTrue(all("a shell only to start another program" in h["findings"][0] for h in result["hooks"]))

    def test_matcher_filters_tool_calls(self):
        write(self.home / ".claude/settings.json", nested({
            "PreToolUse": [("Write|Edit", ["python3 w.py"]), ("Bash", ["python3 b.py"])],
            "UserPromptSubmit": [("", ["python3 p.py"])],
            "SessionStart": [("", ["python3 s1.py", "python3 s2.py"])],
            "Stop": [("", ["python3 stop.py"])]}))
        row = self.run_audit()["summary"]["claude"]
        self.assertEqual(row["tool_call"]["hooks"], 1)
        self.assertEqual(row["prompt"]["hooks"], 1)
        self.assertEqual(row["session"]["hooks"], 2)
        self.assertEqual(row["other_events"], 1)

    def test_no_matcher_npx_and_duplicates(self):
        write(self.home / ".claude/settings.json", nested({"PostToolUse": [("", ["npx -y fmt-hook"])]}))
        write(self.cwd / ".claude/settings.json", nested({"PostToolUse": [("", ["npx -y fmt-hook"])]}))
        result = self.run_audit()
        found = result["hooks"][0]["findings"]
        self.assertTrue(any("npx" in f for f in found))
        self.assertTrue(any("no matcher" in f for f in found))
        self.assertEqual(result["duplicates"][0]["times"], 2)

    def test_script_that_spawns_and_big_entry_file(self):
        script = self.home / ".claude/hooks/dispatch.py"
        write(script, "import subprocess\nsubprocess.run(['python3', 'child.py'])\n" + "x = 1\n" * 500)
        write(self.home / ".claude/settings.json", nested({"PreToolUse": [("Bash", ["python3 %s" % script])]}))
        found = self.run_audit()["hooks"][0]["findings"]
        self.assertTrue(any("start more processes" in f for f in found))
        self.assertTrue(any("compiled again on every run" in f for f in found))

    def test_small_quiet_script_is_clean(self):
        script = self.home / ".claude/hooks/ok.py"
        write(script, "import sys, json\njson.load(sys.stdin)\n")
        write(self.home / ".claude/settings.json", nested({"PreToolUse": [("Bash", ["python3 %s" % script])]}))
        self.assertEqual(self.run_audit()["hooks"][0]["findings"], [])

    def test_cursor_flat_format_counts_as_tool_call(self):
        write(self.home / ".cursor/hooks.json", {"version": 1, "hooks": {
            "beforeShellExecution": [{"command": "./audit.sh"}, {"command": "./audit2.sh"}]}})
        cell = self.run_audit()["summary"]["cursor"]["tool_call"]
        self.assertEqual(cell["hooks"], 2)

    def test_codex_and_gemini_are_read(self):
        write(self.home / ".codex/hooks.json", nested({"PreToolUse": [("Bash", ["python3 c.py"])]}))
        write(self.home / ".gemini/settings.json", nested({"BeforeTool": [("run_shell_command", ["node g.js"])]}))
        summary = self.run_audit()["summary"]
        self.assertEqual(summary["codex"]["tool_call"]["hooks"], 1)
        self.assertEqual(summary["gemini-cli"]["tool_call"]["hooks"], 1)

    def test_only_enabled_plugins_count(self):
        catalog = self.home / ".claude/plugins/marketplaces/m/plugins/{}/hooks/hooks.json"
        write(Path(str(catalog).format("on")), nested({"PreToolUse": [("", ["python3 on.py"])]}))
        write(Path(str(catalog).format("off")), nested({"PreToolUse": [("", ["python3 off.py"])]}))
        write(self.home / ".claude/settings.json", {"enabledPlugins": {"on@m": True, "off@m": False}})
        commands = [h["command"] for h in self.run_audit()["hooks"]]
        self.assertEqual(commands, ["python3 on.py"])

    def test_broken_file_is_reported_not_skipped(self):
        write(self.home / ".codex/hooks.json", "{broken")
        result = self.run_audit()
        self.assertEqual(result["hooks"], [])
        self.assertEqual(len(result["unreadable"]), 1)

    def test_timing_never_runs_a_hook(self):
        # Hooks written as a bare script path, including one named like an interpreter.
        marker = Path(self.tmp.name) / "ran"
        for name in ("guard.sh", "python_guard.py", "bash"):
            script = self.home / "hooks" / name
            write(script, "#!/bin/sh\ntouch '%s'\n" % marker)
            script.chmod(0o755)
        commands = [str(self.home / "hooks" / n) for n in ("guard.sh", "python_guard.py")]
        commands.append(str(self.home / "hooks" / "bash") + " -c true")  # a hook program named like a shell
        write(self.home / ".claude/settings.json", nested({"PreToolUse": [("Bash", commands)]}))
        hooks.audit(self.home, self.cwd, measure=True)
        self.assertFalse(marker.exists(), "timing started a user's hook")

    def test_fake_interpreter_in_project_on_path_is_not_run(self):
        import os
        from unittest import mock
        marker = Path(self.tmp.name) / "fake-ran"
        fake = self.cwd / "bin" / "bash"
        write(fake, "#!/bin/sh\ntouch '%s'\n" % marker)
        fake.chmod(0o755)
        path = str(fake.parent) + os.pathsep + os.environ.get("PATH", "")
        with mock.patch.dict(os.environ, {"PATH": path}):
            hooks.startup_ms({"bash"}, self.cwd, runs=1)
        self.assertFalse(marker.exists(), "a project-local fake interpreter was run")

    def test_probe_ignores_startup_env(self):
        import os
        from unittest import mock
        marker = Path(self.tmp.name) / "env-ran"
        rc = Path(self.tmp.name) / "rc.sh"
        write(rc, "touch '%s'\n" % marker)
        with mock.patch.dict(os.environ, {"BASH_ENV": str(rc)}):
            hooks.startup_ms({"bash"}, self.cwd, runs=1)
        self.assertFalse(marker.exists(), "BASH_ENV ran during the probe")

    def test_wrapper_flag_only_when_payload_starts_an_interpreter(self):
        write(self.home / ".claude/settings.json", nested({"PreToolUse": [("Bash", [
            'bash -c "pgrep -f python3"', 'bash -c "python3 g.py"'])]}))
        found = [h["findings"] for h in self.run_audit()["hooks"]]
        self.assertEqual(found[0], [])
        self.assertTrue(found[1])

    def test_project_false_overrides_user_true_and_cache_beats_catalog(self):
        cache = self.home / ".claude/plugins/cache/m/p/1.0/hooks/hooks.json"
        catalog = self.home / ".claude/plugins/marketplaces/m/plugins/p/hooks/hooks.json"
        write(cache, nested({"PreToolUse": [("", ["python3 installed.py"])]}))
        write(catalog, nested({"PreToolUse": [("", ["python3 catalog.py"])]}))
        import os
        os.utime(cache, (1000, 1000))  # the catalog is newer, the installed copy must still win
        write(self.home / ".claude/settings.json", {"enabledPlugins": {"p@m": True}})
        self.assertEqual([h["command"] for h in self.run_audit()["hooks"]], ["python3 installed.py"])
        write(self.cwd / ".claude/settings.json", {"enabledPlugins": {"p@m": False}})
        self.assertEqual(self.run_audit()["hooks"], [])

    def test_quoted_separators_and_windows_paths(self):
        self.assertEqual(hooks.processes('python3 -c "print(\';\')"'), 1)
        self.assertIsNone(hooks.script_of("$CLAUDE_PROJECT_DIR/h.sh", self.home, Path("C:\\Users\\x")))
        self.assertIsNone(hooks.interpreter("/abs/python_guard_tool"))

    def test_cli_exit_codes(self):
        import os
        from unittest import mock
        write(self.home / ".claude/settings.json", nested({"PreToolUse": [("", ['bash -c "python3 x.py"'])]}))
        with mock.patch.object(hooks.Path, "home", return_value=self.home), \
                mock.patch.object(hooks.Path, "cwd", return_value=self.cwd), \
                mock.patch("sys.stdout", new_callable=lambda: open(os.devnull, "w")):
            self.assertEqual(cli.main(["hooks", "--no-time"]), 1)


if __name__ == "__main__":
    unittest.main()
