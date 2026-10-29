#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 5 ]] || { echo "usage: launch_worker.sh START STOP JOBS CAMPAIGN MODE" >&2; exit 2; }
start=$1
stop=$2
jobs=$3
campaign=$4
mode=$5
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
if [[ -f "worker-$start-$stop.pid" ]]; then
    previous_pid=$(cat "worker-$start-$stop.pid")
    if kill -0 "$previous_pid" 2>/dev/null && \
        ps -p "$previous_pid" -o args= | grep -Fq "worker_raw.sh $start $stop"; then
        echo "worker already running as PID $previous_pid"
        exit 0
    fi
fi
rm -f "worker-$start-$stop.exit"
nohup bash scripts/dataset/worker_raw.sh "$start" "$stop" "$jobs" "$campaign" "$mode" \
    > "worker-$start-$stop.log" 2>&1 < /dev/null &
echo $! > "worker-$start-$stop.pid"
echo "launched worker PID $(cat "worker-$start-$stop.pid")"
