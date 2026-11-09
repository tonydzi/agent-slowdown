# 2. Orphans: helpers whose session died

```bash
agent-slowdown orphans          # shows what it would stop
agent-slowdown orphans --kill   # stops them
```

The test is "the parent died", not "it has been idle". A helper with a dead parent has nobody left to answer to, however busy it looks. An idle helper with a live parent may be waiting for the next turn.

## What we measured

- 172 orphaned Python MCP servers plus about 30 sessions two to four days old filled 14.5 of 15 GB of swap on a Mac. Stopping them took free RAM from 54% to 77%.
- `agent-slowdown orphans` on a working Mac found three shells left by dead sessions, 83 to 88 hours old. They were loops like `until grep -q done log; do sleep 15; done`, waiting for a file that would never appear. They use almost no memory. They are in the list because they prove the session is gone and nothing cleaned up after it.

## What counts

A process is stopped only if all of these hold:

1. It is a known agent helper: an MCP server, ripgrep, find, a language server, `npx`, or a shell the agent's Bash tool started (recognised by the `shell-snapshots` file it sources).
2. Its parent is gone: re-parented to init or `systemd --user` on macOS and Linux, parent PID missing or newer than the child on Windows.
3. No service manager owns it. A shared MCP daemon under launchd or systemd also has PPID 1, and it is exactly the thing you set up to fix cause 1.
4. It is older than 10 minutes (`--min-age`).
5. Its command line does not match `~/.config/agent-slowdown/allow.txt`.

A process's arguments never qualify it, so `vim notes-mcp.md` is safe. If the tool cannot tell what launchd manages, or cannot read your allow file, it refuses to stop anything.

The limit of this check: once the parent is gone, nothing proves an agent started the process. Something you detached by hand, such as `nohup rg ... &` or a daemon you started yourself, looks the same. Read the list first, and put part of such a command line in `allow.txt`.

## How it stops them

SIGTERM, a five second wait, then SIGKILL, because orphans often ignore SIGTERM. The process is re-read before each signal, so a recycled PID is left alone (a very small window remains between the check and the signal). On Windows it uses `taskkill /T /F`.

We run our own version of this check in shadow mode for one class of orphan: it logs what it would stop, and a person reviews the log. Do the same at first and read the list before you add `--kill`.

## Zombies

State `Z` processes hold no memory and cannot be killed. Only their parent can clear them, so they are counted but never touched.
