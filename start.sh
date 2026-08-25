#!/bin/sh
set -eu
case "${PORT-}" in
  ''|*[!0-9]*) echo "ERROR: PORT must be an integer between 1 and 65535" >&2; exit 64 ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "ERROR: PORT must be an integer between 1 and 65535" >&2; exit 64
fi
exec gunicorn 'app:create_app()' --bind "0.0.0.0:${PORT}" --workers 1 --threads 4 --timeout 60 --graceful-timeout 30
