"""Offline replay: command strings are never executed by a shell or find."""
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock

from agent_slowdown import guard

INCIDENTS = [
    "find / -iname 'metrics_export*' 2>/dev/null | head -10",
    "find / -iname state.sqlite3 2>/dev/null | head -10",
    "find / -iname candidates.jsonl 2>/dev/null | head -10",
    "find / -iname SKILL.md -path '*triage*' 2>/dev/null; find /c/Users/dev -iname SKILL.md -path '*triage*'",
    "python scripts/check.py; find / -maxdepth 6 -type d -name _help -path '*agent-bus*' 2>/dev/null | head -10",
]
BLOCK = INCIDENTS + [
    "find '/' -name data", 'find "/" -name data', 'find -L /',
    'find -P -H /', 'find -O3 -D search /', 'find -- /',
    'find /c/Users/dev /', 'find /c /proc/123/fd', 'find /proc',
    'find /proc/self/fd', 'find /./proc/../', 'find /./', 'find ///',
    '/usr/bin/find /', '"C:/Program Files/Git/usr/bin/find.exe" /',
    '& "C:\\Program Files\\Git\\usr\\bin\\find.exe" "/"',
    'env A=b find /', 'env -u HOME --chdir=/tmp find /',
    'timeout 10s find /', 'timeout --signal KILL 10 find /',
    'timeout -k 1 10 env X=y find /', 'command find /', 'exec find /',
    'nice -n 10 find /', "bash -lc 'find / -name data'",
    'sh -c "env A=b find /proc"',
    'powershell.exe -NoProfile -Command "/usr/bin/find / -name data"',
    'echo x && find /', 'echo x || find /proc',
    'if find /; then echo yes; fi', 'for x in a b; do find /; done',
    '2>/dev/null find /', 'find /c -name x;\nfind /',
    '# comment\nfind /proc', 'find \\\n/ -name x',
    "cat <<- EOF\nsafe\nEOF\nfind /",
    "cat <<- EOF\nsafe\n\tEOF\nfind /",
    "cat <<'-EOF'\nsafe\n-EOF\nfind /",
]
POSIX_BLOCK = [
    'find / -name x', 'bfs -S dfs / -name SKILL.md', 'nice -n 10 find / -name x',
    "bash -lc 'find / -name data'", 'find ~ -name x', 'find ~/ -name x',
    'rg -l needle /', 'rg needle ~', 'timeout 30 bfs / -name x', 'find /proc',
]
POSIX_ALLOW = [
    'find ~/.claude/scripts -name x', 'find . -name x', 'bfs ~/projects -name x',
    'rg needle ~/.claude/scripts', 'rg --files ~/.claude/hooks', 'mdfind -name SKILL.md',
    'echo "find / -name x"', 'grep -r needle ~/.claude/memory', 'find /procurement -type f',
    'ssh host find / -name x',
]
ALLOW = [
    '', 'echo "find / -name x"', "printf '%s' 'find /proc'",
    "grep 'find /' data.txt", "python -c 'print(\"find /\")'",
    'find /c/Users/dev/.claude -iname state.sqlite3',
    'find /e/work/tools -name _help', 'find E:/work -name x',
    'find . -name x', 'find .. -name x', 'find -L /c/Users -name x',
    "find /c -name 'find /'", 'find /c -path /proc -print',
    'find /procurement -type f', 'find /c /e -type f',
    'rg --files C:/Users/dev/.claude', '# find /\necho ok',
    'echo find /', 'printf find /', 'grep find /',
    'test "find /" = "find /"', '[ "find /" = "find /" ]',
    '"C:\\Windows\\System32\\find.exe" "/" file.txt',
    '& C:\\Windows\\SysWOW64\\find.exe "/" file.txt',
    '/c/Windows/System32/find.exe / file.txt',
    "bash -lc 'echo \"find /\"'", 'command -v find',
    'env X="find /" echo yes', 'timeout 10 echo "find /"',
    'wsl.exe -e find / -name x', 'ssh host find / -name x',
    "cat <<'EOF'\nfind / -name x\nEOF\necho ok",
    "cat <<'EOF'\n  EOF\nfind /\nEOF",
    "cat <<'EOF'\nEOF  \nfind /\nEOF",
    "cat <<'EOF'\n\tEOF\nfind /\nEOF",
    "cat <<- EOF\n  EOF\nfind /\nEOF",
    "cat <<'-EOF'\nEOF\nfind /\n-EOF",
    'powershell.exe -NoProfile -Command "find / file.txt"',
]

