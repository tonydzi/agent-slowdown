# 3. The agent runs `find /`

An agent looking for one file will sometimes search the whole disk. The process walks everything, and when the command is moved to the background or the session ends, it often keeps going.

## What we saw

- Windows: five `find /` processes from Git Bash still alive after their tasks stopped, and a `grep -rl` over the whole home folder that outlived its session.
- Mac: `bfs -S dfs / -name ...` at 530 to 640% CPU per process.

## The hook

```bash
agent-slowdown guard --print-config      # the block for ~/.claude/settings.json
agent-slowdown guard --test "find / -name x"
```

It blocks `find` and `bfs` from the root or the whole home folder, `grep -r`, `rg` and `fd` over the root or home, and recursive `grep` over `~/.claude` and `~/.codex`. It understands quotes, pipes, `;`, `&&`, `cd`, here-documents, `bash -c`, and wrappers such as `env`, `timeout` and `nice`. It allows searches of a named subfolder.

The agent sees the reason and retries with a narrower path. We confirmed that as a live refusal inside Claude Code on Windows and on macOS.

## What it does not catch

It does not evaluate variables, aliases, globs, `eval`, script files or nested `find -exec`. It stops the accident, and it will not stop an agent that is trying to get around it.

On macOS, `mdfind -name <file>` finds a file by name without walking the disk.
