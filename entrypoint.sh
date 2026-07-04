#!/usr/bin/env bash
set -euo pipefail

# One-time pre-steps run for the web roles only. The dedicated runner
# container (PRESENCE_SERVER=runner) must skip them: migrations get a
# single owner (web) so two containers never migrate concurrently, and
# the runner serves no HTTP so it needs no superuser or static files.
if [[ "${PRESENCE_SERVER:-runserver}" != "runner" ]]; then
    python manage.py migrate --noinput

    if [[ -n "${DJANGO_SUPERUSER_USERNAME:-}" \
       && -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]]; then
        python manage.py createsuperuser --noinput || true
    fi

    # Populate STATIC_ROOT for whitenoise before starting any server. With
    # DEBUG off (the secure default) the staticfiles storage is whitenoise's
    # manifest backend, which raises "Missing staticfiles manifest entry" on
    # every page unless this has run. That holds for both web roles, so it
    # must run for runserver as well as gunicorn (collectstatic is harmless
    # under the plain storage used when DEBUG is on).
    python manage.py collectstatic --noinput
fi

case "${PRESENCE_SERVER:-runserver}" in
    runserver)
        # Django's dev server. NEVER use in production. Single-process,
        # so the in-process runner thread invariant is preserved.
        exec python manage.py runserver --noreload 0.0.0.0:8000
        ;;
    gunicorn)
        # Production-grade WSGI. --workers 1 is mandatory: the in-process
        # ratelimit cache (LocMemCache) is only coherent within a single
        # web process.
        exec gunicorn presence_site.wsgi:application \
            --bind 0.0.0.0:8000 \
            --workers 1 \
            --access-logfile -
        ;;
    runner)
        # The state-machine loop as its own foreground process (issue #47).
        # Runs in the dedicated runner container; exits cleanly on SIGTERM.
        exec python manage.py run_runner
        ;;
    *)
        echo "PRESENCE_SERVER must be one of: runserver, gunicorn, runner" >&2
        echo "Got: ${PRESENCE_SERVER}" >&2
        exit 1
        ;;
esac