class Detection(unittest.TestCase):
    def test_incidents(self):
        for command in INCIDENTS:
            with self.subTest(command=command):
                self.assertTrue(guard.judge(command, is_windows=True))

    def test_block(self):
        for command in BLOCK:
            with self.subTest(command=command):
                self.assertTrue(guard.judge(command, is_windows=True))

    def test_allow(self):
        for command in ALLOW:
            with self.subTest(command=command):
                self.assertIsNone(guard.judge(command, is_windows=True))

    def test_posix(self):
        # A79708 05.11.2026: POSIX is guarded too (MacPro192 bfs storm 02.11).
        for command in POSIX_BLOCK:
            with self.subTest(command=command):
                self.assertTrue(guard.judge(command, is_windows=False))
        for command in POSIX_ALLOW:
            with self.subTest(command=command):
                self.assertIsNone(guard.judge(command, is_windows=False))

    def test_payload(self):
        for tool, key in [('Bash','command'), ('PowerShell','script'), ('exec_command','cmd'), ('shell','command')]:
            for command in INCIDENTS:
                if tool != 'Bash':
                    command = command.replace('find /', '/usr/bin/find /')
                self.assertTrue(guard.check_payload({'tool_name': tool, 'tool_input': {key: command}}, is_windows=True))
        for payload in [None, [], {}, {'tool_input': None}, {'tool_name':'Write','tool_input':{'command':'find /'}}]:
            self.assertIsNone(guard.check_payload(payload, is_windows=True))

    def test_shell_context(self):
        for tool, key in [('PowerShell','command'), ('exec_command','cmd'), ('shell','command')]:
            with self.subTest(tool=tool):
                self.assertIsNone(guard.check_payload({'tool_name':tool, 'tool_input':{key:'find "/" file.txt'}}, is_windows=True))
                self.assertTrue(guard.check_payload({'tool_name':tool, 'tool_input':{key:'bash -c "find /"'}}, is_windows=True))
                self.assertTrue(guard.check_payload({'tool_name':tool, 'tool_input':{key:'& "C:/Program Files/Git/usr/bin/find.exe" /'}}, is_windows=True))
        self.assertTrue(guard.check_payload({'tool_name':'exec_command', 'tool_input':{'cmd':'find /','shell':'C:/Program Files/Git/bin/bash.exe'}}, is_windows=True))

GREP_INCIDENT = r'''"C:\Program Files\Git\usr\bin\grep.exe" -rl 'needle-one|needle-two' /c/Users/dev'''
GREP_BLOCK = [
    GREP_INCIDENT, 'grep -r needle /c/Users/dev',
    'grep -Rl needle C:/Users/dev', 'grep --recursive needle /c/Users/dev/',
    'grep -rl --exclude=*.jsonl needle /c/Users/dev',
    'grep -r --exclude-dir .cache needle C:/Users/dev',
    'grep -re needle /c/Users/dev', 'grep -r -f patterns.txt /c/Users/dev',
    'grep -r -e /c/Users/dev /', 'grep -r needle /',
    'grep -r needle /c/Users/dev/.claude /c/Users/dev',
    'timeout 10 grep -rl needle /c/Users/dev',
    'bash -c "grep -rl needle /c/Users/dev"',
]
GREP_ALLOW = [
    'grep needle /c/Users/dev', 'grep -l needle C:/Users/dev',
    'grep -rl needle /c/Users/dev/.claude/memory', 'grep -r needle C:/Users/dev/logs',
    'grep -r /c/Users/dev ./logs', 'grep -r -e /c/Users/dev ./logs',
    'grep -r --regexp=/c/Users/dev ./logs',
    'grep -r -f /c/Users/dev ./logs', 'grep -e recursive /c/Users/dev',
    'grep -r --exclude /c/Users/dev needle ./logs',
    'grep -r needle /c/Users/dev-other',
    'echo "grep -rl needle /c/Users/dev"',
]

