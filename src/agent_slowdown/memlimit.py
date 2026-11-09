"""Does CLAUDE_CODE_TOOL_MEMORY_LIMIT actually protect you on this Linux box?

The variable asks Claude Code to put Bash tool commands in a cgroup with a memory cap.
That group is created next to Claude's own cgroup. In a login shell or cron that parent
is `session-N.scope`, an ordinary user may not write there, and the setup fails with
EACCES. It logs one debug line and then runs your command uncapped, so a command using
1 GB went through a 300 MB limit in our test while everything looked enabled.

The fix is to start Claude inside a scope your user owns:

    systemd-run --user --scope claude
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import procs

ROOT = Path("/sys/fs/cgroup")
VAR = "CLAUDE_CODE_TOOL_MEMORY_LIMIT"
_ALLOC = "b = bytearray(400 * 1024 * 1024)\nfor i in range(0, len(b), 4096): b[i] = 1\nprint('allocated')"


def own_cgroup(text: Optional[str] = None) -> Optional[str]:
    """cgroup v2 path of this process, from the `0::/path` line."""
    if text is None:
        try:
            text = Path("/proc/self/cgroup").read_text()
        except OSError:
            return None
    for line in text.splitlines():
        if line.startswith("0::"):
            return line[3:].strip()
    return None


def verdict(cgroup: Optional[str], parent_writable: bool, value: Optional[str], is_linux: bool) -> dict:
    """Pure decision: can a sibling group be created where Claude would create it?"""
    if not is_linux:
        return {"state": "n/a", "text": "Linux only: this variable does nothing on macOS or Windows."}
    if cgroup is None:
        return {"state": "unknown", "text": "No cgroup v2 path found, cannot judge."}
    if parent_writable:
        state = "ok" if value else "off"
        text = ("%s=%s can take effect here." % (VAR, value) if value else
                "This shell could use the limit. Set %s=12g (or none to disable)." % VAR)
        return {"state": state, "text": text}
    return {"state": "silent-failure" if value else "would-fail",
            "text": ("%s is set, but the parent of %s is not writable: expect `tool cgroup: disabled "
                     "(EACCES)` in the debug log and no limit at all. Start Claude with "
                     "`systemd-run --user --scope claude`." % (VAR, cgroup))}


def status() -> dict:
    cgroup = own_cgroup() if procs.IS_LINUX else None
    writable = False
    if cgroup is not None:
        parent = ROOT / cgroup.lstrip("/")
        writable = os.access(str(parent.parent), os.W_OK)
    result = verdict(cgroup, writable, os.environ.get(VAR), procs.IS_LINUX)
    result.update({"cgroup": cgroup, "parent_writable": writable, "value": os.environ.get(VAR)})
    return result


def live_test(limit: str = "200M") -> dict:
    """Prove a user scope really caps memory: allocate 400 MB under a 200 MB ceiling."""
    if not procs.IS_LINUX or not shutil.which("systemd-run"):
        return {"proved": None, "text": "needs Linux with systemd-run"}
    command = ["systemd-run", "--user", "--scope", "--quiet", "-p", "MemoryMax=" + limit,
               "-p", "MemorySwapMax=0", "python3", "-c", _ALLOC]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"proved": None, "text": "could not run: %s" % type(exc).__name__}
    if run.returncode in (137, -9):
        return {"proved": True, "text": "exit %d: killed at the ceiling, the scope works" % run.returncode}
    if "allocated" in run.stdout:
        return {"proved": False, "text": "400 MB allocated under %s: no limit applied" % limit}
    return {"proved": None, "text": "exit %d: %s" % (run.returncode, (run.stderr or "").strip()[:160])}
