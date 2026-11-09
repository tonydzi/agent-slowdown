"""Print indexer and antivirus steps for agent folders. Applies nothing.

Excluding a folder from your antivirus is a security decision, so it stays yours: this
module only prints what to run and what it costs you.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

AGENT_DIRS = (".claude", ".codex", ".cursor", ".gemini", ".antigravity", ".cache/claude",
              ".npm/_npx", ".cache/uv", ".cache/puppeteer")


def existing(home: Path) -> List[Path]:
    return [home / rel for rel in AGENT_DIRS if (home / rel).is_dir()]


def render(home: Path) -> str:
    dirs = existing(home)
    lines = ["macOS Spotlight",
             "  Dot folders such as ~/.claude were not indexed in our test, so there is usually",
             "  nothing to exclude there. Your repositories and worktrees are the heavy part.",
             "  A folder whose name ends in .noindex was skipped: keep worktrees in ~/agents.noindex/",
             "  A .metadata_never_index file did NOT stop indexing on macOS 26.",
             "  For an existing folder: System Settings > Spotlight > Search Privacy (older macOS:",
             "  Siri & Spotlight > Spotlight Privacy).",
             "  See docs/06-indexers.md for what we measured.",
             "",
             "Windows Defender (run in an elevated PowerShell, each line is your decision):"]
    for path in dirs or [home / ".claude"]:
        rel = str(path.relative_to(home)).replace("/", "\\")
        lines.append('  Add-MpPreference -ExclusionPath "$env:USERPROFILE\\%s"' % rel)
    lines += ['  Add-MpPreference -ExclusionPath "<your repositories folder>"',
              "",
              "Note: an excluded folder is not scanned, so anything an agent downloads there is not",
              "scanned either. That is a real security trade-off. We have not measured these commands",
              "ourselves; the speed-up figures in the docs come from other people's reports."]
    return "\n".join(lines)