class GrepDetection(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(guard.Path, 'home', return_value=Path('C:/Users/dev'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_actual_incident(self):
        self.assertTrue(guard.judge(GREP_INCIDENT, is_windows=True))

    def test_block(self):
        for command in GREP_BLOCK:
            with self.subTest(command=command):
                self.assertTrue(guard.judge(command, is_windows=True))

    def test_allow(self):
        for command in GREP_ALLOW:
            with self.subTest(command=command):
                self.assertIsNone(guard.judge(command, is_windows=True))

    def test_posix_and_payload(self):
        for command in ['grep -r needle /', 'grep -rl needle ~', 'grep -r needle ~/.claude']:
            with self.subTest(command=command):
                self.assertTrue(guard.judge(command, is_windows=False))
        self.assertTrue(guard.check_payload({'tool_name':'PowerShell','tool_input':{'command':GREP_INCIDENT}}, is_windows=True))

    def test_home_is_derived(self):
        with mock.patch.object(guard.Path, 'home', return_value=Path('D:/Users/Operator')):
            self.assertTrue(guard.judge('grep -rl needle /d/Users/Operator', is_windows=True))
            self.assertTrue(guard.judge('grep -rl needle D:/Users/Operator', is_windows=True))
            self.assertIsNone(guard.judge('grep -rl needle /c/Users/dev', is_windows=True))

class ProfileGrepDetection(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(guard.Path, 'home', return_value=Path('C:/Users/dev'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_profile_historical_command_shape(self):
        command = 'cd ~/.claude && grep -rl synthetic_needle --include=*.json --include=*.md --include=*.txt . | head -20'
        self.assertTrue(guard.judge(command, is_windows=True))

    def test_profile_roots_and_wrappers(self):
        commands = [
            'grep -rl needle ~/.claude', 'grep -r needle ~/.codex/',
            'grep -r needle C:/Users/dev/.claude/./',
            'grep -r needle /c/Users/dev/.codex',
            'cd ~; grep -rl needle .', 'cd ~/.claude && grep -rl needle',
            'cd ~/.codex\ngrep -r needle ./',
            'cd ~/.claude; timeout 5 grep -rl needle .',
            'cd ~/.codex && bash -lc "grep -rl needle ."',
            'env -C ~/.claude grep -rl needle .',
            'env --chdir=~/.codex grep -rl needle .',
            'bash -lc "cd ~/.claude && grep -rl needle ."',
            'cd "C:/Users/dev/.codex"; grep -r needle .',
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(guard.judge(command, is_windows=True))

    def test_payload_cwd_and_target_paths(self):
        for field in ['cwd', 'workdir']:
            self.assertTrue(guard.check_payload({'tool_name':'exec_command', 'tool_input':{
                'cmd':'grep -rl needle .', field:'C:/Users/dev/.claude'}}, is_windows=True))
        self.assertTrue(guard.check_payload({'tool_name':'Bash', 'cwd':'C:/Users/dev/.codex',
            'tool_input':{'command':'grep -rl needle .'}}, is_windows=True))
        allowed = [
            'cd ~/.claude/memory && grep -rl needle .',
            'cd ~/.codex; grep -r needle ./sessions/2026/10/14',
            'cd ~/.claude; grep -rn needle settings.json',
            'cd ~/.claude; grep -r needle ./logs/one.log',
            'cd ~/.claude; cd memory; grep -rl needle .',
            'cd ~/.claude; cd "$dynamic"; grep -rl needle .',
            'cd ~/.claude | cat; grep -rl needle .',
            'echo "cd ~/.claude && grep -rl needle ."',
            'grep -r -e ~/.claude ./memory',
            'grep -r --exclude ~/.claude needle ./memory',
            'grep -r needle ~/.claude-other',
            "grep -r needle '~/.claude'", 'grep -r needle "~/.codex"',
            "cd '~/.claude'; grep -rl needle .",
            'env -C ~/.claude/memory grep -rl needle .',
        ]
        for command in allowed:
            with self.subTest(command=command):
                self.assertIsNone(guard.judge(command, is_windows=True))
        # Missing/dynamic cwd is UNKNOWN, not assumed to be the agent profile.
        self.assertIsNone(guard.judge('grep -rl needle .', is_windows=True))


class Hook(unittest.TestCase):
    def call(self, payload):
        error = io.StringIO()
        return guard.main(io.StringIO(payload), error, is_windows=False), error.getvalue()

    def test_block_contract(self):
        code, message = self.call(json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'find / -name x'}}))
        self.assertEqual(code, 2)
        self.assertIn('broad search blocked', message)

    def test_allow_and_malformed(self):
        for payload in ['', 'garbage', '[]', json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'echo "find /"'}})]:
            self.assertEqual(self.call(payload), (0, ''))


if __name__ == '__main__':
    unittest.main()
