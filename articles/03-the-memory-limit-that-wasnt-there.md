# The memory limit that wasn't there

Claude Code can cap the memory of the shell commands it runs. On Linux you set `CLAUDE_CODE_TOOL_MEMORY_LIMIT`, and a runaway build or test is supposed to be killed at the ceiling instead of taking the machine down with it.

I set it to 300 MB and ran a command that used 1 GB. It finished normally.

## Why it did nothing

Claude Code puts its tool commands into a new control group, a cgroup, created next to its own. In an ordinary login shell or a cron job, Claude's own group lives inside `session-N.scope`, and an ordinary user is not allowed to create groups there.

So the setup fails. The only trace is one line in the debug log:

```text
tool cgroup: disabled (EACCES)
```

Everything else looks enabled. The variable is set, nothing errors, and the command runs without any limit. A safety feature that fails silently is worse than none, because you stop worrying.

## The fix

Start Claude inside a scope your own user owns:

```bash
loginctl enable-linger "$USER"
CLAUDE_CODE_TOOL_MEMORY_LIMIT=12g systemd-run --user --scope claude
```

Same user, same variable, same machine. Only the way Claude was started differs:

```text
started from an ssh login:        silent failure, parent not writable
started with systemd-run --scope: limit applies, parent writable
```

## Proof, not settings

I do not trust a setting until something hits it. On Ubuntu 26.04 with systemd 259 I measured:

- the user manager delegates `cpu memory pids`, so per-command limits are available to an ordinary user;
- a scope with `MemoryMax=200M` and swap disabled killed a 400 MB allocation with exit 137;
- a scope with `TasksMax=20` stopped a process at its 20th child with "resource temporarily unavailable", and the caller carried on;
- starting a scope costs about 16 ms, against under 2 ms for a bare command.

Things to know before you rely on it:

- all sessions of that user share one group, so it is a per-user budget rather than per session;
- reinstalling the package can put a `claude` binary on your path that skips your wrapper, so check after upgrades;
- I have not tested it as root;
- it does nothing on macOS or Windows.

The way to check your own machine is to read `memory.max` from inside a shell command the agent runs, or to run something past the limit and look for exit 137. `agent-slowdown memlimit --test` in [agent-slowdown](https://github.com/tonydzi/agent-slowdown) does the second for you.
