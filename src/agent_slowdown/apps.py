"""Electron and desktop app growth: measure it, and say plainly what the cure is.

There is no cure from the outside. A renderer's heap belongs to the app, and no external
tool can shrink it. What works is restarting the app, and what this module does is tell
you which one is worth restarting. It never stops anything.
"""
from __future__ import annotations

from typing import Dict, List

from . import procs

WATCHED = ("Cursor", "Claude", "Codex", "ChatGPT", "Antigravity", "Windsurf", "Visual Studio Code",
           "Code", "Kiro", "Zed", "Trae")
DEFAULT_WARN_GB = 4.0


_LOWER = {name.lower(): name for name in WATCHED}
_CLI_NAMES = {"claude", "codex"}  # lower-case binaries are the command-line agents


def app_of(proc: procs.Proc) -> str:
    name = procs.label(proc)
    return "" if name in _CLI_NAMES else _LOWER.get(name.lower(), "")


def report(snapshot: List[procs.Proc], warn_gb: float = DEFAULT_WARN_GB) -> List[dict]:
    table: Dict[str, dict] = {}
    for proc in snapshot:
        name = app_of(proc)
        if not name:
            continue
        row = table.setdefault(name, {"app": name, "copies": 0, "rss_mb": 0.0, "largest_mb": 0.0,
                                      "uptime_h": 0.0, "restart": False})
        row["copies"] += 1
        row["rss_mb"] += proc.rss_mb
        row["largest_mb"] = max(row["largest_mb"], proc.rss_mb)
        row["uptime_h"] = max(row["uptime_h"], proc.age_s / 3600)
    for row in table.values():
        row["restart"] = row["rss_mb"] >= warn_gb * 1024
    return sorted(table.values(), key=lambda r: -r["rss_mb"])
