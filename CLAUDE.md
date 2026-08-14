## Commands

Dependencies are managed with **uv** (Python 3.14+, Django 6).
Prefix Django/Python commands with `uv run`.

```
uv sync                                     # create .venv from the lockfile
uv sync --group dev                         # + dev tools (httpie) and pytest
uv run python manage.py migrate
uv run python manage.py makemigrations presence
uv run python manage.py runserver           # also boots the runner thread
uv run pytest                               # run the test suite
uv run pytest presence/tests/test_models.py::test_str_returns_name   # single test
```

Note: tests live in the `presence/tests/` package (`test_models.py`, pytest +
pytest-django). Run them with `uv run pytest`; CI runs the same suite in the
`tests` job of `.github/workflows/build.yml`.

Settings fail closed: `DJANGO_DEBUG` defaults to **off**, and with debug off a
non-blank `DJANGO_SECRET_KEY` is **required** (the app raises
`ImproperlyConfigured` otherwise). So bare `manage.py` commands above need
`DJANGO_DEBUG=true` in the environment for local dev, e.g.
`DJANGO_DEBUG=true uv run python manage.py runserver` — or export it once for
the shell. The test suite is unaffected: it uses `presence_site.settings_test`
(set in `pyproject.toml`), which injects a throwaway key.

Docker (full stack: `db` = PostgreSQL, `web`, `runner`):

```
docker compose up -d --build                # plain HTTP on 127.0.0.1:8000
docker compose --profile tls up -d          # + Caddy TLS sidecar on :443
docker compose --profile tls up -d --force-recreate   # pick up changed env vars
```

The web port publishes to loopback by default (`PRESENCE_WEB_BIND=127.0.0.1`)
so the plain-HTTP app is not externally reachable under the TLS profile; set
`PRESENCE_WEB_BIND=0.0.0.0` only for a deliberately external plain-HTTP host.

## Architecture

Single Django app (`presence/`) inside the `presence_site/` project. The
database is env-selected (`settings.resolve_databases`): PostgreSQL when
`DJANGO_DB_HOST` is set (the docker-compose deployment, where `web` and
`runner` share the `db` service), otherwise a local SQLite file
(`PRESENCE_DB_PATH`), so non-docker dev and the test suite need no database
server. Tests pin in-memory SQLite in `settings_test.py`.

### The runner (most important invariant)

`presence/runner.py` drives the state machine: `run(stop_event=None)` loops
over enabled `Presence` rows, flips their `current_state` between on/off, and
persists `current_state` / `state_since` / `next_transition_at` so the admin
shows live state. **Exactly one runner process may drive a given database.**
It runs in one of three places:

- **Dedicated `runner` container** (docker-compose default): `entrypoint.sh`
  execs `manage.py run_runner` (`presence/management/commands/run_runner.py`),
  which installs SIGTERM/SIGINT handlers that set the stop event, so
  `docker stop` shuts it down cleanly. The web container sets
  `PRESENCE_RUN_RUNNER=false` so it never also spawns the in-process thread.
  Never scale `runner` beyond one replica.
