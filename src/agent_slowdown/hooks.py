"""What your agent hooks cost before they do any work.

Every hook is a new process. Claude Code, Codex, Gemini CLI and Cursor start it on every
matching event: each tool call, each prompt, each session start. Six months of adding
guards ends with dozens of processes per tool call, and the machine crawls while every
single hook looks harmless on its own.

This module never runs your hooks. It reads the config files, counts what each event
starts, times only the bare interpreters (`python3 -c pass`, `node -e 0`, `bash -c true`),
and reads hook scripts as text to spot the usual traps.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

# (agent, path, format). Paths starting with "./" are relative to the current directory.
SOURCES = [
    ("claude", "~/.claude/settings.json", "nested"),
    ("claude", "~/.claude/settings.local.json", "nested"),
    ("claude", "./.claude/settings.json", "nested"),
    ("claude", "./.claude/settings.local.json", "nested"),
    ("codex", "~/.codex/hooks.json", "nested"),
    ("codex", "./.codex/hooks.json", "nested"),
    ("gemini-cli", "~/.gemini/settings.json", "nested"),
    ("gemini-cli", "./.gemini/settings.json", "nested"),
    ("cursor", "~/.cursor/hooks.json", "flat"),
    ("cursor", "./.cursor/hooks.json", "flat"),
]
PLUGIN_GLOB = ("~/.claude/plugins", "hooks/hooks.json")

# Which events fire how often. Anything else is reported but not summed.
PER_TOOL = {"PreToolUse", "PostToolUse", "BeforeTool", "AfterTool", "beforeShellExecution",
            "afterShellExecution", "beforeMCPExecution", "afterMCPExecution", "beforeReadFile",
            "afterFileEdit"}
PER_PROMPT = {"UserPromptSubmit", "BeforeAgent", "beforeSubmitPrompt"}
PER_SESSION = {"SessionStart"}

SHELLS = {"bash", "sh", "zsh", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}
INTERPRETERS = {"python", "python3", "python.exe", "py", "node", "node.exe", "npx", "uv", "uvx",
                "bun", "deno", "ruby", "perl"}
SCRIPT_EXT = (".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".rb")
SPAWN = re.compile(r"\bsubprocess\.|\bPopen\(|\bos\.system\(|\bos\.popen\(|child_process|"
                   r"\bexecSync\(|\bspawnSync\(|\bspawn\(|\$\(|Start-Process")
BIG_MAIN_LINES = 400
PROBE = {"python": ["-c", "pass"], "node": ["-e", "0"], "bun": ["-e", "0"], "deno": ["eval", "0"],
         "bash": ["-c", "true"], "sh": ["-c", "true"], "zsh": ["-c", "true"]}


def _expand(path: str, home: Path, cwd: Path) -> Path:
    if path.startswith("~/"):
        return home / path[2:]
    if path.startswith("./"):
        return cwd / path[2:]
    return Path(path)


def _entries(data: dict, fmt: str):
    """Yield (event, matcher, hook dict) from one config file."""
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return
    for event, items in hooks.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if fmt == "nested" and isinstance(item.get("hooks"), list):
                for hook in item["hooks"]:
                    if isinstance(hook, dict) and hook.get("command"):
                        yield event, str(item.get("matcher") or ""), hook
            elif item.get("command"):
                yield event, str(item.get("matcher") or ""), item


def enabled_plugins(home: Path, cwd: Path) -> List[Path]:
    """hooks.json of Claude Code plugins switched on in settings. The plugins folder also
    holds marketplace catalogs and old cached versions, which never run."""
    state: Dict[str, bool] = {}
    for agent, path, _ in SOURCES:  # listed from weakest to strongest, so later files win
        if agent != "claude":
            continue
        try:
            data = json.loads(_expand(path, home, cwd).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
        if isinstance(plugins, dict):
            state.update({k: bool(v) for k, v in plugins.items()})
    root = _expand(PLUGIN_GLOB[0], home, cwd)
    found = []
    for full in sorted(k for k, v in state.items() if v):
        name, _, market = full.partition("@")
        hits = [p for p in root.glob("**/%s/**/%s" % (name, PLUGIN_GLOB[1]))
                if not market or market in p.parts]
        installed = [p for p in hits if "cache" in p.parts]  # the installed copy runs, the catalog does not
        pool = installed or hits
        if pool:
            found.append(max(pool, key=lambda p: p.stat().st_mtime if p.exists() else 0))
    return found


def load(home: Path, cwd: Path) -> dict:
    """All hooks we can find. Unreadable files are listed, never silently skipped."""
    files = [(a, _expand(p, home, cwd), f) for a, p, f in SOURCES]
    files += [("claude-plugin", p, "nested") for p in enabled_plugins(home, cwd)]
    seen, found, unreadable = set(), [], []
    for agent, path, fmt in files:
        try:
            real = path.resolve()
        except OSError:
            real = path
        if real in seen or not path.is_file():
            continue
        seen.add(real)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            unreadable.append("%s (%s)" % (path, exc.__class__.__name__))
            continue
        for event, matcher, hook in _entries(data, fmt):
            found.append({"agent": agent, "file": str(path), "event": event, "matcher": matcher,
                          "command": str(hook["command"]), "timeout": hook.get("timeout")})
    return {"hooks": found, "unreadable": unreadable}


def _words(command: str) -> List[str]:
    try:
        words = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        words = command.split()
    return [w.strip("\"'") for w in words]  # posix=False keeps the quotes on Windows


def _base(word: str) -> str:
    return os.path.basename(word.strip("\"'")).lower()


_INTERP_NAME = re.compile(r"^(?:python(?:\d+(?:\.\d+)?)?|py|node|bun|deno|bash|zsh|sh)(?:\.exe)?$")
_QUOTED = re.compile(r""""[^"]*"|'[^']*'""")


