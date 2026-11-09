"""Which MCP servers cost RAM in every session.

A server declared with `command` (stdio) is spawned once per agent session or thread.
A server declared with `url` is one process you run once, and every session connects.
Agents have no lazy flag for this, so "lazy" means: not in the always-on config.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import procs

# (agent, path relative to home, format). A missing file means the agent is not installed.
SOURCES = [
    ("claude", ".claude.json", "claude"),
    ("codex", ".codex/config.toml", "toml"),
    ("gemini-cli", ".gemini/settings.json", "json"),
    ("antigravity", ".gemini/antigravity/mcp_config.json", "json"),
    ("cursor", ".cursor/mcp.json", "json"),
    ("windsurf", ".codeium/windsurf/mcp_config.json", "json"),
    ("opencode", ".config/opencode/opencode.json", "opencode"),
]

REMEDY = {
    "claude": "claude mcp remove {n}   (add it back when needed: claude mcp add {n} -- <command>)",
    "codex": "set enabled = false under [mcp_servers.{n}] in ~/.codex/config.toml",
    "cursor": "Cursor > Settings > MCP > turn {n} off, or remove it from ~/.cursor/mcp.json",
    "opencode": 'set "enabled": false for {n} in opencode.json',
}
_TOKEN_SKIP = {"node", "python", "python3", "npx", "uv", "uvx", "bun", "deno", "npm", "cmd",
               "bash", "sh", "docker", "run", "exec", "main.py", "index.js", "--yes", "-y", "/c"}


_STRINGS = re.compile(r""""([^"]*)"|'([^']*)'""")
_STRICT = {"command", "url", "type", "enabled", "disabled"}


def parse_toml(text: str) -> dict:
    """tomllib on 3.11+. Older Pythons use a small reader that fails loud, never silently."""
    try:
        import tomllib  # type: ignore
    except ImportError:
        return _mini_toml(text)
    return tomllib.loads(text)


def _mini_toml(text: str) -> dict:
    servers: Dict[str, dict] = {}
    current = None
    in_root = False  # directly under a bare [mcp_servers] table
    open_key = None  # inside a multi-line array
    for raw in text.splitlines():
        line = raw.strip()
        if open_key:
            if open_key == "args" and current is not None:
                current.setdefault("args", []).extend(a or b for a, b in _STRINGS.findall(line))
            if line.startswith("]"):
                open_key = None
            continue
        header = re.match(r'^\[mcp_servers\.(?:"([^"]+)"|([^\].\s]+))\]\s*(#.*)?$', line)
        if header:
            current, in_root = servers.setdefault(header.group(1) or header.group(2), {}), False
            continue
        if re.match(r"^\[mcp_servers\]\s*(#.*)?$", line):
            current, in_root = None, True
            continue
        if line.startswith("["):
            if line.startswith("[mcp_servers") and not re.match(r"^\[mcp_servers[.\]]", line):
                raise ValueError("broken header: " + line[:60])
            current, in_root = None, False
            continue
        if in_root and line and not line.startswith("#"):
            raise ValueError("inline mcp_servers entries need Python 3.11+")
        if line.startswith("mcp_servers."):
            raise ValueError("dotted mcp_servers keys need Python 3.11+")
        if current is None or not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and "]" not in value:
            open_key = key
            continue
        if key == "args":
            current["args"] = [a or b for a, b in _STRINGS.findall(value)]
        elif key in _STRICT:
            pair = re.match(r"""^(true|false|"[^"]*"|'[^']*')""", value)
            if not pair:
                raise ValueError("cannot read this TOML line without Python 3.11+: " + line[:60])
            token = pair.group(1)
            current[key] = (token == "true") if token in ("true", "false") else token[1:-1]
    return {"mcp_servers": servers}


def _vendor_runtime(command: str) -> bool:
    """Servers the Codex desktop app installs and manages for itself."""
    path = command.replace("\\", "/")
    return ("/OpenAI/Codex/runtimes/" in path or path.startswith("/Applications/Codex.app/")
            or path.startswith("/Applications/ChatGPT.app/Contents/Resources/"))


