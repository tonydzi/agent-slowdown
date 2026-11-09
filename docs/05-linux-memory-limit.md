# 5. `CLAUDE_CODE_TOOL_MEMORY_LIMIT` can be silently off

Claude Code can cap the memory of Bash tool commands through a cgroup. On Linux, setting the variable is not enough.

It creates the group next to its own cgroup. In a normal login shell or cron job, that parent is `session-N.scope`, an ordinary user cannot write there, and the setup fails. The only trace is one line in the debug log:

```text
tool cgroup: disabled (EACCES)
```

In our test a command using 1 GB ran fine under a 300 MB limit, with everything looking enabled. (Claude Code 2.1.318, Linux 7.0, cgroup v2.)

## Check yours

```bash
agent-slowdown memlimit          # can the group be created where Claude would create it?
agent-slowdown memlimit --test   # allocates 400 MB under a real 200 MB scope
```

Verdicts from our machine, same user and same variable, only the start differs:

```text
ssh login:                        silent-failure  (parent not writable)
systemd-run --user --scope:       ok              (parent writable)
--test:                           exit -9: killed at the ceiling, the scope works
```

## The fix

Start Claude inside a scope your user owns. User lingering must be on:

```bash
loginctl enable-linger "$USER"
CLAUDE_CODE_TOOL_MEMORY_LIMIT=12g systemd-run --user --scope claude
```

Accepted values are `12g`, `300m` and `none`.

Things to know:

- The limit is shared by every session of that user, because they all use one group.
- Reinstalling the npm package can restore a `claude` binary that bypasses your wrapper, and then the limit is gone again. Check after upgrades.
- Running as root is untested.
- It does nothing on macOS or Windows.

Trust only the fact: read `memory.max` from inside a Bash tool call, or run a command past the limit and look for exit 137.
