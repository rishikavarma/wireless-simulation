#!/usr/bin/env bash
# Run assigned full-preset simulations and keep every original metric trace.
set -euo pipefail

if [[ $# -lt 5 || $# -gt 6 ]]; then
    echo "usage: worker_raw.sh PART_START PART_STOP JOBS CAMPAIGN MODE [seal]" >&2
    exit 2
fi
start=$1
stop=$2
jobs=$3
available_cpus=$(nproc)
if (( jobs > available_cpus )); then jobs=$available_cpus; fi
(( jobs > 0 )) || { echo "jobs must be positive" >&2; exit 2; }
campaign=$4
mode=$5
seal_only=${6:-run}
[[ $mode == short || $mode == full ]] || { echo "invalid mode: $mode" >&2; exit 2; }
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
heartbeat_file="$root/worker-$start-$stop.heartbeat"
current_file="$root/worker-$start-$stop.current"
heartbeat_loop() {
    while true; do
        date +%s > "$heartbeat_file"
        sleep 60
    done
}
heartbeat_loop </dev/null >/dev/null 2>&1 &
heartbeat_pid=$!
finish() {
    result=$?
    kill "$heartbeat_pid" 2>/dev/null || true
    printf '%s\n' "$result" > "$root/worker-$start-$stop.exit"
}
trap finish EXIT
cd "$root"
export LD_LIBRARY_PATH="$root/ns-3-dev/build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
binary="$root/ns-3-dev/build/scratch/sat-bs-handset/ns3.48-sat-bs-handset-optimized"
if ldd "$binary" | grep -q 'not found'; then
    echo "simulator shared libraries are missing on this VM" >&2
    exit 1
fi
out="results/$campaign"
if [[ ! -f "$out/manifest.json" ]]; then
    plan_args=()
    if [[ $mode == short ]]; then plan_args=(--profile-count 1000 --replicates 20); fi
    python3 scripts/dataset/run_dataset.py --preset full --stream --observation-seconds 300 \
        --out-dir "$out" --run-prefix "$campaign" --plan "${plan_args[@]}"
fi
disk_marker="$root/worker-$start-$stop.disk-ok"
if [[ ! -f "$disk_marker" ]]; then
required_kib=$(python3 - "$out" "$start" "$stop" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, 'scripts/dataset')
from stream_parts import planned_parts
manifest = json.loads((Path(sys.argv[1])/'manifest.json').read_text())
parts, _ = planned_parts(manifest, 64, 64)
count = sum(len(part) for part in parts[int(sys.argv[2]):int(sys.argv[3])])
print(count * 120 * 1024 + 40 * 1024 * 1024)
PY
)
if [[ $mode == short ]]; then
    required_kib=$((70*1024*1024))
fi
available_kib=$(df -Pk "$root" | awk 'NR==2 {print $4}')
if (( available_kib < required_kib )); then
    echo "insufficient VM disk for raw traces: need about $((required_kib/1024/1024)) GiB free, have $((available_kib/1024/1024)) GiB" >&2
    exit 1
fi
touch "$disk_marker"
fi
archive_dir="$out/worker-$start-$stop"
mkdir -p "$archive_dir"
for (( part=start; part<stop; part++ )); do
    archive=$(printf '%s/part-%05d.tar.gz' "$archive_dir" "$part")
    marker=$(printf '%s/part-%05d.done' "$archive_dir" "$part")
    ack=$(printf '%s/part-%05d.ack' "$archive_dir" "$part")
    if [[ -f "$ack" ]]; then
        continue
    fi
    if [[ -s "$archive" && -f "$marker" ]]; then
        python3 scripts/dataset/cleanup_raw_worker.py --campaign "$out" \
            --archive-dir "$archive_dir" --part "$part"
        continue
    fi
    while (( $(df -Pk "$root" | awk 'NR==2 {print $4}') < 30*1024*1024 )); do
        echo "waiting for collector to retrieve archives; VM disk below 30 GiB free"
        sleep 60
    done
    printf '%s\n' "$part" > "$current_file"
    if [[ $seal_only != seal ]]; then
        # Preserve atomic completed outcomes even if timeout/OOM kills the runner.
        if ! timeout --signal=TERM --kill-after=60s 3h \
        python3 scripts/dataset/run_dataset.py --raw-parts --run \
        --out-dir "$out" --jobs "$jobs" --batch-runs 64 --calibration-runs 64 \
        --part-start "$part" --part-stop "$((part+1))"; then
            echo "part $part interrupted; retaining only completed runs"
        fi
    fi
    python3 scripts/dataset/pack_raw_worker.py --allow-partial --campaign "$out" \
        --batch-runs 64 --calibration-runs 64 --out-dir "$archive_dir" \
        --part-start "$part" --part-stop "$((part+1))"
    python3 scripts/dataset/cleanup_raw_worker.py --campaign "$out" \
        --archive-dir "$archive_dir" --part "$part"
done
printf '%s\n' "parts [$start, $stop) complete" > "$archive_dir/COMPLETE"
echo "worker complete: parts [$start, $stop)"
