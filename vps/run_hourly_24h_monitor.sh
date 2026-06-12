#!/usr/bin/env bash
set -euo pipefail

BASE_DIR=/opt/headway-news-bot
STATE_FILE="$BASE_DIR/logs/hourly_24h_state.env"
mkdir -p "$BASE_DIR/logs"

NOW=$(date +%s)
if [ ! -f "$STATE_FILE" ]; then
  STARTED_AT=$NOW
  DEADLINE_AT=$((NOW + 86400))
  RUNS=0
  {
    echo "STARTED_AT=$STARTED_AT"
    echo "DEADLINE_AT=$DEADLINE_AT"
    echo "RUNS=$RUNS"
  } >"$STATE_FILE"
fi

# shellcheck disable=SC1090
. "$STATE_FILE"

if [ "$NOW" -ge "$DEADLINE_AT" ]; then
  systemctl disable --now headway-news-monitor-hourly24.timer || true
  exit 0
fi

RUNS=$((RUNS + 1))
{
  echo "STARTED_AT=$STARTED_AT"
  echo "DEADLINE_AT=$DEADLINE_AT"
  echo "RUNS=$RUNS"
} >"$STATE_FILE"

cd "$BASE_DIR"
"$BASE_DIR/.venv/bin/python" "$BASE_DIR/monitor.py" --hours 24 --send-review

NOW=$(date +%s)
if [ "$NOW" -ge "$DEADLINE_AT" ]; then
  systemctl disable --now headway-news-monitor-hourly24.timer || true
fi
