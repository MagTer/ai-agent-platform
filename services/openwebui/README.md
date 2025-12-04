# Open WebUI Configuration as Code

This directory keeps track of the Open WebUI state that we want under version
control.

- `data/` – bind mounted into the container (`/app/backend/data`). The folder is
  ignored by git because it contains the live SQLite database and uploaded
  assets. Keeping it on disk ensures the UI is reproducible across restarts.
- `export/` – contains SQL dumps created with `poetry run stack openwebui`
  so that meaningful configuration changes (tools, presets, settings) can be
  reviewed as plain text and committed.

Run the helper commands after you modify something in the UI:

```bash
# Capture current Open WebUI state as SQL
poetry run stack openwebui export

# Rehydrate the UI from a tracked dump
poetry run stack openwebui import
```

> **Note:** importing overwrites `/app/backend/data/app.db`. Make sure the UI is
stopped (or at least idle) before restoring a dump to avoid locking conflicts.

## Persistent logins

Set a stable secret to keep sessions valid across restarts:

- Add `OPENWEBUI_SECRET` to `.env`.
- The compose file passes it as `SECRET_KEY` and `WEBUI_JWT_SECRET`.
- This prevents forced logouts on container recreate.
