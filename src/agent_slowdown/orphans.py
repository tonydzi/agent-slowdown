"""Find and stop agent helpers whose parent session is gone.

PPID 1 proves nothing by itself: on one working Mac 436 processes had it, and 309 of those
were launchd services. So a process is stopped only on a sum of evidence, and doubt is
reported instead of acted on:

  1. it is yours, older than an hour, and a known agent helper, judged by the program and
     never by its arguments;
  2. its parent is gone;
  3. no service manager owns it (a shared MCP daemon under launchd or systemd has PPID 1
     too, and it is the very thing you set up to fix this problem);
  4. it is not paused and not listening on a port;
  5. the session that started it is not still alive.

Orphans are often hot and often ignore SIGTERM, so a stop is SIGTERM, a short wait, then
SIGKILL. Nothing is stopped without --kill, and the process is re-read before each signal,
so a recycled PID is left alone.
"""
from __future__ import annotations

import glob
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import procs

ALLOW_FILE = Path.home() / ".config" / "agent-slowdown" / "allow.txt"
DEFAULT_MIN_AGE_S = 3600
SAME_BIRTH_S = 120
FRESH_TRANSCRIPT_S = 2 * 3600

# Helpers that agents start and that are useless once the session is gone.
HELPERS = {"rg", "ripgrep", "find", "bfs", "fd", "grep", "egrep", "tsserver", "tsserver.js",
           "typescript-language-server", "pyright", "pyright-langserver", "pylsp", "gopls",
           "rust-analyzer", "eslint_d", "npx", "playwright", "cursor-agent"}
_INTERPRETERS = {"node", "bun", "python", "python3", "sh", "bash", "zsh"}
# A shell the agent's Bash tool started: every tool call sources a snapshot file first.
# Left behind, it is often a wait loop polling for a file that will never appear.
AGENT_SHELL_RE = re.compile(r"[/\\]\.(?:claude|codex)[/\\]shell[-_]snapshots[/\\]")
SNAPSHOT_BORN_RE = re.compile(r"snapshot-\w+-(\d{13})-")
# A script in Claude Code's own session scratch folder.
AGENT_SCRATCH_RE = re.compile(r"/tmp/claude-[\d]+/")
CLAUDE_PID_RE = re.compile(r"(?:^|\s)CLAUDE_PID=(\d+)")
_SKIP_NAMES = {"launchd", "init", "systemd", "kernel_task"}
_UNSET = object()


class ViewError(Exception):
    """We could not tell what must be protected, so nothing may be stopped."""


def is_helper(proc: procs.Proc) -> bool:
    """Judge the program itself. A word inside a shell one-liner proves nothing."""
    if procs.is_mcp(proc) or AGENT_SHELL_RE.search(proc.cmd):
        return True
    tokens = proc.cmd.split()
    names = [re.split(r"[\\/]", t)[-1].lower().replace(".exe", "") for t in tokens[:3]]
    if not names:
        return False
    # Arguments of a plain program never qualify it; only an interpreter's script can.
    if names[:2] == ["npm", "exec"] or names[0] in HELPERS:
        return True
    if names[0] not in _INTERPRETERS or len(names) < 2:
        return False
    return names[1] in HELPERS or bool(AGENT_SCRATCH_RE.search(tokens[1]))


def _launchd_pids() -> Set[int]:
    """PIDs launchd runs as jobs. These have PPID 1 and are services, not orphans."""
    if not procs.IS_MAC:
        return set()
    try:
        run = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ViewError("launchctl failed: %s" % type(exc).__name__)
    pids = {int(line.split()[0]) for line in run.stdout.splitlines() if line[:1].isdigit()}
    if run.returncode != 0 or len(pids) < 3:
        raise ViewError("launchctl gave no usable list")
    return pids


def _systemd_service(pid: int) -> bool:
    """True when the process is inside a systemd service, however deep in its cgroup."""
    if not procs.IS_LINUX:
        return False
    try:
        text = Path("/proc/%d/cgroup" % pid).read_text()
    except FileNotFoundError:
        return False  # it has exited
    except OSError:
        return True  # cannot read it, so treat it as protected
    # user@1000.service is the session manager, and what sits under it is judged by its tail.
    return any(part.endswith(".service") and not part.startswith("user@")
               for line in text.splitlines() for part in line.strip().split("/"))


