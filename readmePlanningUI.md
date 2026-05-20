# Planning UI Build & Cleanup — kato

The right-pane planning UI is a Vite + React app that compiles to a single bundle served by the Flask webserver. The Python side reads the prebuilt files from `webserver/static/build/` at runtime — there is no live transpile step in production.

## Building the bundle

```bash
cd webserver/ui
npm install        # first run only
npm run build      # outputs webserver/static/build/app.{js,css}
```

`npm run build` is idempotent and finishes in ~1s. There is also `npm run dev` if you want Vite's hot-reload while iterating on the UI; it serves on a separate port and proxies through to the Flask backend.

After a rebuild, **hard-refresh the browser** (Cmd+Shift+R on macOS, Ctrl+Shift+R elsewhere) so it doesn't keep serving the cached `app.js`. That's the most common gotcha when changes don't appear.

## Cleaning between runs

For a normal Ctrl+C → `kato compose-docker` cycle there is nothing to clean. The table below covers the cases where something does need a wipe.

| What | When to clean | How |
| --- | --- | --- |
| Browser cache | After a UI rebuild looks stale | Cmd+Shift+R (hard refresh) |
| `__pycache__` | Almost never; only if you suspect a stale `.pyc` | `find . -name __pycache__ -prune -exec rm -rf {} +` |
| `webserver/ui/node_modules` | After a `package.json` dependency change misbehaves | `rm -rf webserver/ui/node_modules && (cd webserver/ui && npm install)` |
| `webserver/static/build/` | When a build seems half-applied | `rm -rf webserver/static/build && (cd webserver/ui && npm run build)` |
| Per-task workspaces (`~/.kato/workspaces/`) | To wipe a stuck tab | `rm -rf ~/.kato/workspaces/<task-id>` |
| Session records (`~/.kato/sessions/`) | To forget Claude session ids | `rm -rf ~/.kato/sessions` (the workspace's `.kato-meta.json` re-seeds the id on next boot if it has one) |
| Claude transcripts (`~/.claude/projects/<encoded>/<id>.jsonl`) | To erase chat history replay for a task | Delete the matching JSONL — but you'll lose history-replay for that tab |

`clean.sh` exists for Docker-side cleanup (containers, volumes); it is destructive and prunes unused Docker resources without prompting.
