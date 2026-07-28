#!/bin/bash
# Starts gunicorn and nginx, and exits if either one dies so the container's
# restart policy can act instead of leaving a half-running stack.
#
# bash rather than sh: `wait -n` is a bash builtin, and it is what lets this
# notice the *first* process to exit instead of blocking on a specific one.
set -eu

cd /app/backend

# One worker, deliberately. Task state (TaskManager) and the per-project build
# locks (app/api/graph.py) live in process memory, so a second worker would get
# its own copy of both and the locks would stop serializing anything - silently.
# Concurrency within the worker comes from threads, which share that state.
# Raising this requires moving both to shared storage first.
gunicorn \
  --workers 1 \
  --threads "${GUNICORN_THREADS:-8}" \
  --bind 127.0.0.1:5001 \
  --timeout "${GUNICORN_TIMEOUT:-600}" \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile - \
  wsgi:application &
backend_pid=$!

nginx -g 'daemon off;' &
nginx_pid=$!

term() {
    kill -TERM "$backend_pid" "$nginx_pid" 2>/dev/null || true
    wait
}
trap term TERM INT

# Exit as soon as either process does, carrying its status outward.
wait -n "$backend_pid" "$nginx_pid"
status=$?
term
exit "$status"
