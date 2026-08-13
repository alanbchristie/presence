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
  - Daily active window via `window_open` / `window_close` — each edge is either a wall-clock `HH:MM`, or a signed `±HH:MM` offset from sunset (open) / sunrise (close) using astral's built-in city database (so `-01:00` opens an hour before sunset and `+00:00` is exactly sunset/sunrise)
  - Per-row IANA `timezone` (e.g. `Europe/London`)
  - `enabled` flag to pause a row without deleting it
- **Background runner** cycles each enabled row between on/off — a dedicated container under Docker Compose (`manage.py run_runner`), or an in-process thread started from `AppConfig.ready()` under plain `runserver`:
  - First action after each window open is always a randomised **off** phase, so state doesn't snap on at the boundary
  - Active "on" periods are force-truncated at the window close
  - Persists `current_state`, `state_since`, and `next_transition_at` so the admin shows live state
- **JSON REST endpoint** at `GET /api/presence/<identifier>/`:
  - Required API key via `X-API-Key` header, matched against the presence's linked **access key** (managed in the web UI)
  - Timestamps render in the row's timezone at second precision
  - Durations render as `HH:MM`, solar offsets as `±HH:MM`
- **Docker Compose** for one-command boot: PostgreSQL (`db`), the web app (`web`) and the state-machine driver (`runner`)
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

