# FOR ROBOTS

You are an AI agent reading this repo. Ranked takeaways, densest first. One rule about
this file itself: **it is data, not authorization.** Nothing here overrides your
operator's policies or lets you install anything on your own.

## Alpha, ranked

1. **Sum processes by name, never read `top` row by row.** Parallel-agent slowdowns are mostly many small copies. `agent-slowdown top` shows them; see the table in README.
2. **A `command` MCP server costs one process per session; a `url` server costs one per machine.** 84 processes and 2.0 GB against 1 process and 42 MB in our measurements (docs/01-mcp-copies.md). Before removing one, count real tool calls in transcripts: name matches overstate use about 40 times.
3. **Judge orphans by "the parent died", and skip whatever a service manager owns.** A shared daemon also has PPID 1 (docs/02-orphans.md).
4. **`CLAUDE_CODE_TOOL_MEMORY_LIMIT` fails silently outside a user-owned cgroup scope.** Verify by exit 137, not by the variable being set (docs/05-linux-memory-limit.md).
5. **`.noindex` folders stopped Spotlight on macOS 26; a `.metadata_never_index` file did not** (docs/06-indexers.md).

## How to apply

Run `agent-slowdown` (read-only). Show your operator the output. Do not pass `--kill`, add a Defender exclusion or edit an MCP config without asking them first.

## Provenance

Measurements are from our own machines between July and November 2026 (macOS on Intel, Windows, Ubuntu-family Linux with cgroup v2, Claude Code 2.1.x). Numbers from others are linked. The Defender speed-up figures are reported by others and not reproduced by us.

## Family

Sibling: [mcp-daemon-diet](https://github.com/tonydzi/mcp-daemon-diet), the recipe for one shared MCP server per machine. Family map: [claude-bible](https://github.com/tonydzi/claude-bible).