def _classify(agent: str, name: str, spec, source: str) -> dict:
    row = {"agent": agent, "name": name, "source": source, "kind": "unknown",
           "command": "", "args": [], "copies": None, "rss_mb": None}
    if not isinstance(spec, dict):
        return row
    if spec.get("enabled", True) is False or spec.get("disabled") is True:
        row["kind"] = "disabled"
    elif spec.get("url") or spec.get("httpUrl") or str(spec.get("type", "")).lower() in ("remote", "http", "sse"):
        row["kind"] = "shared"
    elif spec.get("command"):
        row["kind"] = "per-session"
        command = spec.get("command")
        if isinstance(command, list):  # opencode keeps argv as one list
            row["command"], row["args"] = str(command[0]), [str(x) for x in command[1:]]
        else:
            row["command"], row["args"] = str(command), [str(x) for x in spec.get("args") or []]
        if _vendor_runtime(row["command"]):
            row["kind"] = "vendor-runtime"
    return row


def _servers_in(data: dict, fmt: str) -> dict:
    for key in (("mcp",) if fmt == "opencode" else ("mcpServers", "mcp_servers")):
        if data.get(key) is not None:
            return data[key]  # the caller rejects anything that is not a table
    return {}


def collect(home: Path, project: Optional[Path] = None) -> Tuple[List[dict], List[str]]:
    """Return (servers, unreadable). A broken file is `cannot judge`, never `no servers`."""
    servers, unreadable = [], []
    for agent, rel, fmt in SOURCES:
        path = home / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            data = parse_toml(text) if fmt == "toml" else json.loads(text or "{}")
            if not isinstance(data, dict):
                raise ValueError("not a table")
            found = _servers_in(data, fmt)
            if not isinstance(found, dict):
                raise ValueError("server list is not a table")
        except (ValueError, OSError) as exc:
            unreadable.append("%s: %s" % (rel, str(exc)[:80]))
            continue
        for name, spec in found.items():
            servers.append(_classify(agent, name, spec, rel))
        if fmt == "claude":  # per-project servers live in the same file
            for folder, block in (data.get("projects") or {}).items():
                if isinstance(block, dict):
                    for name, spec in (block.get("mcpServers") or {}).items():
                        servers.append(_classify(agent, name, spec, "project " + os.path.basename(folder)))
    if project and (project / ".mcp.json").is_file():
        try:
            data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
            for name, spec in (data.get("mcpServers") or {}).items():
                servers.append(_classify("claude", name, spec, ".mcp.json"))
        except (ValueError, OSError, AttributeError) as exc:
            unreadable.append(".mcp.json: %s" % str(exc)[:80])
    return servers, unreadable


def fingerprint(server: dict) -> Optional[str]:
    """Most distinctive token of a server's command, used to find its live processes.

    An interpreter name is never a fingerprint: matching "node" would count every
    unrelated node process as a copy of this server.
    """
    picks = []
    for token in [server.get("command") or ""] + list(server.get("args") or []):
        token = str(token).strip()
        if token.startswith("-"):
            if picks:
                break  # what follows the program is its options, such as a port number
            continue
        base = procs.script_label([token])
        if not token or base.lower() in _TOKEN_SKIP or base.isdigit() or len(base) < 4:
            continue
        picks.append(base)
    return picks[-1] if picks else None


def measure(servers: List[dict], snapshot: List[procs.Proc]) -> int:
    """Fill copies and RSS from live processes. Returns how many servers could not be measured."""
    blind = 0
    for server in servers:
        if server["kind"] != "per-session":
            continue
        mark = fingerprint(server)
        if not mark:
            blind += 1
            continue
        hits = [p for p in snapshot if mark in p.cmd]
        server["copies"] = len(hits)
        server["rss_mb"] = sum(p.rss_mb for p in hits)
    return blind


def remedy(server: dict) -> str:
    template = REMEDY.get(server["agent"], 'set "disabled": true for {n} in {src}, or remove it')
    return template.format(n=server["name"], src=server["source"])


def audit(home: Optional[Path] = None, project: Optional[Path] = None,
          snapshot: Optional[List[procs.Proc]] = None) -> dict:
    home = home or Path.home()
    servers, unreadable = collect(home, project or Path.cwd())
    live = procs.snapshot() if snapshot is None else snapshot
    blind = measure(servers, live) if live is not None else None
    running = [p for p in (live or []) if procs.is_mcp(p)]
    return {"servers": servers, "unreadable": unreadable, "blind": blind,
            "sessions": procs.count_sessions(live) if live is not None else None,
            "mcp_processes": len(running), "mcp_rss_mb": sum(p.rss_mb for p in running),
            "per_session": [s for s in servers if s["kind"] == "per-session"]}
