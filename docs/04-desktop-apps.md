# 4. Desktop apps that grow

**We do not have a fix for this. As far as we can tell, nobody outside the vendors does.**

A renderer's memory belongs to the app. An outside tool cannot shrink it, and stopping one renderer loses that window's state. What works is restarting the app.

```bash
agent-slowdown apps
agent-slowdown apps --warn-gb 6    # suggest a restart above 6 GB (default 4)
```

It sums each app with all its helper processes and shows the largest single process and the uptime, so you can pick which app is worth restarting. It never stops anything.

## What it looks like

On a working Mac, with the ChatGPT app up for 168 hours: 31 processes and 2.3 GB, the largest 400 MB. The Claude desktop app: 28 processes and 2.3 GB, one of them 1.2 GB.

The 4 GB default is our own rule of thumb, not a measured limit.

## What helps a little

- Run long background work in the CLI, and keep the desktop app for interactive work. We counted 100 helper processes for 25 Codex desktop threads.
- Close finished threads and windows.
- Restart the app when it passes your threshold, ideally between tasks.

If you have found something that really shrinks a running app, please open an issue. We would like to be wrong about this.
