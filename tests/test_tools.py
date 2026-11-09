"""Fake snapshots and fake homes, so these behave the same on every OS."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_slowdown import apps, cli, exclusions, mcp, memlimit, orphans, procs


def proc(pid=100, ppid=1, rss_mb=50, cpu=0.0, age_s=3600, stat="S", exe="", cmd="node x"):
    return procs.Proc(pid, ppid, rss_mb, cpu, age_s, stat, exe, cmd)


class Parsing(unittest.TestCase):
    def test_etime(self):
        self.assertEqual(procs.parse_etime("01:02"), 62)
        self.assertEqual(procs.parse_etime("03:01:02"), 10862)
        self.assertEqual(procs.parse_etime("2-03:01:02"), 183662)
        self.assertEqual(procs.parse_etime("junk"), 0)

    def test_ps_keeps_spaces_in_path_and_command(self):
        main = "  10     1  2048  1,5 01:00 S  /Applications/My App.app/Contents/MacOS/My App --flag\n"
        comm = "10 /Applications/My App.app/Contents/MacOS/My App\n"
        rows = procs.parse_ps(main, comm)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].pid, rows[0].rss_mb, rows[0].cpu, rows[0].age_s), (10, 2.0, 1.5, 60))
        self.assertIn("--flag", rows[0].cmd)
        self.assertEqual(procs.label(rows[0]), "My App")

    def test_windows_single_object_and_date(self):
        row = {"ProcessId": 5, "ParentProcessId": 4, "WorkingSetSize": 1048576,
               "CreationDate": "/Date(1000000)/", "Name": "node.exe", "CommandLine": "node a.js"}
        rows = procs.parse_windows(json.dumps(row), now=2000.0)
        self.assertEqual((rows[0].pid, rows[0].age_s, rows[0].rss_mb), (5, 1000, 1.0))


class Labels(unittest.TestCase):
    def test_interpreter_is_named_by_what_it_runs(self):
        self.assertEqual(procs.label(proc(cmd="node /opt/foo-mcp/dist/index.js")), "node: foo-mcp")
        self.assertEqual(procs.label(proc(exe="/usr/bin/python3.12", cmd="/usr/bin/python3.12 /srv/bar/main.py")),
                         "python: bar")
        self.assertEqual(procs.label(proc(cmd="npx -y chrome-devtools-mcp@latest")), "npx: chrome-devtools-mcp")

    def test_electron_helpers_roll_up(self):
        helper = proc(exe="/Applications/Cursor.app/Contents/Frameworks/Cursor Helper (Renderer).app/Contents/MacOS/x")
        self.assertEqual(procs.label(helper), "Cursor")

    def test_group_sums_copies(self):
        rows = procs.group([proc(pid=1, rss_mb=100, cmd="node /a/foo-mcp/i.js"),
                            proc(pid=2, rss_mb=200, cmd="node /a/foo-mcp/i.js"),
                            proc(pid=3, rss_mb=10, cmd="vim")])
        self.assertEqual((rows[0]["copies"], rows[0]["rss_mb"]), (2, 300))

    def test_mcp_ignores_the_agents_own_flag(self):
        self.assertFalse(procs.is_mcp(proc(cmd="claude --mcp-config /tmp/a.json --print")))
        self.assertTrue(procs.is_mcp(proc(cmd="node /x/telegram-mcp/server.js")))
        self.assertTrue(procs.is_mcp(proc(cmd="npx @modelcontextprotocol/server-github")))
        self.assertFalse(procs.is_mcp(proc(cmd="node /x/mcpherson/app.js")))

    def test_mcp_in_an_argument_does_not_count(self):
        self.assertFalse(procs.is_mcp(proc(cmd="vim /tmp/foo-mcp-notes.md")))
        self.assertTrue(procs.is_mcp(proc(cmd="python3 -m foo_mcp --port 3000")))
        self.assertTrue(procs.is_mcp(proc(cmd="uv run some-mcp")))

    def test_sessions(self):
        rows = [proc(pid=1, cmd="/home/a/.local/bin/claude --resume"),
                proc(pid=2, cmd="node /usr/lib/node_modules/.bin/codex"),
                proc(pid=3, cmd="node /opt/not-an-agent.js")]
        self.assertEqual(procs.count_sessions(rows), 2)
        rows.append(proc(pid=4, exe="/Applications/Claude.app/Contents/MacOS/Claude", cmd="x"))
        self.assertEqual(procs.count_sessions(rows), 2)


class Mcp(unittest.TestCase):
    def home(self, files):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        return root

    def test_stdio_is_per_session_and_url_is_shared(self):
        root = self.home({".claude.json": json.dumps({"mcpServers": {
            "a": {"command": "npx", "args": ["-y", "alpha-mcp@1.2"]},
            "b": {"type": "sse", "url": "http://127.0.0.1:9/sse"}}})})
        result = mcp.audit(root, root, snapshot=[proc(pid=1, rss_mb=70, cmd="node /x/alpha-mcp/i.js"),
                                                 proc(pid=2, rss_mb=30, cmd="npm exec alpha-mcp")])
        self.assertEqual([s["name"] for s in result["per_session"]], ["a"])
        self.assertEqual((result["per_session"][0]["copies"], result["per_session"][0]["rss_mb"]), (2, 100))

    def test_port_number_is_not_a_fingerprint(self):
        self.assertEqual(mcp.fingerprint({"command": "npx", "args": ["foo-mcp", "--port", "3000"]}), "foo-mcp")

    def test_wrong_shape_and_unreadable_are_cannot_judge(self):
        root = self.home({".cursor/mcp.json": '{"mcpServers": []}'})
        self.assertEqual(len(mcp.collect(root)[1]), 1)
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("no")):
            self.assertEqual(len(mcp.collect(root)[1]), 1)

    def test_mini_toml_refuses_inline_servers(self):
        with self.assertRaises(ValueError):
            mcp._mini_toml('[mcp_servers]\nfoo = {command="uvx", args=["foo-mcp"]}\n')

    def test_vendor_runtime_is_not_blamed_on_you(self):
        text = '[mcp_servers.node_repl]\ncommand = "/Applications/ChatGPT.app/Contents/Resources/node_repl"\n'
        root = self.home({".codex/config.toml": text})
        self.assertEqual(mcp.collect(root)[0][0]["kind"], "vendor-runtime")

    def test_fingerprint_is_never_an_interpreter(self):
        self.assertIsNone(mcp.fingerprint({"command": "node", "args": []}))
        self.assertEqual(mcp.fingerprint({"command": "uvx", "args": ["some-server@latest"]}), "some-server")

    def test_codex_toml_on_any_python(self):
        text = '[mcp_servers.pg]\ncommand = "uvx"\nargs = ["pg-server"]\n[mcp_servers.off]\ncommand = "x-server"\nenabled = false\n'
        root = self.home({".codex/config.toml": text})
        kinds = {s["name"]: s["kind"] for s in mcp.collect(root)[0]}
        self.assertEqual(kinds, {"pg": "per-session", "off": "disabled"})
        self.assertEqual(mcp._mini_toml(text)["mcp_servers"]["pg"]["args"], ["pg-server"])

    def test_broken_file_is_cannot_judge_not_clean(self):
        root = self.home({".cursor/mcp.json": "{broken"})
        servers, unreadable = mcp.collect(root)
        self.assertEqual(servers, [])
        self.assertEqual(len(unreadable), 1)

    def test_mini_toml_reads_multiline_args_and_ignores_other_keys(self):
        text = '[mcp_servers.a]\ncommand = "uvx"\nenv_vars = ["A", "B"]\nargs = [\n "x-server",\n]\nenv = { A = "1" }\n'
        self.assertEqual(mcp._mini_toml(text)["mcp_servers"]["a"], {"command": "uvx", "args": ["x-server"]})

    def test_mini_toml_fails_loud_on_a_key_we_need(self):
        with self.assertRaises(ValueError):
            mcp._mini_toml('[mcp_servers.a]\ncommand = ["uvx", "x"]\n')

    def test_missing_process_list_is_not_zero(self):
        root = self.home({})
        with mock.patch.object(mcp.procs, "snapshot", return_value=None):
            self.assertIsNone(mcp.audit(root, root)["sessions"])


class Orphans(unittest.TestCase):
    def find(self, rows, **kw):
        kw.setdefault("managed", set())
        kw.setdefault("allow", [])
        kw.setdefault("service_check", lambda pid: False)
        return orphans.find(rows, **kw)

    def test_dead_parent_helper_is_found(self):
        found, _ = self.find([proc(pid=10, cmd="node /x/foo-mcp/index.js")])
        self.assertEqual([p.pid for p in found], [10])

    def test_live_parent_is_left_alone(self):
        rows = [proc(pid=9, ppid=2, cmd="claude"), proc(pid=10, ppid=9, cmd="node /x/foo-mcp/index.js")]
        self.assertEqual(self.find(rows)[0], [])

    def test_unknown_process_with_ppid_1_is_left_alone(self):
        self.assertEqual(self.find([proc(pid=10, cmd="/usr/bin/some-daemon --serve")])[0], [])

    def test_launchd_and_systemd_daemons_are_skipped(self):
        row = proc(pid=10, cmd="python /srv/shared-mcp/server.py")
        self.assertEqual(self.find([row], managed={10}), ([], 1))
        self.assertEqual(self.find([row], service_check=lambda pid: True), ([], 1))

    def test_young_and_allowlisted_are_skipped(self):
        self.assertEqual(self.find([proc(pid=10, age_s=60, cmd="rg foo /")])[0], [])
        self.assertEqual(self.find([proc(pid=10, cmd="node /x/my-mcp/i.js")], allow=["my-mcp"])[0], [])

    def test_systemd_user_parent_counts_as_dead(self):
        rows = [proc(pid=5, ppid=1, cmd="/lib/systemd/systemd --user"),
                proc(pid=10, ppid=5, cmd="node /x/foo-mcp/index.js")]
        self.assertEqual([p.pid for p in self.find(rows)[0]], [10])

    def test_word_inside_a_shell_line_is_not_a_helper(self):
        self.assertEqual(self.find([proc(pid=10, cmd="/bin/zsh -c make; rg foo; npx bar")])[0], [])

    def test_interpreter_argument_is_not_a_helper(self):
        self.assertEqual(self.find([proc(pid=10, cmd="python manage.py find")])[0], [])
        self.assertEqual(len(self.find([proc(pid=11, cmd="node /x/tsserver.js --stdio")])[0]), 1)

    def test_left_behind_agent_shell_is_found(self):
        row = proc(pid=10, cmd="/bin/zsh -c source /home/a/.claude/shell-snapshots/snapshot-zsh-1.sh && until test -f x; do sleep 15; done")
        self.assertEqual([p.pid for p in self.find([row])[0]], [10])

    def test_recycled_pid_is_not_signalled(self):
        with mock.patch.object(orphans.procs, "snapshot", return_value=[proc(pid=10, cmd="something else")]), \
                mock.patch.object(orphans.os, "kill") as kill:
            self.assertEqual(orphans.stop(proc(pid=10, cmd="rg foo /")), "recycled")
            kill.assert_not_called()

    def test_failed_service_view_refuses_to_judge(self):
        with mock.patch.object(orphans.procs, "IS_MAC", True), \
                mock.patch.object(orphans.subprocess, "run", side_effect=OSError("no launchctl")):
            with self.assertRaises(orphans.ViewError):
                orphans._launchd_pids()

    def test_unreadable_allowlist_refuses_to_judge(self):
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("no")):
            with self.assertRaises(orphans.ViewError):
                orphans.read_allow()

    def test_identity_is_rechecked_before_sigkill(self):
        seen = iter([True, False])
        with mock.patch.object(orphans, "_still_same", side_effect=lambda p: next(seen)), \
                mock.patch.object(orphans, "_alive", return_value=True), \
                mock.patch.object(orphans.os, "kill") as kill:
            self.assertEqual(orphans.stop(proc(pid=10), grace_s=0.05), "gone")
        self.assertEqual(kill.call_count, 1)

    def test_zombies(self):
        self.assertEqual(len(orphans.zombies([proc(stat="Z+"), proc(stat="S")])), 1)


@unittest.skipIf(os.name == "nt", "POSIX signals")
class RealStop(unittest.TestCase):
    """Real processes, because a fake snapshot cannot show that a stop really stops."""

    def spawn(self, code):
        child = subprocess.Popen([sys.executable, "-c", code])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        time.sleep(0.8)
        return child, [p for p in procs.snapshot() if p.pid == child.pid][0]

    def test_polite_process_exits_on_sigterm(self):
        child, row = self.spawn("import time; time.sleep(60)")
        with mock.patch.object(orphans, "_alive", side_effect=lambda pid: child.poll() is None):
            self.assertEqual(orphans.stop(row, grace_s=3), "gone")
        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)

    def test_process_ignoring_sigterm_is_killed(self):
        code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        child, row = self.spawn(code)
        with mock.patch.object(orphans, "_alive", side_effect=lambda pid: child.poll() is None):
            self.assertEqual(orphans.stop(row, grace_s=0.5), "killed")
        self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)


class Apps(unittest.TestCase):
    def test_sum_and_threshold(self):
        rows = [proc(pid=1, rss_mb=3000, age_s=7200, exe="/Applications/Cursor.app/Contents/MacOS/Cursor"),
                proc(pid=2, rss_mb=2000, exe="/Applications/Cursor.app/Contents/Frameworks/H.app/Contents/MacOS/H"),
                proc(pid=3, rss_mb=500, cmd="vim")]
        report = apps.report(rows, warn_gb=4)
        self.assertEqual(len(report), 1)
        self.assertEqual((report[0]["app"], report[0]["copies"], report[0]["restart"]), ("Cursor", 2, True))
        self.assertFalse(apps.report(rows, warn_gb=9)[0]["restart"])

    def test_windows_name_counts_and_cli_does_not(self):
        rows = [proc(pid=1, exe="cursor.exe", cmd="C:/x/cursor.exe"), proc(pid=2, cmd="/home/a/.local/bin/claude")]
        self.assertEqual([r["app"] for r in apps.report(rows)], ["Cursor"])


class MemLimit(unittest.TestCase):
    def test_login_session_silently_fails(self):
        state = memlimit.verdict("/user.slice/user-1000.slice/session-3.scope", False, "12g", True)
        self.assertEqual(state["state"], "silent-failure")
        self.assertIn("systemd-run --user --scope", state["text"])

    def test_owned_scope_works(self):
        self.assertEqual(memlimit.verdict("/a/app.slice/run-1.scope", True, "12g", True)["state"], "ok")

    def test_not_linux(self):
        self.assertEqual(memlimit.verdict(None, False, "12g", False)["state"], "n/a")

    def test_cgroup_line(self):
        self.assertEqual(memlimit.own_cgroup("0::/user.slice/x.scope\n"), "/user.slice/x.scope")
        self.assertIsNone(memlimit.own_cgroup("1:name=systemd:/x\n"))


class Exclusions(unittest.TestCase):
    def test_lists_only_existing_folders_and_warns(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".claude").mkdir()
            text = exclusions.render(root)
            self.assertIn(".claude", text)
            self.assertNotIn(".cursor", text.split("Windows Defender")[1].split("Note")[0])
            self.assertIn("security", text.lower())


class Cli(unittest.TestCase):
    def test_guard_test_flag(self):
        self.assertEqual(cli.main(["guard", "--test", "find / -name x"]), 2)
        self.assertEqual(cli.main(["guard", "--test", "find . -name x"]), 0)

    def test_guard_sees_rg_files_and_fd(self):
        for command in ("rg --files /", "fd x /", "rg --files ~"):
            self.assertEqual(cli.main(["guard", "--test", command]), 2, command)
        for command in ("rg --files src", "fd x src", "rg / src"):
            self.assertEqual(cli.main(["guard", "--test", command]), 0, command)

    def test_blind_snapshot_is_exit_2(self):
        with mock.patch.object(cli.procs, "snapshot", return_value=None):
            self.assertEqual(cli.main(["orphans"]), 2)
            self.assertEqual(cli.main(["top"]), 2)

    def test_blind_views_are_exit_2(self):
        with mock.patch.object(cli.procs, "snapshot", return_value=None), \
                mock.patch.object(cli.mcp, "collect", return_value=([], [])):
            self.assertEqual(cli.main(["mcp"]), 2)
        with mock.patch.object(cli.memlimit, "status", return_value={"state": "unknown", "text": "x", "cgroup": None}):
            self.assertEqual(cli.main(["memlimit"]), 2)
        with mock.patch.object(cli.procs, "snapshot", return_value=[proc()]), \
                mock.patch.object(cli.orphans, "find", side_effect=orphans.ViewError("x")), \
                mock.patch.object(cli.orphans, "stop") as stop:
            self.assertEqual(cli.main(["orphans", "--kill"]), 2)
            stop.assert_not_called()

    def test_orphans_never_stop_without_flag(self):
        with mock.patch.object(cli.procs, "snapshot", return_value=[proc(pid=10, cmd="node /x/foo-mcp/i.js")]), \
                mock.patch.object(cli.orphans, "_launchd_pids", return_value=set()), \
                mock.patch.object(cli.orphans, "_systemd_service", return_value=False), \
                mock.patch.object(cli.orphans, "read_allow", return_value=[]), \
                mock.patch.object(cli.orphans, "stop") as stop:
            with mock.patch.object(orphans.procs, "IS_WIN", False):
                self.assertEqual(cli.main(["orphans"]), 1)
            stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
