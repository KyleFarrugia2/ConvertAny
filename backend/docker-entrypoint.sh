#!/bin/sh
set -e
case "$1" in
  api)
    exec uvicorn omniconvert.api.main:app --host 0.0.0.0 --port 8000 --workers "${OC_API_WORKERS:-2}" --proxy-headers --no-access-log
    ;;
  worker)
    shift
    exec python -m omniconvert.worker.main "$@"
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
