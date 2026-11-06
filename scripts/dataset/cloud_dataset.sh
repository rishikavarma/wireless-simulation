#!/usr/bin/env bash
# Run on the collector. stage prepares workers; start explicitly begins the campaign.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
source .venv/bin/activate
export DATASET_COLLECTOR=cloud COLLECTOR_MAX_GB=3500
export DISTRIBUTED_TOKENIZE=1
export TMPDIR="$root/results/tmp"
mkdir -p "$TMPDIR"
inventory=scripts/dataset/vm-commands-private.txt
mode=$(cat local/cloud-campaign-mode.txt 2>/dev/null || echo short)
case $mode in
    short) campaign=short-distributed-300-20k; total_parts=320; mode_args=(--short) ;;
    full) campaign=full-distributed; total_parts=640; mode_args=() ;;
    *) echo "unknown cloud campaign mode: $mode" >&2; exit 2 ;;
esac
receipt="results/cloud-complete.json"
if [[ $mode == full ]]; then receipt="results/$campaign-cloud-complete.json"; fi
phase=${1:-status}
if [[ $phase == start ]]; then
    if systemctl is-active --quiet wireless-dataset.service; then
        echo "cloud campaign coordinator is already running"; exit 0
    fi
    # Keep collection detached from SSH and reserve RAM for OS and transfers.
    sudo systemd-run --unit=wireless-dataset --collect \
        --uid="$(id -u)" --gid="$(id -g)" \
        --property="WorkingDirectory=$root" \
        --property="Environment=HOME=$HOME" \
        --property=MemoryHigh=200G --property=MemoryMax=230G \
        --property=TasksMax=4096 --property=OOMPolicy=stop \
        /bin/bash "$root/scripts/dataset/cloud_dataset.sh" run
    echo "started cloud campaign; journalctl -u wireless-dataset -f follows progress"
elif [[ $phase == run ]]; then
    mkdir -p results
    watch_pid=
    stop_watch() {
        if [[ -n "${watch_pid:-}" ]] && kill -0 "$watch_pid" 2>/dev/null; then
            kill "$watch_pid" 2>/dev/null || true
            wait "$watch_pid" 2>/dev/null || true
        fi
    }
    on_exit() {
        local code=$?
        stop_watch
        if (( code != 0 )); then
            python scripts/dataset/cloud_notify.py --failure --detail "Campaign failed before final packaging (exit $code)." || true
        fi
    }
    trap on_exit EXIT
    python scripts/dataset/cloud_notify.py --started || true
    python -u scripts/dataset/cloud_notify.py --watch --interval-minutes 30 >> results/progress-notify.log 2>&1 &
    watch_pid=$!
    # Freshly started guests can become RUNNING before sshd is ready.
    ready=0
    for attempt in {1..12}; do
        if python scripts/dataset/stop_cloud_fleet.py --dry-run; then ready=1; break; fi
        sleep 5
    done
    [[ $ready == 1 ]] || { echo "worker startup checks failed" >&2; exit 1; }
    # A resumed collection with every raw part already on the collector only
    # needs tokenization; do not restart finished simulation workers.
    raw_count=0
    if [[ -d results/$campaign-raw-archives ]]; then
        raw_count=$(find "results/$campaign-raw-archives" -name 'part-*.done' | wc -l)
    fi
    if (( raw_count < total_parts )); then
        bash scripts/dataset/distributed_raw.sh "${mode_args[@]}" start "$inventory"
    fi
    bash scripts/dataset/distributed_raw.sh "${mode_args[@]}" collect "$inventory"
    sha256sum "results/$campaign.tar.gz" > "results/$campaign.tar.gz.sha256.tmp"
    mv "results/$campaign.tar.gz.sha256.tmp" "results/$campaign.tar.gz.sha256"
    python - "$campaign" "$receipt" <<'PYCODE'
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
p=Path('results')/(sys.argv[1]+'.tar.gz')
receipt=dict(archiveBytes=p.stat().st_size, sha256=p.with_name(p.name+'.sha256').read_text().split()[0],
             finalizedAt=datetime.now(timezone.utc).isoformat())
target=Path(sys.argv[2])
temporary=target.with_suffix('.json.tmp')
temporary.write_text(json.dumps(receipt, indent=2)+'\n')
temporary.replace(target)
PYCODE
    sync
    stop_watch
    trap - EXIT
    if ! python scripts/dataset/cloud_notify.py; then
        echo "Push notification failed; dataset remains available on the collector" >&2
    fi
    python scripts/dataset/stop_cloud_fleet.py
else
    case $phase in prepare|stage|check-vms|status) ;; *) echo "usage: cloud_dataset.sh {prepare|stage|check-vms|start|status}" >&2; exit 2 ;; esac
    bash scripts/dataset/distributed_raw.sh "${mode_args[@]}" "$phase" "$inventory"
fi
