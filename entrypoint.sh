#!/usr/bin/env bash
set -euo pipefail

python manage.py migrate --noinput

if [[ -n "${DJANGO_SUPERUSER_USERNAME:-}" \
   && -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]]; then
    python manage.py createsuperuser --noinput || true
fi

case "${PRESENCE_SERVER:-runserver}" in
    runserver)
        # Django's dev server. NEVER use in production. Single-process,
        # so the in-process runner thread invariant is preserved.
        exec python manage.py runserver --noreload 0.0.0.0:8000
        ;;
    gunicorn)
        # Production-grade WSGI. --workers 1 is mandatory: the background
        # runner thread is in-process and would race across workers.
        # collectstatic populates STATIC_ROOT for whitenoise to serve.
        python manage.py collectstatic --noinput
        exec gunicorn presence_site.wsgi:application \
            --bind 0.0.0.0:8000 \
            --workers 1 \
            --access-logfile -
        ;;
    *)
        echo "PRESENCE_SERVER must be one of: runserver, gunicorn" >&2
        echo "Got: ${PRESENCE_SERVER}" >&2
        exit 1
        ;;
esac
