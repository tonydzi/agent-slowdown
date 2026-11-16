# Seven packages and none of them installable

In October I counted my public repositories: 61. Eight of them were tools I had announced as reusable, with a `pyproject.toml`, release notes and the word "install" in the README. On PyPI, every one of those eight names returned 404. Anyone who wanted to try one had to clone a repository, which almost nobody does for a tool they have not used yet.

A repository without an install command is a road with no on-ramp. This week I built the on-ramps. Here is what got in the way, because none of it was in the documentation I read.

## Trusted Publishing, briefly

PyPI can accept a release straight from a GitHub Actions workflow without any stored token. You register a "pending publisher" on PyPI, naming the GitHub owner, repository, workflow file and environment. The first successful run creates the project.

The workflows had existed for months. The PyPI side had never been registered, so every release run failed or was skipped, while the release notes said "published".

## Four things that blocked me

**1. Three at a time.** PyPI lets an account hold at most three pending publishers. I registered three, the fourth failed with "You can't register more than 3 pending trusted publishers at once." The way through is to publish those three first: once the project exists, the pending entry becomes an ordinary publisher and frees the slot.

**2. A name too close to someone else's.** `claim-check` was refused: "This project name is too similar to an existing project". A package called `claimcheck` already exists. The tool now ships as `claim-check-ci`, and the command people type is still `claim-check`. Worth checking before you write a README that tells people to `pip install` a name you cannot have.

**3. A switch I had set myself.** One workflow ran green and published nothing. Months earlier I had made the publish job conditional on a repository variable, so that releases would not show as red before PyPI was set up. Then I forgot about it. Green with the publish job "skipped" looks almost exactly like success.

**4. A login that expires.** The registration is a web form behind a login. The first time I had everything ready, the browser session had expired after six days. Anything that depends on a human login should be finished in the same sitting.

## What "done" means here

I counted a package as shipped only when `pip install <name>` worked in a fresh, empty environment and reported the version. All seven pass that check now:

```bash
pip install agent-slowdown
pip install claim-check-ci
pip install lambda-cloud-mcp
pip install pr-watch
pip install secondop-panel
pip install verbatim-citation-gate
pip install sqlite-graph-memory
```

A check that reads the README would have said all of them were installable months ago. Until this week, a check that installs them found one of the seven.