- **`presence-runner` Deployment** (the Helm chart, issue #62): the same
  image and role, pinned to `replicas: 1` with the `Recreate` strategy so a
  rolling update's surge pod cannot briefly run a second state machine.
- **In-process daemon thread** (plain `runserver` local dev): started from
  `PresenceConfig.ready()` (`apps.py`) via `runner.start()`, gated by
  `PRESENCE_RUN_RUNNER` (default true) and `runner._should_start()` (long-
  running commands and the autoreloader child, `RUN_MAIN=true`, only — so
  management commands and the reloader parent don't spawn duplicate threads).

The **web** container stays `--workers 1` regardless: the ratelimit cache
(below) is in-process and only coherent within one worker. A shared cache
backend is the remaining prerequisite for multi-worker web — do not loosen
the worker count without it.

Existing SQLite deployments migrate their data once via
`scripts/sqlite_to_postgres.sh` (dump/load/counts; see README "Migrating
from SQLite"). `migrate` has a single owner — the web entrypoint; the runner
container deliberately skips it to avoid a concurrent-migrate race. Under
Helm the runner's init container waits on the read-only `migrate --check`
instead, which is the Kubernetes equivalent of compose's `depends_on: web
condition: service_healthy`.

### Kubernetes (Helm)

`helm/presence` deploys the same two roles to Kubernetes v1.36+ (ARM k3s is
the target; the released image is built for `linux/amd64` and `linux/arm64`).
Both replica counts are **hard-coded to 1 in the templates**, not exposed as
values — `web.replicaCount`/`runner.replicaCount` exist only to document
that — and both Deployments use `Recreate`. `presence/tests/test_helm_chart.py`
renders the chart with `helm template` and asserts these invariants, so they
fail loudly if someone parameterises them; CI additionally lints the chart and
validates every value permutation with kubeconform (the `helm` job of
`.github/workflows/build.yml`). Chart-facing config follows the same pattern
as the rest: env var → `settings.py` → `values.yaml` → the pod's env.

The chart carries **no `appVersion` and no default `image.tag`** — the
operator names the image, and rendering fails (via `required`) if they do
not. An appVersion would be the default tag, so it would have to track the
newest published release, which coupled the chart's revision to the app's
release cadence: every application fix dragged a chart bump behind it. Do not
reintroduce one; every `helm template`/`lint` invocation, in tests and CI
alike, must pass `--set image.tag=…`.

State-machine rules live in `runner._evaluate()`. Two non-obvious behaviors that
must be preserved when editing it:
- The first transition after a window opens is always a randomized **off**
  delay placed *after* the open boundary (state never snaps on at the edge),
  and the delayed target is computed once, not re-randomized each tick.
- An active "on" period is force-truncated at `window_close`.

### Window computation

Each window edge is one string field on `Presence` (issue #59):
`window_open` and `window_close`, parsed by `models.parse_window_edge`:
- **Absolute**: `HH:MM` — a wall-clock time, interpreted in the IANA
  `timezone` of the presence's `Location`.
- **Solar**: `+HH:MM` / `-HH:MM` — a signed offset from sunset (open) or
  sunrise (close) via the `astral` library's built-in city database, keyed
  by the `Location`'s `city`. The sign alone selects solar mode, so
  `+00:00` is exactly sunset/sunrise while `00:00` is midnight.

`timezone` and `city` live on `Location` (issue #43): all presences at a
location share them, and the window helpers read `self.location.timezone` /
`self.location.city`. `Location.clean`/field validators check the IANA
`timezone` and astral `city`; the edge strings are checked by
`models.validate_window_edge` and normalised (2-digit hours) by the form;
`Presence.clean()` validates `identifier` (RFC 1123 DNS label, unique),
rejects a zero-length absolute window, and — for a solar edge — raises a
non-field error unless the linked location names a city. The window helpers
(`is_in_window`, `next_window_open`, `window_close_after`,
`_window_for_date`) are the contract the runner depends on; the API
(`_serialize`) still exposes `timezone`/`city`, sourced from the location.

### API

`GET /api/presence/<identifier>/` → JSON (`presence/views.py`). Access is
protected per-presence: each `Presence` has a required `access_key` FK to an
`AccessKey` row (named secret, auto-generated `value`), and the view rejects the
request with `403` unless the caller's `X-API-Key` header matches that key's
value (`presence/auth.request_has_valid_key`, `hmac.compare_digest`). There is
no longer a global/open mode. The initial `Default` key is seeded by migration
`0009`. Access keys are
managed in the web UI (`access-key/*` routes); a key in use by any presence is
PROTECTed from deletion. Timestamps render in the row's timezone; durations as
`HH:MM`; the window edges are served verbatim (`HH:MM` or `±HH:MM`).

An unknown identifier returns the **same** `403` as a known one with a bad key,
so callers cannot enumerate which presences exist (do not reintroduce
`get_object_or_404` here). Both the API and the login view rate-limit failed
attempts per client IP via `presence/ratelimit.py` (in-process LocMemCache,
which is coherent only because the web container runs a single worker);
exceeding the limit yields `429`. A successful auth clears the caller's
counter. The runner container serves no HTTP and never touches this cache.

### Configuration is environment-driven

`presence_site/settings.py` reads everything from `os.environ` (12-factor).
`docker-compose.yml` is the source of truth for container env wiring and derives
`DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` from `PRESENCE_DOMAIN`.
The app sits behind Caddy (TLS-terminating reverse proxy), so
`SECURE_PROXY_SSL_HEADER` is set and trusts Caddy's `X-Forwarded-Proto`; that is
only safe because Caddy is the sole entry point. When adding deployment-facing
settings, follow the existing pattern: env var → settings.py → documented in
`.env.example` → wired in `docker-compose.yml`.

## Conventions

- Work off `main` via a feature branch; open a PR (this repo does not commit
  directly to `main`).
- `entrypoint.sh` runs migrations and an idempotent `createsuperuser` on boot
  of the **web** roles (the runner role skips them) — schema changes ship as
  migrations, no manual DB steps in deploy docs.
- Use "conventional commits"
  