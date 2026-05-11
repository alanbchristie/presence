# presence

A small Django app that simulates the presence of someone in a building by toggling a configurable process between **on** and **off** with randomised durations, constrained to a daily time window. The window can be expressed either as wall-clock times in a chosen IANA timezone or relative to local sunset/sunrise.

Originally written as a way to drive lights with non-fixed schedules so a property doesn't look obviously empty when the occupants are away.

## Features

- **Per-row configuration** stored as `Presence` model instances (admin-editable):
  - `min_on_duration`, `max_on_duration`, `min_off_duration`, `max_off_duration` (≥ 1 minute each)
  - Daily active window via `earliest_on` / `latest_off` wall-clock times, or
  - Solar-relative window via `earliest_on_relative_to_sunset` / `earliest_on_offset` and `latest_off_relative_to_sunrise` / `latest_off_offset` (offsets are signed `HH:MM`/`HH:MM:SS` durations) using astral's built-in city database
  - Per-row IANA `timezone` (e.g. `Europe/London`)
  - `enabled` flag to pause a row without deleting it
- **Background runner thread** started from `AppConfig.ready()` cycles each enabled row between on/off:
  - First action after each window open is always a randomised **off** phase, so state doesn't snap on at the boundary
  - Active "on" periods are force-truncated at the window close
  - Persists `current_state`, `state_since`, and `next_transition_at` so the admin shows live state
- **JSON REST endpoint** at `GET /api/presence/<int:pk>/`:
  - Optional API key via `X-API-Key` header (configured by `PRESENCE_API_KEY`); if unset the endpoint is open
  - Timestamps render in the row's timezone at second precision
  - Durations render as `HH:MM`, solar offsets as `±HH:MM`
- **Docker Compose** for one-command boot with persisted SQLite volume
- **uv**-managed virtualenv and lockfile

## Quick start (Docker)

```
docker compose up -d --build
```

