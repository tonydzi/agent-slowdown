# Changelog

## 0.1.1

`orphans` now needs a sum of evidence: your own process, one hour old, not paused, not listening on a port, and its session not alive. Doubtful cases are listed and left alone. The default minimum age went from 10 to 60 minutes.

## 0.1.0

First release: `top`, `mcp`, `orphans`, `guard`, `apps`, `memlimit`, `exclusions`, and `doctor` to run the read-only checks together.
