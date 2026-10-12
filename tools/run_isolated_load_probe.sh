#!/bin/sh
set -eu

: "${NEXORA_TENANT_ID:?NEXORA_TENANT_ID is required}"

log_file=/tmp/nexora-load-api.log
DEV_AUTH_BYPASS=true uvicorn central_monitor.main:app --host 127.0.0.1 --port 8091 >"$log_file" 2>&1 &
probe_pid=$!
cleanup() {
  kill "$probe_pid" 2>/dev/null || true
  wait "$probe_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ready=0
attempt=1
while [ "$attempt" -le 30 ]; do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/api/v1/health/live', timeout=1)" >/dev/null 2>&1; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if [ "$ready" -ne 1 ]; then
  cat "$log_file"
  exit 1
fi

NEXORA_BASE_URL=http://127.0.0.1:8091 \
NEXORA_ACCESS_TOKEN=isolated-probe \
NEXORA_LOAD_REQUESTS="${NEXORA_LOAD_REQUESTS:-600}" \
NEXORA_LOAD_CONCURRENCY="${NEXORA_LOAD_CONCURRENCY:-20}" \
python /tmp/load_probe.py