def _kind(name: str) -> Optional[str]:
    """Interpreter family of a bare program name, or None. Script names never match."""
    if not _INTERP_NAME.match(name):
        return None
    name = name[:-4] if name.endswith(".exe") else name
    return "python" if name.startswith("py") else name


def processes(command: str) -> int:
    """Lower bound of programs one run starts: one per pipeline or && segment outside
    quotes, plus every shell or interpreter inside a quoted payload such as bash -c "...".
    The shell the agent itself uses to run the line is not counted."""
    words = _words(command)
    if not words:
        return 0
    nested = sum(1 for w in words[1:] if re.search(r"\s", w)
                 for part in re.split(r"[\s;|&]+", w) if _base(part) in SHELLS | INTERPRETERS)
    bare = _QUOTED.sub("", command)
    segments = len(re.findall(r"\|\||&&|(?<![|&])\|(?![|&])|;", bare))
    return 1 + segments + nested


def interpreter(command: str) -> Optional[str]:
    words = _words(command)
    for word in words[:3]:
        kind = _kind(_base(word))
        if kind:
            return kind
    if words and words[0].endswith(".py"):
        return "python"
    if words and words[0].endswith(".sh"):
        return "bash"
    return None


def script_of(command: str, home: Path, cwd: Path) -> Optional[Path]:
    for word in _words(command):
        word = word.replace("$HOME", str(home)).replace("${HOME}", str(home))
        word = re.sub(r"\$\{?CLAUDE_PROJECT_DIR\}?", lambda _m: str(cwd), word)
        if word.lower().endswith(SCRIPT_EXT):
            path = Path(os.path.expanduser(word))
            if not path.is_absolute():
                path = cwd / path
            if path.is_file():
                return path
    return None


def matches_bash(matcher: str) -> bool:
    if matcher in ("", "*"):
        return True
    try:
        return re.fullmatch(matcher, "Bash") is not None or re.fullmatch(matcher, "run_shell_command") is not None
    except re.error:
        return "Bash" in matcher


def findings(hook: dict, home: Path, cwd: Path) -> List[str]:
    out, command = [], hook["command"]
    raw = _words(command)
    words = [_base(w) for w in raw]
    payload = _words(raw[2]) if len(raw) > 2 and words[1] in ("-c", "/c", "-command") else []
    if words and words[0] in SHELLS and payload and _base(payload[0]) in INTERPRETERS:
        out.append("a shell only to start another program: call the program directly, one process less")
    if "npx" in words or "uvx" in words:
        out.append("npx/uvx resolves the package on every run: install it once and call the binary")
    if hook["event"] in PER_TOOL and hook["matcher"] in ("", "*") and hook["agent"] != "cursor":
        out.append("no matcher: runs on every tool call, reads too; narrow it to the tools it guards")
    script = script_of(command, home, cwd)
    if script is not None:
        try:
            text = script.read_text(encoding="utf-8", errors="replace")[:400000]
        except OSError:
            text = ""
        if SPAWN.search(text):
            out.append("the script can start more processes itself: each one is paid on every event")
        lines = text.count("\n")
        if script.suffix == ".py" and lines > BIG_MAIN_LINES:
            out.append("%d-line Python entry file is compiled again on every run (Python caches imported "
                       "modules, never the main file), about 2 ms per 800 lines: small, but paid on every event; "
                       "keep the entry tiny and import the rest" % lines)
    return out


