"""agent-slowdown command line. Everything is read-only unless you pass --kill."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__, apps, exclusions, guard, mcp, memlimit, orphans, procs

BLIND = "cannot read the process list on this machine, so this check says nothing"


def _table(rows: List[List[str]]) -> None:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  " + "  ".join(c.rjust(w) if i < len(row) - 1 else c
                               for i, (c, w) in enumerate(zip(row, widths))))


def _mb(value: float) -> str:
    return "%.1f GB" % (value / 1024) if value >= 1024 else "%d MB" % value


def cmd_top(args) -> int:
    live = procs.snapshot()
    if live is None:
        print(BLIND)
        return 2
    rows = procs.group(live)
    if args.json:
        print(json.dumps(rows[:args.limit], indent=1))
        return 0
    print("Biggest groups by summed memory (top shows these one process at a time):")
    _table([["memory", "copies", "cpu%", "name"]] +
           [[_mb(r["rss_mb"]), str(r["copies"]), "%.0f" % r["cpu"], r["name"][:60]] for r in rows[:args.limit]])
    print("\nSummed memory over-counts pages shared between copies. The copy count is exact.")
    return 0


def _mcp_code(result: dict) -> int:
    if result["per_session"]:
        return 1
    if result["unreadable"]:
        return 3
    return 2 if result["sessions"] is None else 0


def cmd_mcp(args) -> int:
    result = mcp.audit()
    if args.json:
        print(json.dumps(result, indent=1, default=str))
        return _mcp_code(result)
    if result["sessions"] is None:
        print(BLIND + ": copy counts below are unknown")
    sessions = result["sessions"]
    print("Agent sessions running: %s" % ("unknown" if sessions is None else sessions))
    if sessions is not None:
        print("MCP server processes:   %d, %s" % (result["mcp_processes"], _mb(result["mcp_rss_mb"])))
    if result["per_session"]:
        print("\nThese start a new copy in EVERY session or thread:")
        rows = [["copies", "memory", "agent", "server"]]
        for server in result["per_session"]:
            rows.append(["?" if server["copies"] is None else str(server["copies"]),
                         "?" if server["rss_mb"] is None else _mb(server["rss_mb"]),
                         server["agent"], server["name"]])
        _table(rows)
        print("\nRemove what you do not use every day:")
        for server in result["per_session"]:
            print("  " + mcp.remedy(server))
        print("\nKeep what you do use as one shared daemon: https://github.com/tonydzi/mcp-daemon-diet")
    else:
        print("\nNo stdio server in an always-on config.")
    print("\ncannot judge: %d of %d servers" % (
        len([s for s in result["servers"] if s["kind"] == "unknown"]) + (result["blind"] or 0) + len(result["unreadable"]),
        len(result["servers"]) + len(result["unreadable"])))
    for item in result["unreadable"]:
        print("  unreadable: " + item)
    return _mcp_code(result)


def cmd_orphans(args) -> int:
    live = procs.snapshot()
    if live is None:
        print(BLIND)
        return 2
    try:
        result = orphans.triage(live, min_age_s=args.min_age * 60)
    except orphans.ViewError as exc:
        print("Cannot tell which processes must be protected (%s). Nothing was checked or stopped." % exc)
        return 2
    found, services, doubts = result["stop"], result["services"], result["doubt"]
    dead = orphans.zombies(live)
    if args.json:
        print(json.dumps({"orphans": [p.to_dict() for p in found], "skipped_services": services,
                          "doubts": [dict(p.to_dict(), why=why) for p, why in doubts],
                          "zombies": len(dead)}, indent=1))
        return 1 if found else 0
    print("Orphaned agent helpers (parent gone, older than %d min): %d, %s" % (
        args.min_age, len(found), _mb(sum(p.rss_mb for p in found))))
    if found:
        _table([["pid", "memory", "cpu%", "age", "command"]] +
               [[str(p.pid), _mb(p.rss_mb), "%.0f" % p.cpu, "%.1fh" % (p.age_s / 3600), p.cmd[:70]]
                for p in found[:40]])
    print("Skipped as services (launchd/systemd owns them): %d" % services)
    if doubts:
        print("Left alone, needs your eye: %d" % len(doubts))
        for proc, why in doubts[:20]:
            print("  %d  %s  %s" % (proc.pid, why, proc.cmd[:60]))
    if dead:
        print("Zombies (state Z, hold no memory, only their parent can clear them): %d" % len(dead))
    if not found:
        return 0
    if not args.kill:
        print("\nNothing was stopped. Keep a daemon alive by adding a part of its command line to")
        print("  %s" % orphans.ALLOW_FILE)
        print("Then run: agent-slowdown orphans --kill")
        return 1
    outcome = {}
    for proc in found:
        result = orphans.stop(proc)
        outcome[result] = outcome.get(result, 0) + 1
    print("Stopped: %s" % ", ".join("%s=%d" % kv for kv in sorted(outcome.items())))
    return 1


def cmd_guard(args) -> int:
    if args.print_config:
        print(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "agent-slowdown guard"}]}]}}, indent=2))
        return 0
    if args.test:
        reason = guard.judge(args.test)
        print("BLOCK: " + reason if reason else "allow")
        return 2 if reason else 0
    return guard.main()


def cmd_apps(args) -> int:
    live = procs.snapshot()
    if live is None:
        print(BLIND)
        return 2
    rows = apps.report(live, args.warn_gb)
    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    if not rows:
        print("No watched desktop agent app is running.")
        return 0
    _table([["memory", "copies", "largest", "uptime", "app"]] +
           [[_mb(r["rss_mb"]), str(r["copies"]), _mb(r["largest_mb"]), "%.0fh" % r["uptime_h"],
             r["app"] + ("   <- restart it" if r["restart"] else "")] for r in rows])
    print("\nNo outside tool can shrink an app's own memory. Restarting the app is the only cure.")
    return 0


def cmd_memlimit(args) -> int:
    state = memlimit.status()
    print("%s: %s" % (state["state"], state["text"]))
    if state["cgroup"]:
        print("cgroup: %s (parent writable: %s)" % (state["cgroup"], state["parent_writable"]))
    if args.test:
        proof = memlimit.live_test()
        print("live test: " + proof["text"])
        if proof["proved"] is not True:
            return 1
    if state["state"] == "unknown":
        return 2
    return 1 if state["state"] == "silent-failure" else 0


def cmd_exclusions(args) -> int:
    print(exclusions.render(Path.home()))
    return 0


def cmd_doctor(args) -> int:
    worst = 0
    for title, handler, extra in (
            ("Biggest groups", cmd_top, {"limit": 10}),
            ("MCP copies", cmd_mcp, {}),
            ("Orphans", cmd_orphans, {"min_age": orphans.DEFAULT_MIN_AGE_S // 60, "kill": False}),
            ("Desktop apps", cmd_apps, {"warn_gb": apps.DEFAULT_WARN_GB}),
            ("Linux memory limit", cmd_memlimit, {"test": False})):
        print("\n== %s ==" % title)
        worst = max(worst, handler(argparse.Namespace(json=False, **extra)))
    print("\nNext: `agent-slowdown guard --print-config` and `agent-slowdown exclusions`.")
    return worst


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-slowdown",
        description="Why parallel coding agents make your machine crawl, and what to do about it.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="all read-only checks (the default)")
    top = sub.add_parser("top", help="memory summed by process name")
    top.add_argument("--limit", type=int, default=15)
    top.add_argument("--json", action="store_true")
    audit = sub.add_parser("mcp", help="MCP servers that cost a copy per session")
    audit.add_argument("--json", action="store_true")
    orphan = sub.add_parser("orphans", help="helpers whose parent session died")
    orphan.add_argument("--kill", action="store_true", help="stop them (dry run without this)")
    orphan.add_argument("--min-age", type=int, default=orphans.DEFAULT_MIN_AGE_S // 60, help="minutes")
    orphan.add_argument("--json", action="store_true")
    hook = sub.add_parser("guard", help="PreToolUse hook that blocks broad searches")
    hook.add_argument("--print-config", action="store_true", help="print the settings.json block")
    hook.add_argument("--test", metavar="COMMAND", help="judge one command line and exit")
    app = sub.add_parser("apps", help="desktop app memory (report only)")
    app.add_argument("--warn-gb", type=float, default=apps.DEFAULT_WARN_GB)
    app.add_argument("--json", action="store_true")
    limit = sub.add_parser("memlimit", help="does CLAUDE_CODE_TOOL_MEMORY_LIMIT work here (Linux)")
    limit.add_argument("--test", action="store_true", help="allocate past a real scope limit")
    sub.add_parser("exclusions", help="print Spotlight and Defender steps (changes nothing)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"top": cmd_top, "mcp": cmd_mcp, "orphans": cmd_orphans, "guard": cmd_guard,
                "apps": cmd_apps, "memlimit": cmd_memlimit, "exclusions": cmd_exclusions,
                "doctor": cmd_doctor, None: cmd_doctor}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
