"""Cross-platform process snapshot and the grouping that `top` will not do for you.

`top` shows one row per process, so forty 90 MB copies of one server never reach
the first screen. Everything here sums by a normalised name instead.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from typing import Dict, List, Optional

IS_WIN = os.name == "nt"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# Words that say nothing about which program an interpreter is running.
_BORING = {"index.js", "index.mjs", "index.cjs", "index.ts", "main.js", "main.py", "cli.js",
           "cli.mjs", "server.js", "server.py", "__main__.py", "dist", "build", "lib", "bin",
           "src", "out", "app", "run", "start", "exec", "x", "tool", "node_modules", ".bin"}
_INTERPRETERS = {"node", "bun", "deno", "python", "python3", "pypy3", "uv", "uvx", "npx",
                 "npm", "pnpm", "pipx", "tsx", "ts-node", "java", "ruby"}

# "mcp" as a part of a name. Plain substring matching would also hit `--mcp-config`
# on the agent's own command line.
MCP_RE = re.compile(r"(?:^|[-_/@.\\])mcp(?:$|[-_/@.\\])|modelcontextprotocol", re.I)
AGENT_RE = re.compile(r"^(claude|codex|cursor-agent|gemini|agy|opencode|grok|aider|qwen)(\.exe)?$")


class Proc:
    __slots__ = ("pid", "ppid", "rss_mb", "cpu", "age_s", "stat", "exe", "cmd")

    def __init__(self, pid, ppid, rss_mb, cpu, age_s, stat, exe, cmd):
        self.pid, self.ppid, self.rss_mb, self.cpu = pid, ppid, rss_mb, cpu
        self.age_s, self.stat, self.exe, self.cmd = age_s, stat, exe, cmd

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


def parse_etime(text: str) -> int:
    """`ps etime`: [[dd-]hh:]mm:ss -> seconds. Unknown shape -> 0 (treated as young)."""
    match = re.fullmatch(r"(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)", text.strip())
    if not match:
        return 0
    days, hours, minutes, seconds = (int(x or 0) for x in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_ps(main: str, comm: str = "") -> List[Proc]:
    """Parse `ps` output. command= comes last because only it may contain spaces.

    macOS `comm` prints the full path, and that path often contains a space
    ("Application Support"), so it is read from a separate two-column call.
    """
    exe: Dict[int, str] = {}
    for line in comm.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            exe[int(parts[0])] = parts[1]
    out = []
    for line in main.splitlines():
        parts = line.strip().split(None, 6)
        if len(parts) < 7 or not parts[0].isdigit():
            continue
        pid, ppid, rss, cpu, etime, stat, cmd = parts
        try:
            out.append(Proc(int(pid), int(ppid), int(rss) / 1024, float(cpu.replace(",", ".")),
                            parse_etime(etime), stat, exe.get(int(pid), ""), cmd))
        except ValueError:
            continue
    return out


def _win_date(value) -> Optional[float]:
    match = re.search(r"/Date\((\d+)", str(value or ""))
    return int(match.group(1)) / 1000 if match else None


def parse_windows(text: str, now: float) -> List[Proc]:
    """Parse ConvertTo-Json of Win32_Process. PowerShell emits an object, not a list, for one row."""
    data = json.loads(text or "[]")
    if isinstance(data, dict):
        data = [data]
    out = []
    for row in data:
        created = _win_date(row.get("CreationDate"))
        out.append(Proc(int(row.get("ProcessId") or 0), int(row.get("ParentProcessId") or 0),
                        int(row.get("WorkingSetSize") or 0) / (1024 * 1024), 0.0,
                        int(now - created) if created else 0, "",
                        str(row.get("Name") or ""),
                        str(row.get("CommandLine") or row.get("Name") or "")))
    return out


def snapshot() -> Optional[List[Proc]]:
    """All processes, or None when the OS would not tell us (callers must say so out loud)."""
    try:
        if IS_WIN:
            script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,"
                      "WorkingSetSize,CreationDate,Name,CommandLine | ConvertTo-Json -Compress")
            result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                    capture_output=True, text=True, timeout=60)
            return parse_windows(result.stdout, time.time()) if result.returncode == 0 else None
        main = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,rss=,pcpu=,etime=,stat=,command="],
                              capture_output=True, text=True, timeout=30)
        comm = subprocess.run(["ps", "-ax", "-o", "pid=,comm="],
                              capture_output=True, text=True, timeout=30)
        return parse_ps(main.stdout, comm.stdout) if main.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _base(token: str) -> str:
    return re.split(r"[\\/]", token.rstrip("\\/"))[-1]


def _strip_version(name: str) -> str:
    return name if name.startswith("@") else re.sub(r"@[\w.^~-]*$", "", name)


def script_label(tokens: List[str]) -> str:
    """Name what an interpreter runs: `node /x/foo-mcp/dist/index.js` -> foo-mcp."""
    for token in tokens:
        if token.startswith("-") or "=" in token:
            continue
        pieces = [x for x in re.split(r"[\\/]", token) if x]
        for piece in reversed(pieces):
            clean = _strip_version(piece)
            low = clean.lower()
            if low not in _BORING and low not in _INTERPRETERS and len(clean) > 1:
                return clean
    return ""


def label(proc: Proc) -> str:
    """Stable group name. Electron helpers roll up into their app."""
    source = proc.exe or proc.cmd
    bundle = re.search(r"/([^/]+)\.app/", source + "/")
    if bundle:
        return bundle.group(1)
    tokens = proc.cmd.split()
    first = _base(proc.exe) if proc.exe else (_base(tokens[0]) if tokens else "?")
    first = re.sub(r"\.exe$", "", first, flags=re.I)
    stem = re.sub(r"[\d.]+$", "", first.lower())
    if stem in _INTERPRETERS:
        inner = script_label(tokens[1:])
        if inner:
            return "%s: %s" % (stem, inner)
    return first


def program_tokens(proc: Proc) -> List[str]:
    """The program itself: its executable, plus what it runs if it is an interpreter.

    `vim /tmp/foo-mcp-notes.md` is vim, and arguments never make something a server.
    """
    tokens = proc.cmd.split()
    if not tokens:
        return []
    first = _base(tokens[0]).lower()
    first = re.sub(r"\.exe$", "", first)
    if re.sub(r"[\d.]+$", "", first) not in _INTERPRETERS:
        return [tokens[0]]
    picked = [tokens[0]]
    for token in tokens[1:8]:
        if token.startswith("-") or token.lower() in ("run", "exec", "x", "tool"):
            continue
        picked.append(token)
        break
    return picked


def is_mcp(proc: Proc) -> bool:
    """True for an MCP server, judged by the program and never by its arguments."""
    return any(MCP_RE.search(token) for token in program_tokens(proc))


def group(snapshot_rows: List[Proc]) -> List[dict]:
    """Sum by label, biggest first. Summed RSS over-counts shared pages; the copy count is exact."""
    table: Dict[str, dict] = {}
    for proc in snapshot_rows:
        name = label(proc)
        row = table.setdefault(name, {"name": name, "copies": 0, "rss_mb": 0.0, "cpu": 0.0})
        row["copies"] += 1
        row["rss_mb"] += proc.rss_mb
        row["cpu"] += proc.cpu
    return sorted(table.values(), key=lambda r: -r["rss_mb"])


def count_sessions(snapshot_rows: List[Proc]) -> int:
    """Live agent CLI sessions. Node-hosted CLIs show up as `node .../bin/claude`."""
    total = 0
    for proc in snapshot_rows:
        tokens = proc.cmd.split()
        names = ([_base(proc.exe)] if proc.exe else []) + [_base(t) for t in tokens[:2]]
        if any(AGENT_RE.match(n) for n in names if n):
            total += 1
    return total
