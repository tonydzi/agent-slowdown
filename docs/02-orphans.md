# 2. Orphans: helpers whose session died

```bash
agent-slowdown orphans          # shows what it would stop
agent-slowdown orphans --kill   # stops them
```

The test is "the parent died", not "it has been idle". A helper with a dead parent has nobody left to answer to, however busy it looks. An idle helper with a live parent may be waiting for the next turn.

But PPID 1 proves nothing by itself. On one working Mac 436 processes had it, and 309 of those were launchd services. So it takes a sum of evidence, and doubt is reported instead of acted on.

## What we measured

- 172 orphaned Python MCP servers plus about 30 sessions two to four days old filled 14.5 of 15 GB of swap on a Mac. Stopping them took free RAM from 54% to 77%.
- `agent-slowdown orphans` on a working Mac found three shells left by dead sessions, 83 to 88 hours old. They were loops like `until grep -q done log; do sleep 15; done`, waiting for a file that would never appear. They use almost no memory. They are in the list because they prove the session is gone and nothing cleaned up after it.

## What counts

A process is stopped only if all of these hold:

1. It is yours and older than an hour (`--min-age`, in minutes).
2. It is a known agent helper: an MCP server, ripgrep, find, a language server, `npx`, a script in Claude Code's scratch folder, or a shell the agent's Bash tool started (recognised by the `shell-snapshots` file it sources).
3. Its parent is gone: re-parented to init or `systemd --user` on macOS and Linux, parent PID missing or newer than the child on Windows.
4. No service manager owns it. A shared MCP daemon under launchd or systemd also has PPID 1, and it is exactly the thing you set up to fix cause 1.
5. It is not paused and not listening on a port.
6. The session that started it is not still alive, judged by `CLAUDE_PID` in its environment, by a live agent started at the same moment as its shell snapshot, and by a recently written transcript.
7. Its command line does not match `~/.config/agent-slowdown/allow.txt`.

A process's arguments never qualify it, so `vim notes-mcp.md` is safe, and a plain `python backup.py` or `nohup ffmpeg` is never a candidate. If the tool cannot tell what launchd manages, cannot see listening ports or cannot read your allow file, it stops nothing it is unsure of.

The limit of this check: once the parent is gone, nothing proves an agent started the process. A helper you detached by hand, such as `nohup rg ... &`, looks the same. Read the list first, and put part of such a command line in `allow.txt`.

## How it stops them

SIGTERM, a five second wait, then SIGKILL, because orphans often ignore SIGTERM. The process is re-read before each signal, so a recycled PID is left alone (a very small window remains between the check and the signal). On Windows it uses `taskkill /T /F`.

## How it did on our machines

This package is a smaller version of the check we run on our own machines, which also samples CPU over time. That one ran in shadow mode, logging what it would stop, for 76 runs on one Mac: 4 targets, no false ones. When it went live it stopped 3 processes on that Mac and 2 on another, with no refusals, and found nothing to stop on a Linux server. A review by another model found 8 defects first; a person's long-running `nohup ffmpeg` would have died without one of the fixes.

Do the same here: run it without `--kill` for a while and read the list.

## Zombies

State `Z` processes hold no memory and cannot be killed. Only their parent can clear them, so they are counted but never touched.
