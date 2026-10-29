#!/usr/bin/env bash
# Consume downloaded raw part archives in order, retaining each archive.
set -euo pipefail
[[ $# -eq 4 ]] || { echo "usage: local_tokenize.sh CAMPAIGN ARCHIVES RANGES STATE" >&2; exit 2; }
campaign_dir=$1
archive_dir=$2
ranges=$3
state_dir=$4
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"

part_done() {
    python3 - "$campaign_dir" "$1" <<'PY'
import json, sys
from pathlib import Path
progress = Path(sys.argv[1])/'stream-progress.json'
if progress.exists() and int(sys.argv[2]) in {
        part['number'] for part in json.loads(progress.read_text())['parts']}:
    sys.exit(0)
sys.exit(1)
PY
}

while IFS=$'\t' read -r instance zone project start stop; do
    worker_dir="$archive_dir/worker-$start-$stop"
    for (( part=start; part<stop; part++ )); do
        if part_done "$part"; then
            continue
        fi
        archive=$(printf '%s/part-%05d.tar.gz' "$worker_dir" "$part")
        marker=$(printf '%s/part-%05d.done' "$worker_dir" "$part")
        until [[ -s "$archive" && -f "$marker" && -s "$worker_dir/source-hashes.json" ]]; do
            [[ ! -f "$state_dir/monitor.failed" ]] || {
                echo "collector failed while waiting for part $part" >&2
                exit 1
            }
            sleep 20
        done
        python3 scripts/dataset/unpack_raw_worker.py \
            --campaign "$campaign_dir" --archive "$archive" \
            --source-hashes "$worker_dir/source-hashes.json"
        python3 scripts/dataset/run_dataset.py --stream --stream-from-raw --run \
            --out-dir "$campaign_dir" --batch-runs 64 --calibration-runs 64 \
            --part-start "$part" --part-stop "$((part+1))" --jobs 8
        echo "locally tokenized part $part" | tee -a "$state_dir/tokenize-progress.log"
    done
done < "$ranges"

python3 scripts/dataset/run_dataset.py --stream --stream-from-raw --run \
    --finalize-only --out-dir "$campaign_dir" \
    --batch-runs 64 --calibration-runs 64 --jobs 8
echo "dataset ready: $campaign_dir/default-dataset.json"