def _listen_pids() -> Optional[Set[int]]:
    """PIDs listening on TCP, or None when neither lsof nor ss can tell us."""
    try:
        run = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fp"],
                             capture_output=True, text=True, timeout=30)
        if run.returncode in (0, 1):  # 1 means an empty list
            return {int(x[1:]) for x in run.stdout.split() if re.fullmatch(r"p\d+", x)}
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        run = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, timeout=30)
        if run.returncode == 0:
            return {int(x) for x in re.findall(r"pid=(\d+)", run.stdout)}
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _env_of(pid: int) -> str:
    """A process's environment. Empty means unreadable, as with hardened macOS binaries."""
    try:
        if procs.IS_MAC:
            return subprocess.run(["ps", "-E", "-ww", "-o", "command=", "-p", str(pid)],
                                  capture_output=True, text=True, timeout=10).stdout
        return Path("/proc/%d/environ" % pid).read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def _transcripts() -> List[Tuple[float, Optional[float]]]:
    """(last write, creation time) of each Claude Code transcript on this machine."""
    out = []
    for path in glob.glob(str(Path.home() / ".claude" / "projects" / "*" / "*.jsonl")):
        try:
            info = os.stat(path)
        except OSError:
            continue
        out.append((info.st_mtime, getattr(info, "st_birthtime", None)))
    return out


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
        return parent is None or bool(parent.age_s and proc.age_s and parent.age_s < proc.age_s)
    if proc.ppid == 1:
        return True
    parent = by_pid.get(proc.ppid)
    return bool(parent and re.search(r"systemd --user|/systemd\s+--user", parent.cmd))


def session_alive(proc: procs.Proc, env: str, snapshot: List[procs.Proc], now: float,
                  transcripts: Callable[[], List[Tuple[float, Optional[float]]]]) -> bool:
    """Is the session that started this still running? Doubt counts as alive where we can tell."""
    agents = [p for p in snapshot if procs.is_agent(p)]
    match = CLAUDE_PID_RE.search(env)
    if match and int(match.group(1)) in {p.pid for p in agents}:
        return True
    born = SNAPSHOT_BORN_RE.search(proc.cmd)
    if not born:
        return False
    # A shell snapshot is written when the session starts, and zsh hides its environment on macOS.
    birth = int(born.group(1)) / 1000.0
    if any(abs((now - p.age_s) - birth) <= SAME_BIRTH_S for p in agents):
        return True
    return any(made is not None and abs(made - birth) <= SAME_BIRTH_S
               and now - written < FRESH_TRANSCRIPT_S for written, made in transcripts())


def triage(snapshot: List[procs.Proc], min_age_s: int = DEFAULT_MIN_AGE_S,
           managed: Optional[Set[int]] = None, allow: Optional[List[str]] = None,
           service_check: Optional[Callable[[int], bool]] = None, listening=_UNSET,
           env_of: Optional[Callable[[int], str]] = None, now: Optional[float] = None,
           transcripts: Optional[Callable] = None) -> dict:
    """Return {stop: [...], doubt: [(proc, why)], services: N}."""
    me, uid = os.getpid(), getattr(os, "getuid", lambda: None)()
    by_pid = {p.pid: p for p in snapshot}
    allow = read_allow() if allow is None else allow
    suspects = []
    for proc in snapshot:
        if (proc.pid in (0, 1, me) or procs.label(proc) in _SKIP_NAMES or proc.stat.startswith("Z")
                or (proc.uid is not None and uid is not None and proc.uid != uid)
                or proc.age_s < min_age_s or any(item in proc.cmd for item in allow)
                or (".app/contents/" in proc.cmd.lower() and "python.app/" not in proc.cmd.lower())):
            continue
        if is_helper(proc) and parent_is_dead(proc, by_pid):
            suspects.append(proc)
    result = {"stop": [], "doubt": [], "services": 0}
    if not suspects:
        return result
    managed = _launchd_pids() if managed is None else managed
    service_check = service_check or _systemd_service
    open_ports = _listen_pids() if listening is _UNSET else listening
    env_of = env_of or _env_of
    now = time.time() if now is None else now
    cache: Dict[str, list] = {}

    def transcript_list():
        if "rows" not in cache:
            cache["rows"] = (transcripts or _transcripts)()
        return cache["rows"]

    for proc in suspects:
        if proc.pid in managed or service_check(proc.pid):
            result["services"] += 1
        elif proc.stat.startswith("T"):
            result["doubt"].append((proc, "paused on purpose"))
        elif open_ports is None:
            result["doubt"].append((proc, "cannot tell if it listens on a port"))
        elif proc.pid in open_ports:
            result["doubt"].append((proc, "listening on a port"))
        elif session_alive(proc, env_of(proc.pid), snapshot, now, transcript_list):
            result["doubt"].append((proc, "its session is still alive"))
        else:
            result["stop"].append(proc)
    result["stop"].sort(key=lambda p: -p.rss_mb)
    return result


def find(snapshot: List[procs.Proc], **kw) -> Tuple[List[procs.Proc], int]:
    """Return (processes that may be stopped, skipped as services)."""
    result = triage(snapshot, **kw)
    return result["stop"], result["services"]


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
