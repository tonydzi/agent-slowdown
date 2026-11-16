# I told Spotlight not to index a folder. It indexed it anyway.

The advice you find most often for keeping macOS Spotlight out of a folder is to drop an empty file called `.metadata_never_index` into it. I wanted to keep Spotlight away from the folders my coding agents churn through, so before recommending that to anyone, I tested it.

## The test

macOS 26, an Intel Mac. I made four folders in my home directory. Each held one text file with the same made-up word that appears nowhere else on the disk:

| Folder | What it was |
|---|---|
| `plain/` | an ordinary folder |
| `never/` | an ordinary folder with a `.metadata_never_index` file inside |
| `x.noindex/` | a folder whose name ends in `.noindex` |
| `.dot/` | a hidden folder whose name starts with a dot |

Then I waited and asked Spotlight for the word with `mdfind`.

## The result

| Folder | Found by Spotlight? |
|---|---|
| `plain/` | yes |
| `never/` | **yes** |
| `x.noindex/` | no |
| `.dot/` | no |

The marker file did nothing. I ran the whole thing a second time with fresh folders and no manual import, in case my first attempt had forced the indexer's hand. Same answer.

## What this means for agent users

Two things changed in my own setup.

First, the folders I worried about most, like `~/.claude` and `~/.codex`, start with a dot. Spotlight was not indexing them in the first place, so excluding them bought nothing.

Second, the heavy part is the visible folders: repositories, worktrees, build output. Those are worth moving. I now keep agent worktrees under a folder named `agents.noindex`, which Spotlight skipped in both runs. For an existing folder you cannot rename, System Settings, Spotlight, Search Privacy still works.

## What I did not measure

This table shows only what gets indexed. I did not measure how much CPU skipping those folders saves on a real workload. I also did not test Windows Defender exclusions. Other people report large speed-ups from them, but an excluded folder is also a folder nobody scans, including whatever an agent downloads into it. That trade-off is yours to make, and I would want to measure it before recommending it.

On Windows there was one thing worth checking: on one of my machines, Defender real-time protection was on at the same time as a third-party antivirus, so files may have been scanned twice. That is one machine, and I do not know how common it is.

The probe is small enough to repeat on your own Mac in five minutes. The commands and both runs are in [agent-slowdown](https://github.com/tonydzi/agent-slowdown/blob/main/docs/06-indexers.md). If your result differs, especially on another macOS version, I would like to know.
