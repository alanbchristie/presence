---
name: screenshot-app
description: Launch the Presence Django app and screenshot it in a real browser (login page + the logged-in status dashboard). Use when asked to run/screenshot/visually verify the app or its UI.
---

# Screenshot the Presence app

Launches the app with a throwaway DB, logs in via a real Chrome browser, and
captures screenshots of the login page and the status dashboard. Verified on
macOS with Google Chrome + Node installed.

## Why this isn't just `runserver` + a headless `--screenshot`

Three things make a naive launch produce a blank or wrong screenshot:

1. **The status page is login-gated.** Anonymous `/` 302-redirects to `/login/`.
   Chrome's `--headless --screenshot` can't fill a form, so the dashboard needs
   a browser driver (Playwright) that can log in.
2. **The runner thread mutates state.** `runner.py` starts under `runserver` and,
   on its first tick, flips every *enabled* row's `current_state` based on the
   current time vs. its window. Seed rows with **always-open windows** and a
   **far-future `next_transition_at`** or the on/off badges won't be what you set.
3. **Static files only serve with `DEBUG=True`.** Bootstrap is vendored under
   `presence/static/` and referenced via `{% static %}`. With `DEBUG=False` the
   manifest storage needs `collectstatic` first; with `DEBUG=True`, `runserver`
   serves it straight from the app dir. So run with `DJANGO_DEBUG=True`.

## Prerequisites (one-off)

- Google Chrome installed (`/Applications/Google Chrome.app`).
- Node + npx (`/opt/homebrew/bin/node`).
- Playwright driver — installs in seconds, uses system Chrome (no browser download):
  ```bash
  mkdir -p /tmp/shotdir && cd /tmp/shotdir && npm init -y >/dev/null 2>&1 && npm i playwright-core
  ```

## Steps

All commands run from the repo root. Uses a temp DB so the repo's `db.sqlite3`
is untouched.

### 1. Seed a throwaway DB (user + stable demo rows)

```bash
export PRESENCE_DB_PATH=/tmp/presence_demo.sqlite3
rm -f "$PRESENCE_DB_PATH"
uv run python manage.py migrate --noinput
DJANGO_SUPERUSER_USERNAME=alan DJANGO_SUPERUSER_PASSWORD=demopass1234 \
  DJANGO_SUPERUSER_EMAIL=alan@example.com \
  uv run python manage.py createsuperuser --noinput
uv run python manage.py shell < .claude/skills/screenshot-app/seed_demo.py
```

`seed_demo.py` creates three rows that exercise every badge colour and pins them
so the runner thread leaves them alone:
- Lounge Lamp — enabled + on  → green `On`     / green `Enabled`
- Hall Light  — enabled + off → red   `Off`    / green `Enabled`
- Garage Light— disabled      → grey  `On`     / red   `Disabled`

### 2. Start the dev server (background)

```bash
export PRESENCE_DB_PATH=/tmp/presence_demo.sqlite3
export DJANGO_DEBUG=True
nohup uv run python manage.py runserver --noreload 127.0.0.1:8765 \
  > /tmp/presence_server.log 2>&1 &
# wait until it answers
for i in $(seq 1 20); do curl -fsS -o /dev/null http://127.0.0.1:8765/login/ && break; sleep 1; done
```

### 3. Drive Chrome: log in + screenshot

Run the driver from the dir that has `playwright-core` installed — Node resolves
the `playwright-core` import from the *script's* directory, so copy it in first:

```bash
cp .claude/skills/screenshot-app/shot.mjs /tmp/shotdir/
(cd /tmp/shotdir && node shot.mjs)
```

Writes `/tmp/shot_login.png` and `/tmp/shot_index.png`. **Read both images** —
a blank frame means the launch failed. Confirm: Bootstrap navbar with the
`Presence` brand on both; username + `Logout` only on the dashboard; the
on/off/disabled pill badges.

### 4. Stop the server and clean up

```bash
pkill -f "manage.py runserver --noreload 127.0.0.1:8765"
rm -f /tmp/presence_demo.sqlite3 /tmp/presence_server.log
# keep or delete the screenshots as needed:
# rm -f /tmp/shot_login.png /tmp/shot_index.png
```

## Knobs

- Port: change `8765` in steps 2–3 and `shot.mjs` (`BASE` env var overrides).
- Credentials: `shot.mjs` reads `PRESENCE_USER` / `PRESENCE_PASS` (defaults
  `alan` / `demopass1234` — must match step 1).
