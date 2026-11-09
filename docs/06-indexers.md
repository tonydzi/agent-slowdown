# 6. Spotlight and antivirus

Agents create and rewrite huge numbers of small files: session logs, worktrees, `node_modules`, caches. Indexers and antivirus scanners look at each one.

```bash
agent-slowdown exclusions    # prints the steps, changes nothing
```

## Spotlight: what we tested

macOS 26 (Darwin 25.3). We made four folders, each with one file holding a unique word, then searched for that word with `mdfind`.

| Folder | Found by Spotlight? |
|--------|---------------------|
| `plain/` | yes |
| `never/` containing a `.metadata_never_index` file | **yes, the file did not help** |
| `x.noindex/` | no |
| `.dot/` | no |

The result was the same in a second run with no forced import.

What to do with this:

- `~/.claude`, `~/.codex` and other dot folders were not indexed, so excluding them buys you nothing here.
- The cost is in visible folders: repositories, worktrees, build output. Keep agent worktrees under a folder such as `~/agents.noindex/`, or add the folder in System Settings > Spotlight > Search Privacy.
- We did not measure how much CPU this saves on a real workload. This table shows only what gets indexed.

## Windows Defender: not measured by us

Other people report tests running 8 to 20 times faster with exclusions. We have not applied or timed this ourselves. `agent-slowdown exclusions` prints the `Add-MpPreference` commands. Run them only if you accept the trade-off: an excluded folder is not scanned, and that includes anything an agent downloads into it.

One thing worth checking on your own machine: on one of ours, Defender real-time protection was on at the same time as a third-party antivirus, so files may be scanned twice. That is one machine, and we do not know how common it is.

```powershell
Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled, AMRunningMode
```
