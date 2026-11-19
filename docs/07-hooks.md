# 7. Hooks: every guard is a new process

```bash
agent-slowdown hooks            # what each tool call, prompt and session start launches
agent-slowdown hooks --json
agent-slowdown hooks --no-time  # skip timing the bare interpreters
```

A hook is a program the agent starts on an event. Claude Code, Codex, Gemini CLI and Cursor all start a fresh process for every matching hook on every matching event. One guard costs nothing you would notice. Six months of adding them, one per lesson learned, is a different story: each one still looks harmless, and the machine crawls.

## What we measured

On a Windows box running about 13 Claude Code sessions in parallel:

- every Bash call started 19 separate hooks, each one `bash` + a console host + `python`: about 57 processes for a single command;
- the machine launched 938 processes per minute (a quiet machine does tens); hooks were 42% of them (counted with Sysmon process-create events by parent);
- symptoms: keyboard lag, flashing console windows, antivirus scanning every new process.

What helped was not deleting guards but changing how they start: one dispatcher per event reads a rule file, picks the hooks whose matcher and a cheap regex prefilter fit this call, and runs Python guards inside its own process instead of one interpreter each. On a Mac with the same guards after the change, the dispatcher's own log over 7,214 events (11-19 Nov 2026) shows:

| event | p50 | p90 |
|---|---|---|
| PreToolUse | 6 ms | 23 ms |
| PostToolUse | 8 ms | 10 ms |
| UserPromptSubmit | 9 ms | 32 ms |
| SessionStart | 9 ms | 82 ms |

and on average 1-2 guards actually ran per event out of the dozens registered. Those timings are measured inside the dispatcher, so add one interpreter start (15-17 ms for Python on that Mac). The before and after numbers come from two different machines, so read them as an order of magnitude, not as a clean A/B.

## What the command flags

- **A shell only to start another program** (`bash -c "python3 guard.py"`): two processes where one does the job.
- **npx / uvx in a hook**: the package is resolved on every run.
- **No matcher on a per-tool event**: the hook runs on every Read and Grep too.
- **A script that starts more processes itself**: a dispatcher that `Popen`s each sub-hook still pays one interpreter per sub-hook.
- **A large Python entry file**: Python caches bytecode for imported modules, never for the file you run. An 800-line entry script is compiled from source on every event. We measured about 2 ms for an 814-line file: small next to an interpreter start, but paid on every event. Keep the entry tiny and import the rest.
- **The same command registered twice** (for example in user settings and a project settings file): it runs twice.

## A tip the command does not check

**Imports compiled from source every run.** With `PYTHONDONTWRITEBYTECODE=1` (the Claude desktop app sets it) Python never writes `.pyc` for a hook's own modules, but it still reads `.pyc` that already exist. On 200 generated modules (32,000 lines) the import took 137 ms with no `.pyc` and 31 ms after one `python -m compileall` over them, with the variable still set. Run `compileall` with the same interpreter and flags the hook uses: Apple's Command Line Tools Python keeps `.pyc` in `~/Library/Caches/com.apple.python`, and `-I` makes it look elsewhere.

## How sure we are

- Counting is static: it reads `~/.claude/settings*.json`, `./.claude/settings*.json`, enabled Claude Code plugins, `~/.codex/hooks.json`, `~/.gemini/settings.json` and `~/.cursor/hooks.json`. Process counts are a lower bound; a script's own children are flagged, not counted.
- Timing covers only bare interpreter start-up (`python3 -c pass`, `node -e 0`, `bash -c true`), using the interpreter found on PATH (never a program path taken from a hook, never one inside the project folder) and a stripped environment, so `BASH_ENV` or `NODE_OPTIONS` cannot pull code in. Your hooks are never run, because hooks have side effects. If a hook pins a different interpreter than the one on PATH, its start-up may differ.
- The shell the agent itself uses to run a hook line is not counted, which is one reason the process count is a lower bound.
- Agents run matching hooks in parallel, so the summed figure is CPU paid per event, not added wait. On a busy machine CPU is the wait.
- Claude Code and Codex configs were checked against real files. Gemini CLI and Cursor shapes follow their docs and are covered by tests only. A file that is not valid JSON is listed as "cannot judge"; a valid file in a shape we do not know reads as zero hooks, so if the count looks too low, open an issue with the file.
