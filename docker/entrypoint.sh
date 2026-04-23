#!/bin/sh
# Softarr container entrypoint.
#
# Runs first-boot initialisation (table creation, softarr.ini defaults,
# admin user bootstrap) exactly once, then execs gunicorn. Running
# softarr-init before the workers start avoids a race where every worker
# tries to insert the default admin row on a fresh database and one of
# them crashes on a UNIQUE constraint violation.
#
# Any extra arguments passed to ``docker run``/``compose run`` are handed
# to gunicorn via "$@" so operators can still override worker counts,
# bind address, etc. without losing the init step.
set -e

softarr-init

exec gunicorn softarr.main:app \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --access-logfile - \
    --error-logfile - \
    "$@"
