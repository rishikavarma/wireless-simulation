#!/usr/bin/env bash
# Machine-readable status for the coordinator heartbeat.
set -euo pipefail
[[ $# -eq 3 ]] || exit 2
start=$1
stop=$2
campaign=$3
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
pid_file="worker-$start-$stop.pid"
alive=0
if [[ -f "$pid_file" ]]; then
    worker_pid=$(cat "$pid_file")
    if kill -0 "$worker_pid" 2>/dev/null && \
        ps -p "$worker_pid" -o args= | grep -Fq "worker_raw.sh $start $stop"; then
        alive=1
    fi
fi
echo "alive=$alive"
echo "heartbeat=$(cat "worker-$start-$stop.heartbeat" 2>/dev/null || echo 0)"
echo "exit=$(cat "worker-$start-$stop.exit" 2>/dev/null || echo pending)"
echo "current=$(cat "worker-$start-$stop.current" 2>/dev/null || echo none)"
worker_dir="results/$campaign/worker-$start-$stop"
for marker in "$worker_dir"/part-*.done; do
    [[ -f "$marker" ]] || continue
    name=${marker##*/}
    echo "done=${name#part-}"
done
