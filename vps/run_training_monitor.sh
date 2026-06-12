#!/usr/bin/env bash
set -euo pipefail

BASE_DIR=${HEADWAY_BASE_DIR:-/opt/headway-news-bot}
STATE_FILE=${TRAINING_STATE_FILE:-"$BASE_DIR/logs/training_7d_state.env"}
DURATION_HOURS=${TRAINING_DURATION_HOURS:-168}
LOOKBACK_HOURS=${TRAINING_LOOKBACK_HOURS:-24}
TARGET_DRAFTS_PER_DAY=${TRAINING_TARGET_DRAFTS_PER_DAY:-2}
TIMER_NAME=${TRAINING_TIMER_NAME:-headway-news-monitor-training7d.timer}

mkdir -p "$BASE_DIR/logs"

NOW=$(date +%s)
if [ ! -f "$STATE_FILE" ]; then
  STARTED_AT=$NOW
  DEADLINE_AT=$((NOW + DURATION_HOURS * 3600))
  RUNS=0
  {
    echo "STARTED_AT=$STARTED_AT"
    echo "DEADLINE_AT=$DEADLINE_AT"
    echo "RUNS=$RUNS"
    echo "DURATION_HOURS=$DURATION_HOURS"
    echo "LOOKBACK_HOURS=$LOOKBACK_HOURS"
    echo "TARGET_DRAFTS_PER_DAY=$TARGET_DRAFTS_PER_DAY"
  } >"$STATE_FILE"
fi

# shellcheck disable=SC1090
. "$STATE_FILE"

if [ "$NOW" -ge "$DEADLINE_AT" ]; then
  systemctl disable --now "$TIMER_NAME" || true
  exit 0
fi

RUNS=$((RUNS + 1))
{
  echo "STARTED_AT=$STARTED_AT"
  echo "DEADLINE_AT=$DEADLINE_AT"
  echo "RUNS=$RUNS"
  echo "DURATION_HOURS=$DURATION_HOURS"
  echo "LOOKBACK_HOURS=$LOOKBACK_HOURS"
  echo "TARGET_DRAFTS_PER_DAY=$TARGET_DRAFTS_PER_DAY"
  echo "LAST_RUN_AT=$NOW"
} >"$STATE_FILE"

cd "$BASE_DIR"
"$BASE_DIR/.venv/bin/python" "$BASE_DIR/monitor.py" --hours "$LOOKBACK_HOURS" --send-review

NOW=$(date +%s)
if [ "$NOW" -ge "$DEADLINE_AT" ]; then
  systemctl disable --now "$TIMER_NAME" || true
fi
