# First release

Every check here started as a script on our own machines. This package is a clean rewrite of those ideas, without our paths or machine names. The search guard is the exception: it is ported almost as it was, tests included.

Dogfood gate: skipped for the package itself. The scripts it descends from have been in daily use on our machines since summer 2026, but nobody other than the author has run this package yet. So this release is a 0.1, and `orphans --kill` tells you to read the list first.

Things the rewrite taught us:

- Matching helpers by any word in the command line flagged shell one-liners that merely mentioned `grep`. It now judges the program itself.
- That same scan found a class we had not thought about: shells left by dead sessions, polling forever for a file.
- `.metadata_never_index` did not stop Spotlight on macOS 26, although it is the advice you find most often.