def duplicates(hooks: List[dict]) -> List[dict]:
    seen: Dict[tuple, int] = {}
    for hook in hooks:
        key = (hook["agent"].replace("-plugin", ""), hook["event"], hook["matcher"], hook["command"].strip())
        seen[key] = seen.get(key, 0) + 1
    return [{"agent": k[0], "event": k[1], "command": k[3], "times": n} for k, n in seen.items() if n > 1]


def _resolve(kind: str, cwd: Path) -> Optional[str]:
    """The interpreter on PATH, never a path taken from a hook: a hook's own program
    could be the hook itself, and timing must never run a hook."""
    names = {"python": ("python3", "python")}.get(kind, (kind,))
    for name in names:
        found = shutil.which(name)
        if not found:
            continue
        # A project folder on PATH could hold a fake "python3"; home is fine (pyenv, nvm).
        project = cwd.resolve()
        if project != Path.home().resolve() and Path(found).resolve().is_relative_to(project):
            continue
        return found
    return None


def _clean_env() -> Dict[str, str]:
    """Only what an interpreter needs to start, so BASH_ENV, NODE_OPTIONS and
    PYTHONSTARTUP cannot pull in anyone's code during the probe."""
    keep = ("PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "HOME", "USERPROFILE")
    return {k: v for k, v in os.environ.items() if k in keep}


def startup_ms(kinds, cwd: Path, runs: int = 3) -> Dict[str, Optional[float]]:
    """Median wall time of a bare interpreter start. None = could not measure."""
    result: Dict[str, Optional[float]] = {}
    for kind in sorted(kinds):
        exe = _resolve(kind, cwd)
        if exe is None or kind not in PROBE:
            result[kind] = None
            continue
        times = []
        for _ in range(runs):
            start = time.perf_counter()
            try:
                subprocess.run([exe] + PROBE[kind], capture_output=True, timeout=30,
                               env=_clean_env(), cwd=str(Path.home()))
            except (OSError, subprocess.SubprocessError):
                times = []
                break
            times.append((time.perf_counter() - start) * 1000)
        result[kind] = sorted(times)[len(times) // 2] if times else None
    return result


def audit(home: Optional[Path] = None, cwd: Optional[Path] = None, measure: bool = True) -> dict:
    home, cwd = home or Path.home(), cwd or Path.cwd()
    loaded = load(home, cwd)
    hooks = loaded["hooks"]
    kinds = {k for k in (interpreter(h["command"]) for h in hooks) if k}
    starts = startup_ms(kinds, cwd) if measure else {}
    for hook in hooks:
        hook["processes"] = processes(hook["command"])
        hook["interpreter"] = interpreter(hook["command"])
        hook["findings"] = findings(hook, home, cwd)
    per_agent: Dict[str, dict] = {}
    for hook in hooks:
        agent = hook["agent"].replace("-plugin", "")
        row = per_agent.setdefault(agent, {"tool_call": [], "prompt": [], "session": [], "other": 0})
        if hook["event"] in PER_TOOL:
            if hook["agent"] == "cursor" or matches_bash(hook["matcher"]):
                row["tool_call"].append(hook)
        elif hook["event"] in PER_PROMPT:
            row["prompt"].append(hook)
        elif hook["event"] in PER_SESSION:
            row["session"].append(hook)
        else:
            row["other"] += 1
    summary = {}
    for agent, row in per_agent.items():
        summary[agent] = {"other_events": row["other"]}
        for moment in ("tool_call", "prompt", "session"):
            group = row[moment]
            known = [starts.get(h["interpreter"]) for h in group]
            summary[agent][moment] = {
                "hooks": len(group),
                "processes": sum(h["processes"] for h in group),
                "startup_ms": round(sum(ms for ms in known if ms)) if measure else None,
                "unknown_startup": sum(1 for ms in known if not ms),
            }
    return {"hooks": hooks, "summary": summary, "startup_ms": starts,
            "duplicates": duplicates(hooks), "unreadable": loaded["unreadable"]}
