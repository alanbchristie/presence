# presence

![GitHub Release](https://img.shields.io/github/v/release/alanbchristie/presence?include_prereleases)
[![codecov](https://codecov.io/gh/alanbchristie/presence/branch/main/graph/badge.svg)](https://codecov.io/gh/alanbchristie/presence)

A small Django app that simulates the presence of someone in a building by toggling a configurable process between **on** and **off** with randomised durations, constrained to a daily time window. The window can be expressed either as wall-clock times in a chosen IANA timezone or relative to local sunset/sunrise.

Originally written as a way to drive lights with non-fixed schedules so a property doesn't look obviously empty when the occupants are away.

## Features

- **Per-row configuration** stored as `Presence` model instances (admin-editable):
  - `identifier` — URL-safe RFC 1123 DNS label (unique) used in the REST API path
  - `name` — human-readable label
  - `min_on_duration`, `max_on_duration`, `min_off_duration`, `max_off_duration` (≥ 1 minute each)
  - Daily active window via `earliest_on` / `latest_off` wall-clock times, or
  - Solar-relative window via `earliest_on_relative_to_sunset` / `earliest_on_offset` and `latest_off_relative_to_sunrise` / `latest_off_offset` (offsets are signed `HH:MM`/`HH:MM:SS` durations) using astral's built-in city database
  - Per-row IANA `timezone` (e.g. `Europe/London`)
  - `enabled` flag to pause a row without deleting it
- **Background runner thread** started from `AppConfig.ready()` cycles each enabled row between on/off:
  - First action after each window open is always a randomised **off** phase, so state doesn't snap on at the boundary
  - Active "on" periods are force-truncated at the window close
  - Persists `current_state`, `state_since`, and `next_transition_at` so the admin shows live state
- **JSON REST endpoint** at `GET /api/presence/<identifier>/`:
  - Required API key via `X-API-Key` header, matched against the presence's linked **access key** (managed in the web UI)
  - Timestamps render in the row's timezone at second precision
  - Durations render as `HH:MM`, solar offsets as `±HH:MM`
- **Docker Compose** for one-command boot with persisted SQLite volume
- **uv**-managed virtualenv and lockfile

## Quick start (Docker)

```
./docker-up.sh
```

`docker-up.sh` resolves the application version from git (see
[Version](#version-about-modal)) and runs `docker compose build` then
`up -d` with it baked in. Forward any compose options to it, e.g.
`./docker-up.sh --profile tls`. The plain equivalent (version not baked in) is:

```
docker compose up -d --build
```

Either way this builds the image (Astral's `uv` slim base), runs migrations, attempts to create an `admin/admin` superuser (idempotent), and starts the dev server.

- Admin: <http://localhost:8000/admin/> &nbsp; (`admin` / `admin`)
- API: `curl http://localhost:8000/api/presence/<identifier>/`

SQLite persists in the named volume `presence-data` (mounted at `/data` inside the container). `docker compose down` keeps it; `docker compose down -v` wipes it.

## Configuration

Docker Compose reads a `.env` file in the project root (gitignored). Copy `.env.example` to `.env` and edit. Available variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRESENCE_API_KEY` | _(empty)_ | **Deprecated.** API access is now protected by per-presence access keys (managed in the web UI). Read only once, by migration `0009`, to seed the initial `Default` access key when upgrading; can be removed afterward. |
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

## Version (About modal)

The version shown on the **About** modal is `settings.VERSION`, resolved once
when the process starts (`presence_site/settings.py`):

1. If the `VERSION` environment variable is set and non-blank, that value wins.
2. Otherwise it is resolved from git, the same way CI computes it:
   `git describe --tags --exact-match` (an exact tag) falling back to
   `git rev-parse --short HEAD` (the short commit SHA).
3. If git is unavailable (no binary, no `.git`, or a shallow clone with no tag)
   it is `dev`.

So a plain `runserver` from a checkout reports the right version automatically:

| Situation | `VERSION` shown | Example |
| --- | --- | --- |
| `VERSION` env var set | that value | whatever you set |
| unset, HEAD is exactly a tag | the tag | `1.1.0` |
| unset, HEAD is *not* on a tag | short commit SHA | `8148bd0` |
| unset, git unavailable | the built-in default | `dev` |

(The exact-match step needs the tag present locally; run `git fetch --tags`
first, since creating a GitHub release only makes the tag on the remote.)

**CI** (`.github/workflows/build.yml`) computes the value and bakes it into the
image as a build arg — released images are tagged automatically, nothing to do.

**Docker, built locally** — the image has no `.git`, so the git fallback can't
run inside the container; `VERSION` must be set at **build** time (not just at
`up`). `./docker-up.sh` does this for you; the manual equivalent is:

```
VERSION=$(git describe --tags --exact-match 2>/dev/null || git rev-parse --short HEAD) \
  docker compose build
docker compose up -d
```

**`runserver` (no Docker)** — nothing required; the version is read from git in
your working tree. To override it, export `VERSION` before launching.

## Publishing images (Docker Hub)

Publishing a GitHub **Release** triggers `.github/workflows/release.yml`, which
builds a multi-architecture image (`linux/amd64` and `linux/arm64`) and pushes
it to Docker Hub as `DOCKERHUB_USERNAME/presence-web`, tagged with the release
version and `:latest`. The same workflow can be run manually via
**workflow_dispatch** (which publishes only the version tag, not `:latest`).

It needs two repository settings:

- `DOCKERHUB_USERNAME` — a repository **variable** (Settings → Secrets and
  variables → Actions → Variables) holding the Docker Hub account/namespace.
- `DOCKERHUB_TOKEN` — a repository **secret** holding a Docker Hub access token.

The username is a variable rather than a secret so it can gate the workflow:
when `DOCKERHUB_USERNAME` is unset the publish job is skipped entirely, so forks
without Docker Hub credentials publish nothing.

## API

`GET /api/presence/<identifier>/` returns a JSON object describing the row, where `<identifier>` is the row's RFC 1123-style identifier (e.g. `living-room`, `sequence-a`). The `X-API-Key` header must carry the value of the access key linked to that presence (create and manage keys on the **Access keys** page). Example:

```
$ curl -s -H "X-API-Key: my-secret" http://localhost:8000/api/presence/living-room/ | jq .
{
  "id": 1,
  "identifier": "living-room",
  "name": "Living room",
  "enabled": true,
  "access_key": "Living room key",
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
$ http GET http://localhost:8000/api/presence/living-room/ X-API-Key:my-secret
HTTP/1.1 200 OK
Content-Type: application/json
...

{
    "id": 1,
    "identifier": "living-room",
    "name": "Living room",
    ...
}
```

HTTPie is in the `dev` dependency group; install it locally with `uv sync --group dev` (or run on demand with `uvx httpie`).

- Missing or wrong `X-API-Key` (it must match the presence's linked access key) → `403`.
- Unknown identifier → `404`.

## Development (without Docker)

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

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
    views.py            # JSON API + presence/access-key web UI
    auth.py             # X-API-Key check against a presence's access key
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
