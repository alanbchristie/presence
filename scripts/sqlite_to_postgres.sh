#!/usr/bin/env bash
set -euo pipefail

# One-time SQLite -> PostgreSQL data migration helper (issue #47).
#
# The docker-compose default database moved from a SQLite file (in the
# old 'presence-data' volume) to PostgreSQL (the 'db' service). Existing
# deployments carry their data across in three steps, run from the
# repository root:
#
#   1. On the OLD stack (SQLite image/volume still in place):
#        ./scripts/sqlite_to_postgres.sh dump
#      Writes ./presence-dump.json (override with DUMP_FILE=...).
#      Record the row counts with:
#        ./scripts/sqlite_to_postgres.sh counts
#
#   2. Switch to the new compose file and start Postgres + web:
#        docker compose up -d db web
#      web's entrypoint runs 'migrate' against the empty Postgres DB.
#
#   3. Load the dump and verify, then start the runner:
#        ./scripts/sqlite_to_postgres.sh load
#        ./scripts/sqlite_to_postgres.sh counts   # compare with step 1
#        docker compose up -d runner
#
# 'loaddata' loads with explicit primary keys inside a transaction and
# resets the Postgres sequences for the affected models afterwards, so
# subsequent inserts do not collide. (Fallback if a sequence is ever
# off: manage.py sqlsequencereset presence auth | psql ...)
#
# contenttypes / auth.permission are excluded because fresh migrations
# recreate those rows on the Postgres side (keeping them would clash);
# sessions are disposable. auth.User password hashes, AccessKey values
# and foreign keys survive verbatim.
#
# Keep the old 'presence-data' volume until verification passes - it is
# the rollback.

DUMP_FILE="${DUMP_FILE:-presence-dump.json}"

COUNTS_SCRIPT='
from django.contrib.auth.models import User
from presence.models import AccessKey, Location, Presence
print(f"Presence:  {Presence.objects.count()}")
print(f"Location:  {Location.objects.count()}")
print(f"AccessKey: {AccessKey.objects.count()}")
print(f"User:      {User.objects.count()}")
'

case "${1:-}" in
    dump)
        docker compose exec -T web python manage.py dumpdata \
            --natural-foreign --natural-primary \
            -e contenttypes -e auth.permission -e sessions.session \
            --indent 2 > "${DUMP_FILE}"
        echo "Wrote ${DUMP_FILE}"
        ;;
    load)
        if [[ ! -f "${DUMP_FILE}" ]]; then
            echo "No ${DUMP_FILE} - run the 'dump' step first" >&2
            exit 1
        fi
        docker compose cp "${DUMP_FILE}" web:/tmp/presence-dump.json
        docker compose exec -T web \
            python manage.py loaddata /tmp/presence-dump.json
        echo "Loaded ${DUMP_FILE}; compare 'counts' with the pre-dump" \
             "values, then: docker compose up -d runner"
        ;;
    counts)
        docker compose exec -T web \
            python manage.py shell -c "${COUNTS_SCRIPT}"
        ;;
    *)
        echo "Usage: $0 {dump|load|counts}" >&2
        echo "  dump   - dumpdata from the running (old, SQLite) web" >&2
        echo "  load   - loaddata into the running (new, Postgres) web" >&2
        echo "  counts - print key row counts for verification" >&2
        exit 1
        ;;
esac