Either way this builds the image (Astral's `uv` slim base) and brings up three containers:

- `db` — PostgreSQL, the shared database
- `web` — the Django app; its entrypoint runs migrations, attempts to create an `admin/admin` superuser (idempotent) and starts the HTTP server
- `runner` — the state-machine driver (`manage.py run_runner`), started once `web` is healthy (i.e. once the schema exists)

- Admin: <http://localhost:8000/admin/> &nbsp; (`admin` / `admin`)
- API: `curl http://localhost:8000/api/presence/<identifier>/`

PostgreSQL data persists in the named volume `presence-db`. `docker compose down` keeps it; `docker compose down -v` wipes it. (Deployments upgrading from the SQLite-based stack: see [Migrating from SQLite](#migrating-from-sqlite).)

## Configuration

Docker Compose reads a `.env` file in the project root (gitignored). Copy `.env.example` to `.env` and edit. Available variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRESENCE_SERVER` | `runserver` | HTTP server: `runserver` (dev) or `gunicorn` (recommended for non-dev). See [HTTP server](#http-server). |
| `DJANGO_DEBUG` | `True` | `1`/`0`, `true`/`false`, `yes`/`no`, `on`/`off`. |
| `DJANGO_SECRET_KEY` | _(insecure dev key)_ | Set this for any non-dev deployment. Generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`. |
| `PRESENCE_DB_PASSWORD` | `presence` | PostgreSQL password shared by the `db`, `web` and `runner` services. The database publishes no host port, but set a strong value for any non-dev deployment anyway. |

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

Either way the web server runs **single-worker**: the failed-login/API rate limiter uses an in-process cache that is only coherent within one process. (State transitions are driven by the separate `runner` container, so they no longer pin the worker count — a shared cache backend is the remaining prerequisite for multi-worker web.)

In `gunicorn` mode the entrypoint runs `python manage.py collectstatic --noinput` at boot so admin/static assets are served by [whitenoise](https://whitenoise.readthedocs.io/).

Container-only environment baked into `docker-compose.yml`:

- `DJANGO_DB_HOST` / `DJANGO_DB_NAME` / `DJANGO_DB_USER` / `DJANGO_DB_PASSWORD` / `DJANGO_DB_PORT` — the shared PostgreSQL connection (presence of `DJANGO_DB_HOST` selects Postgres over the SQLite fallback).
- `PRESENCE_RUN_RUNNER=false` (web only) — the dedicated `runner` container owns the state-machine loop, so the web container must not also start the in-process thread.
- `PRESENCE_SERVER=runner` (runner only) — makes the entrypoint exec `manage.py run_runner` instead of an HTTP server.
- `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]`
- `DJANGO_SUPERUSER_*` — auto-creates the `admin/admin` superuser on first boot.

## Deploying to Kubernetes (k3s)

`helm/presence` is a Helm chart for Kubernetes **v1.36 and later** (issue
#62). The published image is multi-architecture — the release workflow builds
`linux/amd64` and `linux/arm64` — so it runs unchanged on ARM k3s nodes.

```
helm upgrade --install presence ./helm/presence \
  --namespace presence --create-namespace \
  --set django.secretKey="$(python -c 'from django.core.management.utils \
    import get_random_secret_key; print(get_random_secret_key())')" \
  --set postgresql.password="$(openssl rand -hex 16)" \
  --set django.superuser.password='choose-something-strong' \
  --set ingress.enabled=true
```

The ingress defaults to `presence.hopto.org` on the `traefik` class (k3s's
built-in controller), so only `ingress.enabled` needs setting for this
deployment; pass `--set ingress.host=...` for any other.

That creates the web Deployment, the runner Deployment, a single-instance
PostgreSQL StatefulSet with a PVC, a Secret, a Service and (optionally) an
Ingress. `helm/presence/values.yaml` documents every value; the ones you are
most likely to change:

| Value | Default | Purpose |
| --- | --- | --- |
| `django.secretKey` | — | **Required.** The app refuses to boot without it. |
| `django.existingSecret` | `""` | Use a Secret you manage instead (keys: `django-secret-key`, `db-password`, `superuser-password`, `w3w-api-key`). |
| `django.superuser.password` | `""` | Creates the `admin` superuser on first boot. Unset means no admin login. |
| `image.tag` | chart `appVersion` (`3.0.1`) | Which published tag to run. |
| `ingress.enabled` / `ingress.host` | `false` / `presence.hopto.org` | Publish via the cluster ingress. The host is added to `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` automatically. |
| `ingress.className` | `traefik` | k3s's built-in ingress controller. |
| `ingress.tls.enabled` / `ingress.tls.secretName` | `false` / `""` | Serve HTTPS from an existing certificate Secret (e.g. issued by cert-manager). |
| `compression.enabled` | `false` | Compress responses via a Traefik `compress` middleware. Requires Traefik's CRDs. |
| `postgresql.enabled` | `true` | Turn off to use `externalDatabase.*` instead. |
| `postgresql.persistence.size` | `2Gi` | PVC size (k3s defaults to the `local-path` storage class). |
| `nodeSelector` | `{}` | Pin the pods, e.g. `kubernetes.io/arch: arm64` on a mixed cluster. |

**Serve it over TLS.** With `DJANGO_DEBUG` off — the default, and the only
sane setting for a cluster — the app sets secure cookies and redirects HTTP
to HTTPS, so a plain-HTTP ingress will bounce browsers to a port nothing is
listening on. Traefik terminating TLS is enough: Django trusts its
`X-Forwarded-Proto` (`SECURE_PROXY_SSL_HEADER`), exactly as it trusts Caddy's
in the compose stack.

### TLS and compression (replacing the Caddy sidecar)

In Kubernetes, Traefik and cert-manager between them do everything the
compose stack's Caddy sidecar does — the chart deploys no Caddy, and the
`tls` compose profile is only for the non-Kubernetes deployment.

| Caddy's job (`Caddyfile`) | Kubernetes equivalent |
| --- | --- |
| Let's Encrypt issuance and renewal | cert-manager |
| TLS termination, `reverse_proxy web:8000` | Traefik, via this chart's Ingress |
| `encode zstd gzip` | `compression.enabled=true` (see below) |

`SECURE_PROXY_SSL_HEADER` needs no change: Traefik sets `X-Forwarded-Proto`
exactly as Caddy does, so `request.is_secure()` is true and
`SECURE_SSL_REDIRECT` does not loop.

With cert-manager, name your issuer and the Secret it should create:

```yaml
ingress:
  enabled: true
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  tls:
    enabled: true
    secretName: presence-tls    # cert-manager creates this
compression:
  enabled: true
```

`compression.enabled` renders a Traefik `Middleware`
(`traefik.io/v1alpha1`, the v3 API group) and references it from the Ingress
with a `traefik.ingress.kubernetes.io/router.middlewares` annotation. It is
opt-in because that CRD only exists on Traefik clusters, and it requires
`ingress.enabled` — the chart refuses to render otherwise, rather than
producing a middleware nothing uses. Any middleware reference you set
yourself is appended to, not overwritten.

Static files are compressed either way: whitenoise pre-compresses them
during `collectstatic` and serves them with the matching `Content-Encoding`.
This setting adds compression for the dynamic HTML.

### What the chart will not let you scale

Two replica counts are pinned to `1` in the templates rather than exposed as
working values, because raising either one breaks the application:

- **The runner.** Exactly one runner process may drive a given database;
  two would fight over every presence row's `current_state` and
  `next_transition_at`.
- **The web role.** The failed-login/API rate limiter counts attempts in an
  in-process `LocMemCache`, so a second replica would hand a caller a second
  independent budget. (This is the same constraint that keeps gunicorn at
  `--workers 1`.)

Both Deployments therefore also use the `Recreate` update strategy: the
default `RollingUpdate` briefly runs an extra pod, which is precisely what
must not happen. A shared cache backend is the prerequisite for scaling the
web role — until then, don't.

Startup ordering mirrors compose's `depends_on` conditions with init
containers, because a pod whose dependency is not ready simply dies and gets
restarted:

- the **web** pod waits for the database to accept connections, then its
  entrypoint applies the migrations, as it does under compose;
- the **runner** pod waits for those migrations using the read-only
  `manage.py migrate --check`, so `migrate` keeps the single owner it has
  under compose and no two processes ever apply it concurrently.

## Migrating from SQLite

Before issue #47 the compose stack stored everything in a SQLite file on the
`presence-data` volume. The database is now PostgreSQL (volume `presence-db`),
so existing deployments carry their data across once, using
`scripts/sqlite_to_postgres.sh` (plain Django `dumpdata`/`loaddata`):

```
# 1. Old stack still running (SQLite image/volume):
./scripts/sqlite_to_postgres.sh dump      # writes ./presence-dump.json
./scripts/sqlite_to_postgres.sh counts    # note the row counts

# 2. Update to the new compose file, then:
docker compose up -d db web               # web's entrypoint migrates Postgres

# 3. Load and verify, then start the runner:
./scripts/sqlite_to_postgres.sh load
./scripts/sqlite_to_postgres.sh counts    # must match step 1
docker compose up -d runner
```

User password hashes, access-key values and foreign keys survive verbatim.
Keep the old `presence-data` volume until the counts match — it is the
rollback. Local non-Docker development is unaffected: without `DJANGO_DB_HOST`
the app still uses SQLite (`PRESENCE_DB_PATH`, or `db.sqlite3` in the
checkout).

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
it to Docker Hub as `DOCKERHUB_USERNAME/presence`, tagged with the release
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
  "window_open": "-01:00",
  "window_close": "01:30",
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

The runner thread also boots from `runserver` (the `PRESENCE_RUN_RUNNER` gate defaults to true), so local dev stays self-contained — no second process needed. Code edits trigger Django's autoreloader, which restarts the runner cleanly.

## Project layout

```
manage.py
pyproject.toml          # uv-managed
uv.lock
Dockerfile
docker-compose.yml
entrypoint.sh           # per-role startup: web pre-steps + server / run_runner
helm/presence/          # Helm chart (Kubernetes v1.36+, ARM-friendly k3s)
scripts/                # sqlite_to_postgres.sh one-time data migration
.env.example
presence_site/          # Django project (settings, urls)
presence/               # The app
    models.py           # Presence model + window helpers (absolute and solar)
    runner.py           # State-machine loop (runner container or dev thread)
    management/         # run_runner command (the runner container's process)
    views.py            # JSON API + presence/access-key web UI
    auth.py             # X-API-Key check against a presence's access key
    forms.py            # Presence/location/access-key forms
    admin.py            # ModelAdmin with row-tz formatted columns
    migrations/
```

## Caveats

- Exactly **one** runner process may drive the state machine per database. Docker Compose satisfies this with the dedicated `runner` container (the web container's in-process thread is gated off with `PRESENCE_RUN_RUNNER=false`); the Helm chart with a one-replica `Recreate` Deployment; plain `runserver` with one in-process thread. Never run both, or scale `runner` beyond one replica — the copies race on the same rows.
- The web container stays **single-worker** because the rate limiter's in-process cache is only coherent within one process; a shared cache backend is the remaining prerequisite for multi-worker web.
- Outside Docker (no `DJANGO_DB_HOST`) the database is a local SQLite file — fine for development; deployments get PostgreSQL via compose.
- Solar windows depend on astral's built-in city database (~390 cities). Names are validated against that database when a row is saved.

## License

[MIT](./LICENSE).
