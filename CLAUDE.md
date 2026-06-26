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

Docker (full stack with persisted SQLite):

```
docker compose up -d --build                # plain HTTP on :8000
docker compose --profile tls up -d          # + Caddy TLS sidecar on :443
docker compose --profile tls up -d --force-recreate   # pick up changed env vars
```

## Architecture

Single Django app (`presence/`) inside the `presence_site/` project. SQLite is
the only configured database.

### The runner thread (most important invariant)

`presence/runner.py` runs **one in-process daemon thread per process**, started
from `PresenceConfig.ready()` (`apps.py`). It loops over enabled `Presence`
rows, flips their `current_state` between on/off, and persists
`current_state` / `state_since` / `next_transition_at` so the admin shows live
state.

This design assumes **exactly one worker**. `entrypoint.sh` enforces it
(`runserver --noreload`, or `gunicorn --workers 1`), and `runner._should_start()`
further gates startup to long-running commands and the autoreloader child
(`RUN_MAIN=true`) so management commands and the reloader parent don't spawn
duplicate threads. Any change touching deployment, worker count, or the ASGI/WSGI
entrypoint must preserve the single-runner invariant — otherwise multiple threads
race on the same rows. Scaling out requires factoring the runner into its own
process first.

State-machine rules live in `runner._evaluate()`. Two non-obvious behaviors that
must be preserved when editing it:
- The first transition after a window opens is always a randomized **off**
  delay placed *after* the open boundary (state never snaps on at the edge),
  and the delayed target is computed once, not re-randomized each tick.
- An active "on" period is force-truncated at `window_close`.

### Window computation

`presence/models.py` `Presence` computes the daily active window two ways,
selected per row:
- **Absolute**: `earliest_on` / `latest_off` wall-clock times in the row's IANA
  `timezone`.
- **Solar**: offsets relative to sunset/sunrise via the `astral` library's
  built-in city database (`city` field).

`Presence.clean()` enforces which fields are required for the chosen mode and
validates `identifier` (RFC 1123 DNS label, unique), `timezone`, and `city`.
The window helpers (`is_in_window`, `next_window_open`, `window_close_after`,
`_window_for_date`) are the contract the runner depends on.

### API

`GET /api/presence/<identifier>/` → JSON (`presence/views.py`). Access is
protected per-presence: each `Presence` has a required `access_key` FK to an
`AccessKey` row (named secret, auto-generated `value`), and the view rejects the
request with `403` unless the caller's `X-API-Key` header matches that key's
value (`presence/auth.request_has_valid_key`, `hmac.compare_digest`). There is
no longer a global/open mode. The former `PRESENCE_API_KEY` env var is read only
once, by migration `0009`, to seed the initial `Default` key. Access keys are
managed in the web UI (`access-key/*` routes); a key in use by any presence is
PROTECTed from deletion. Timestamps render in the row's timezone; durations as
`HH:MM`, signed solar offsets as `±HH:MM` (see `forms.SignedDurationFormField`).

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
- `entrypoint.sh` runs migrations and an idempotent `createsuperuser` on boot —
  schema changes ship as migrations, no manual DB steps in deploy docs.
- Use "conventional commits"
  