# 1. One MCP server, one copy per session

A server declared with `command` is stdio: the agent starts it for each session or thread. A server declared with `url` is one process you run once.

```bash
agent-slowdown mcp
```

This reads the configs of Claude Code, Codex, Gemini CLI, Antigravity, Cursor, Windsurf and opencode, and counts the live processes for each stdio server. A broken config is reported as "cannot judge", never as clean. Servers the Codex desktop app installs for itself are not counted against you.

## What we measured

| Machine | Server | Sessions | Processes | Summed memory |
|---------|--------|----------|-----------|---------------|
| 32 GB Intel Mac | chrome-devtools-mcp | 26 | 84 | 2012 MB |
| Windows desktop | a local screen-history MCP | 56 | 176 | 6955 MB |
| Both | a server over HTTP | any | 1 | 42 MB |

The Mac went into swap with 57 MB free. That server had 83 real tool calls in 14 days. On the Windows machine no workflow called the stdio server at all: every use went to the same program's REST API.

So before removing a server, count real tool calls in your transcripts. Searching for its name overstates usage about 40 times, because the deferred tool list repeats every name in every session.

## The fix

1. Remove what you do not use daily: `claude mcp remove <name>`. Add it back when needed with `claude mcp add <name> -- <command>`.
2. For what you do use, run one server and point every client at its URL. Autostart templates, a watchdog and 13 gotchas: [mcp-daemon-diet](https://github.com/tonydzi/mcp-daemon-diet).

## What does not work (Claude Code 2.1.x, checked)

- `claude mcp disable` does not exist.
- The config has no lazy, on-demand or autostart key. The request is open: [claude-code#63251](https://github.com/anthropics/claude-code/issues/63251).
- Deferred tools (ToolSearch) save context, not the process.

## Other people's numbers

Codex desktop: 1300+ zombie processes and 37 GB after MCP children were not reaped: [codex#12491](https://github.com/openai/codex/issues/12491).
