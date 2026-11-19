# Changelog

## 0.2.0

New `hooks` command, also part of `doctor`: counts what Claude Code, Codex, Gemini CLI and Cursor hooks launch on each tool call, prompt and session start, times bare interpreter start-up, and flags shell wrappers, npx, missing matchers, scripts that spawn more processes, large Python entry files and duplicates. It reads configs and never runs a hook. See docs/07-hooks.md.

## 0.1.1

`orphans` now needs a sum of evidence: your own process, one hour old, not paused, not listening on a port, and its session not alive. Doubtful cases are listed and left alone. The default minimum age went from 10 to 60 minutes.

## 0.1.0

First release: `top`, `mcp`, `orphans`, `guard`, `apps`, `memlimit`, `exclusions`, and `doctor` to run the read-only checks together.
