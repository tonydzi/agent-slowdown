# agent-slowdown

**Your Mac or PC crawls when Claude Code, Codex, Cursor and Antigravity run side by side. This finds out why, and fixes the parts that can be fixed.**

`top` makes this hard to see. It lists one row per process, so forty 90 MB copies of the same helper rarely reach the first screen. This tool sums by name.

```bash
pipx run --spec git+https://github.com/tonydzi/agent-slowdown agent-slowdown
```

Python 3.9+, no dependencies, macOS, Linux and Windows. That command is read-only: nothing is stopped or changed unless you pass `--kill`.

```text
== Biggest groups ==
  memory  copies  cpu%  name
  3.8 GB      10    15  claude
  2.3 GB      31     4  ChatGPT

== Orphans ==
Orphaned agent helpers (parent gone, older than 10 min): 3, 3 MB
  29462    1 MB     0  82.9h  /bin/zsh -c source ~/.claude/shell-snapshots/snapshot-zsh-1...
```

Those three were real, found on our own machine: wait loops left by dead sessions, still polling every 15 seconds for a file that would never appear, 88 hours later.

## Six causes, what helps, and how sure we are

| # | Cause | Command | What helps | Status |
|---|-------|---------|------------|--------|
| 1 | A stdio MCP server starts a copy in every session | `agent-slowdown mcp` | Remove it, or run one shared HTTP server | Fix works, measured |
| 2 | Helpers outlive the session that started them | `agent-slowdown orphans` | Stop those whose parent died | Fix works, `--kill` needs your review |
| 3 | The agent runs `find /` or `rg x ~` | `agent-slowdown guard` | A hook that refuses it before it starts | Fix works, live in Claude Code |
| 4 | Electron and desktop apps grow | `agent-slowdown apps` | Restart the app. **There is no other cure** | Report only |
| 5 | The Linux memory limit is silently off | `agent-slowdown memlimit` | Start Claude in a user scope | Fix works, measured on Linux |
| 6 | Spotlight and antivirus scan agent folders | `agent-slowdown exclusions` | `.noindex` folders; Defender exclusions are your call | Spotlight measured, Defender not |

Details and numbers: [MCP copies](docs/01-mcp-copies.md) · [orphans](docs/02-orphans.md) · [broad searches](docs/03-broad-searches.md) · [desktop apps](docs/04-desktop-apps.md) · [Linux memory limit](docs/05-linux-memory-limit.md) · [indexers](docs/06-indexers.md)

## The one that costs the most

```text
one stdio MCP server, 26 sessions on a 32 GB Mac:      84 processes, 2.0 GB
one stdio MCP server, 56 sessions on a Windows box:   176 processes, 7.0 GB
the same kind of server over HTTP, any session count:   1 process,  42 MB
```

Summed memory over-counts pages shared between copies. The copy count is exact.

There is no lazy flag for this in Claude Code. Deferred tools do not save the process, because it has already started by then. Lazy means the server is not in the always-on config. The other half of the fix, running one shared server that survives reboots, has its own repository: [mcp-daemon-diet](https://github.com/tonydzi/mcp-daemon-diet).

## Stop broad searches before they start

The hook needs the command to stay on your PATH, so install it first:

```bash
pipx install git+https://github.com/tonydzi/agent-slowdown
agent-slowdown guard --print-config
```

Paste the result into `~/.claude/settings.json`. Blocked commands exit 2, and the agent reads the reason and retries with a narrower path. It reads the command as text and never runs it. It is a seatbelt against the accidental command, not a sandbox.

## What this does not do

- It does not make your agents faster. It removes what is slowing the machine down around them.
- It cannot shrink a desktop app's memory.
- It has no idea how many agents your machine can take. Nobody has a good number; our rough figure is 1 to 1.5 GB per active session with all its helpers.
- Windows process reading runs in CI, but we have used `orphans --kill` on macOS and Linux test processes only.

Exit codes: `0` nothing found, `1` found something, `2` could not read the process list, `3` a config file could not be read. A check that could not look says so; it never reports clean.

## Who made this

Built while running a fleet of coding agents across several machines, and paying for each of these lessons with a frozen computer. Claude wrote most of it. Codex and GLM reviewed it and found real bugs, which are fixed. Measurements are ours unless linked.

If it found something on your machine, an issue with the output helps more than a star.

TonyDzi, Palo Alto AI Research Lab · second brain, agent coordination, persistent memory: [github.com/tonydzi](https://github.com/tonydzi) · WhatsApp +1 341 222 9178

MIT license.