That builds the image (Astral's `uv` slim base), runs migrations, attempts to create an `admin/admin` superuser (idempotent), and starts the dev server.

- Admin: <http://localhost:8000/admin/> &nbsp; (`admin` / `admin`)
- API: `curl http://localhost:8000/api/presence/<pk>/`

SQLite persists in the named volume `presence-data` (mounted at `/data` inside the container). `docker compose down` keeps it; `docker compose down -v` wipes it.

## Configuration

Docker Compose reads a `.env` file in the project root (gitignored). Copy `.env.example` to `.env` and edit. Available variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRESENCE_API_KEY` | _(empty)_ | If set, every API call must include `X-API-Key: <value>`. Blank/unset → endpoint is open. |
| `PRESENCE_SERVER` | `runserver` | HTTP server: `runserver` (dev) or `gunicorn` (recommended for non-dev). See [HTTP server](#http-server). |
| `DJANGO_DEBUG` | `True` | `1`/`0`, `true`/`false`, `yes`/`no`, `on`/`off`. |
| `DJANGO_SECRET_KEY` | _(insecure dev key)_ | Set this for any non-dev deployment. Generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`. |

### TLS (Caddy sidecar)

TLS is opt-in. The compose stack ships a [Caddy](https://caddyserver.com/) service under the `tls` profile that fronts the presence container and auto-manages certificates.

```
docker compose --profile tls up -d
```

Caddy reads `PRESENCE_DOMAIN` from `.env`:

- `PRESENCE_DOMAIN=localhost` (default) → Caddy issues a cert from its [internal CA](https://caddyserver.com/docs/automatic-https#local-https). Install the root cert (`docker compose exec caddy cat /data/caddy/pki/authorities/local/root.crt`) into your OS/browser trust store to avoid the warning.
- `PRESENCE_DOMAIN=presence.example.com` → Caddy automatically requests a Let's Encrypt certificate. Requirements: the name resolves to this host and ports 80 and 443 are reachable from the internet. Let's Encrypt expiry notifications go to the `email` address configured in the `Caddyfile`.

The app continues to listen on host port 8000 (plain HTTP) for direct curl access; Caddy serves the same upstream on 443. To go TLS-only, remove the `web.ports` mapping from `docker-compose.yml`.

`PRESENCE_DOMAIN` is also appended to `DJANGO_ALLOWED_HOSTS` automatically so Django accepts the proxied requests.

### HTTP server

`PRESENCE_SERVER` chooses which HTTP server fronts the app:

- **`runserver`** (default) — Django's development server. Only suitable for local dev.
- **`gunicorn`** — Recommended for any non-dev use. Mature, sync WSGI server.

Either way the server runs **single-worker**, because the in-process runner thread that drives state transitions would race across workers. If you need to scale to multiple processes, the runner needs to be factored out into a dedicated process first.

In `gunicorn` mode the entrypoint runs `python manage.py collectstatic --noinput` at boot so admin/static assets are served by [whitenoise](https://whitenoise.readthedocs.io/).

Container-only environment baked into `docker-compose.yml`:

- `PRESENCE_DB_PATH=/data/db.sqlite3` — moves the SQLite file into the persistent volume.
- `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]`
- `DJANGO_SUPERUSER_*` — auto-creates the `admin/admin` superuser on first boot.

## API

`GET /api/presence/<pk>/` returns a JSON object describing the row. Example:

```
$ curl -s -H "X-API-Key: my-secret" http://localhost:8000/api/presence/1/ | jq .
{
  "id": 1,
  "name": "Living room",
  "enabled": true,
  "timezone": "Europe/London",
  "min_on_duration": "00:15",
  "max_on_duration": "01:30",
  "min_off_duration": "00:05",
  "max_off_duration": "00:45",
  "earliest_on": null,
  "latest_off": null,
  "earliest_on_relative_to_sunset": true,
  "earliest_on_offset": "-01:00",
  "latest_off_relative_to_sunrise": false,
  "latest_off_offset": null,
  "city": "London",
  "state": "off",
  "state_since": "2026-05-11T18:21:30+01:00",
  "next_transition_at": "2026-05-11T19:42:11+01:00",
  "in_window": true,
  "now": "2026-05-11T18:48:02+01:00"
}
```

The same call via [HTTPie](https://httpie.io/) (header syntax is `Name:Value`):

```
$ http GET http://localhost:8000/api/presence/1/ X-API-Key:my-secret
HTTP/1.1 200 OK
Content-Type: application/json
...

{
    "id": 1,
    "name": "Living room",
    ...
}
```

HTTPie is in the `dev` dependency group; install it locally with `uv sync --group dev` (or run on demand with `uvx httpie`).

- Missing or wrong `X-API-Key` (when configured) → `403`.
- Unknown PK → `404`.

## Development (without Docker)

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```
uv sync                                 # create .venv, install pinned deps
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

The runner thread also boots from `runserver`. Code edits trigger Django's autoreloader, which restarts the runner cleanly.

## Project layout

```
manage.py
pyproject.toml          # uv-managed
uv.lock
Dockerfile
docker-compose.yml
entrypoint.sh           # migrate + idempotent createsuperuser + runserver
.env.example
presence_site/          # Django project (settings, urls)
presence/               # The app
    models.py           # Presence model + window helpers (absolute and solar)
    runner.py           # Background thread that flips state
    views.py            # JSON API
    auth.py             # X-API-Key decorator
    forms.py            # SignedDurationFormField (±HH:MM rendering)
    admin.py            # ModelAdmin with row-tz formatted columns
    migrations/
```

## Caveats

- The background runner is one **in-process** daemon thread. It is fine for `runserver` and a single-worker production setup, but multi-worker WSGI (e.g. `gunicorn -w 4`) would spawn one thread per worker and race on the same rows. For multi-worker deployments, factor the runner out into a separate process (e.g. a management command driven by `systemd`).
- SQLite is the configured database. It is suitable for a few rows on a single host; switch to Postgres if you need anything more.
- Solar windows depend on astral's built-in city database (~390 cities). Names are validated against that database when a row is saved.

## License

[MIT](./LICENSE).
