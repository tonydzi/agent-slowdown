"""Find and stop agent helpers whose parent session is gone.

The test is "the parent died", not "it has been idle". A helper with a dead parent has
nobody left to answer to, however busy it looks; an idle helper with a live parent may
be waiting for the next turn. Orphans are often hot and often ignore SIGTERM, so a stop
is SIGTERM, a short wait, then SIGKILL.

Three things keep this from shooting something you need:
  1. only known agent helpers are candidates, never "anything with PPID 1";
  2. anything a service manager owns is skipped: a shared MCP daemon under launchd or
     systemd has PPID 1 too, and it is the very thing you set up to fix this problem;
  3. nothing is stopped without --kill, and every PID is re-read just before the signal,
     so a recycled PID is left alone.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import procs

ALLOW_FILE = Path.home() / ".config" / "agent-slowdown" / "allow.txt"
DEFAULT_MIN_AGE_S = 600

# Helpers that agents start and that are useless once the session is gone.
HELPERS = {"rg", "ripgrep", "find", "bfs", "fd", "grep", "egrep", "tsserver", "tsserver.js",
           "typescript-language-server", "pyright", "pyright-langserver", "pylsp", "gopls",
           "rust-analyzer", "eslint_d", "npx", "playwright", "cursor-agent"}
_INTERPRETERS = {"node", "bun", "python", "python3", "sh", "bash", "zsh"}
# A shell the agent's Bash tool started: every tool call sources a snapshot file first.
# Left behind, it is often a wait loop polling for a file that will never appear.
AGENT_SHELL_RE = re.compile(r"[/\\]\.(?:claude|codex)[/\\]shell[-_]snapshots[/\\]")
# Interpreters and shells are too generic to judge by name alone.
_SKIP_NAMES = {"launchd", "init", "systemd", "kernel_task"}


class ViewError(Exception):
    """We could not tell what must be protected, so nothing may be stopped."""


def is_helper(proc: procs.Proc) -> bool:
    """Judge the program itself. A word inside a shell one-liner proves nothing."""
    if procs.is_mcp(proc) or AGENT_SHELL_RE.search(proc.cmd):
        return True
    tokens = proc.cmd.split()
    names = [re.split(r"[\\/]", t)[-1].lower().replace(".exe", "") for t in tokens[:3]]
    # Arguments of a plain program never qualify it; only an interpreter's script can.
    if not names:
        return False
    if names[:2] == ["npm", "exec"] or names[0] in HELPERS:
        return True
    return names[0] in _INTERPRETERS and len(names) > 1 and names[1] in HELPERS


def _launchd_pids() -> Set[int]:
    """PIDs launchd runs as jobs. These have PPID 1 and are services, not orphans."""
    if not procs.IS_MAC:
        return set()
    try:
        run = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ViewError("launchctl failed: %s" % type(exc).__name__)
    if run.returncode != 0:
        raise ViewError("launchctl exit %d" % run.returncode)
    return {int(line.split()[0]) for line in run.stdout.splitlines() if line[:1].isdigit()}


def _systemd_service(pid: int) -> bool:
    """True when the process sits directly in a systemd .service cgroup."""
    if not procs.IS_LINUX:
        return False
    try:
        text = Path("/proc/%d/cgroup" % pid).read_text()
    except FileNotFoundError:
        return False  # it has exited
    except OSError:
        return True  # cannot read it, so treat it as protected
    return any(line.rstrip().endswith(".service") for line in text.splitlines())


def read_allow(path: Path = ALLOW_FILE) -> List[str]:
    """Substrings of command lines that must never be stopped, one per line."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ViewError("cannot read %s: %s" % (path, type(exc).__name__))
    return [x.strip() for x in lines if x.strip() and not x.lstrip().startswith("#")]


def parent_is_dead(proc: procs.Proc, by_pid: Dict[int, procs.Proc]) -> bool:
    """POSIX: re-parented to init or to `systemd --user`. Windows: parent PID gone or recycled."""
    if procs.IS_WIN:
        parent = by_pid.get(proc.ppid)
        return parent is None or (parent.age_s and proc.age_s and parent.age_s < proc.age_s)
    if proc.ppid == 1:
        return True
    parent = by_pid.get(proc.ppid)
    return bool(parent and re.search(r"systemd --user|/systemd\s+--user", parent.cmd))


def find(snapshot: List[procs.Proc], min_age_s: int = DEFAULT_MIN_AGE_S,
         managed: Optional[Set[int]] = None, allow: Optional[List[str]] = None,
         service_check=_systemd_service) -> Tuple[List[procs.Proc], int]:
    """Return (orphans, skipped_as_service)."""
    managed = _launchd_pids() if managed is None else managed
    allow = read_allow() if allow is None else allow
    by_pid = {p.pid: p for p in snapshot}
    me = os.getpid()
    found, services = [], 0
    for proc in snapshot:
        if proc.pid in (0, 1, me) or procs.label(proc) in _SKIP_NAMES:
            continue
        if not is_helper(proc) or not parent_is_dead(proc, by_pid):
            continue
        if proc.pid in managed or service_check(proc.pid):
            services += 1
            continue
        if proc.age_s < min_age_s or any(item in proc.cmd for item in allow):
            continue
        found.append(proc)
    return sorted(found, key=lambda p: -p.rss_mb), services


def zombies(snapshot: List[procs.Proc]) -> List[procs.Proc]:
    """State Z. They hold no memory and cannot be killed; only the parent can reap them."""
    return [p for p in snapshot if p.stat.startswith("Z")]


def _still_same(proc: procs.Proc) -> bool:
    now = procs.snapshot() or []
    for row in now:
        if row.pid == proc.pid:
            return row.cmd == proc.cmd and row.age_s >= proc.age_s
    return False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop(proc: procs.Proc, grace_s: float = 5.0) -> str:
    """Stop one orphan. Returns gone, killed, recycled or failed: <reason>."""
    if not _still_same(proc):
        return "recycled"
    try:
        if procs.IS_WIN:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=20, check=True)
            return "killed"
        os.kill(proc.pid, signal.SIGTERM)
        deadline = time.time() + grace_s
        while time.time() < deadline:
            if not _alive(proc.pid):
                return "gone"
            time.sleep(0.2)
        if not _still_same(proc):
            return "gone"  # exited during the wait, and the PID may already be someone else's
        os.kill(proc.pid, signal.SIGKILL)
        return "killed"
    except ProcessLookupError:
        return "gone"
    except (OSError, subprocess.SubprocessError) as exc:
        return "failed: %s" % type(exc).__name__
