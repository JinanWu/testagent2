#!/bin/sh
set -eu

# Cloud Run instances have an ephemeral writable /tmp.  Start every test
# instance from clean SQLite seed files instead of writing to image layers.
runtime_dir=/tmp/agent-service
mkdir -p "$runtime_dir/bundles"
cp /app/docker-seed/web-seed-clean.sqlite3 "$runtime_dir/web.sqlite3"
cp /app/docker-seed/published-seed-clean.sqlite3 "$runtime_dir/published.sqlite3"

exec "$@"
