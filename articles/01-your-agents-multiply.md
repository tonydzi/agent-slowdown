# My laptop wasn't slow. My agents were multiplying.

I run several coding agents at once: Claude Code in a few windows, Codex, sometimes Cursor and Antigravity. For weeks the machine felt like it was wading through mud. Activity Monitor showed nothing dramatic. No single process ate the CPU. So I assumed it was age, or Chrome, or bad luck.

It was none of those. It was copies.

## What `top` hides

`top` and Activity Monitor show one row per process. That is exactly the wrong view for this problem. A helper that weighs 90 MB never reaches the first screen. Forty copies of it weigh 3.6 GB, and they never reach the first screen either, because each one is still only 90 MB.

When I summed processes by name instead of reading them row by row, the picture changed in one second. On one Windows machine a single screen-history helper was running 176 times across 56 agent sessions, 7 GB in total. On a 32 GB Mac, a browser-automation helper was running 84 times across 26 sessions, 2 GB, and the machine had gone into swap with 57 MB free.

Summed memory over-counts a little, because copies share some pages. The copy count does not lie.

## Six causes, in the order they cost us

1. **A helper per session.** An MCP server configured with a `command` is started once for every agent session or thread. Configured with a `url`, it is one process for the whole machine. I wrote about this one in September: [Stop spawning an MCP server per agent session](https://dev.to/tonydzi/stop-spawning-an-mcp-server-per-agent-session-and-what-it-wont-fix-5e2m).
2. **Leftovers.** When a session ends, its helpers often stay. Some ignore the polite stop signal. One sweep found 172 orphaned helpers plus about 30 sessions two to four days old, with 14.5 of 15 GB of swap in use; stopping them took free memory from 54% to 77%.
3. **The agent searches the whole disk.** An agent looking for one file sometimes runs `find /` or `rg` over the home folder. On one Mac, `bfs` from the root ran at 530 to 640% CPU per process.
4. **Desktop apps grow.** Electron-based agent apps accumulate renderer memory over days.
5. **A memory limit that silently does nothing.** On Linux, Claude Code can cap the memory of its shell commands. In an ordinary login session the cap quietly fails to apply.
6. **Indexers.** Spotlight and antivirus scanners look at every small file agents write.

## The test that matters: did the parent die?

The tempting rule for leftovers is "kill anything idle". It is wrong both ways. An idle helper with a live parent may be waiting for its next turn. A busy helper with a dead parent has nobody left to answer to.

But "parent died" alone is not enough either. On one working Mac, 436 processes had the system as their parent, and 309 of those were ordinary services. So the check needs a sum of evidence: the process is yours, it is a known agent helper, nothing manages it as a service, it is not paused, it is not listening on a port, and the session that started it is gone. If any of that cannot be checked, the tool says so and stops nothing.

That last part was not my idea. A review by another model found that an earlier version would have killed a person's long-running `nohup ffmpeg`. A second model found that a shell script merely mentioning `grep` would have counted as a helper. Both were fixed before release.

The most surprising find was small. My own sessions had left behind shells running loops like `until grep -q done log; do sleep 15; done`, waiting for a file that would never appear. They had been polling every 15 seconds for 88 hours. They used almost no memory. They were evidence that nothing cleans up after a session ends.

## What I shipped

I turned all of this into one read-only command:

```bash
pipx run agent-slowdown
```

It sums memory by name, lists per-session MCP copies across Claude Code, Codex, Cursor, Gemini CLI, Antigravity, Windsurf and opencode, shows orphaned helpers, and checks the Linux memory cap. Nothing is stopped unless you add `--kill`. There is also a hook that refuses whole-disk searches before they start.

It does not make agents faster. It removes what slows the machine down around them. For desktop apps it says honestly that a restart is the only cure I know of.

The code and the measurements are at [github.com/tonydzi/agent-slowdown](https://github.com/tonydzi/agent-slowdown). If it finds something on your machine, an issue with the output would help me more than anything.
