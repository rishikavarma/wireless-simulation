#!/usr/bin/env bash
# Deploy raw-trace runs, retrieve archives, and tokenize locally.
set -euo pipefail

usage() {
    echo "usage: distributed_raw.sh [--short] {all|prepare|check-vms|deploy|collect|status} [VM_COMMANDS_FILE]" >&2
    exit 2
}
mode=full
if [[ ${1:-} == --short ]]; then mode=short; shift; fi
[[ $# -ge 1 && $# -le 2 ]] || usage
phase=$1
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
commands_file=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${2:-$root/scripts/dataset/vm-commands.txt}")
[[ -f "$commands_file" ]] || { echo "missing VM commands file: $commands_file" >&2; exit 2; }
cd "$root"
if [[ $mode == short ]]; then
    campaign=short-distributed-300-20k
    profile_count=1000
    replicates=20
    min_free_gib=400
    archive_min_free_gib=400
else
    campaign=full-distributed
    profile_count=2000
    replicates=20
    min_free_gib=2048
    archive_min_free_gib=1536
fi
store_root=${DATASET_STORE_DIR:-$root/results}
campaign_dir="$store_root/$campaign"
state_dir="$root/results/$campaign-control"
archive_dir=${RAW_ARCHIVE_DIR:-$store_root/$campaign-raw-archives}
bundle="$state_dir/worker-bundle.tar.gz"
ranges="$state_dir/ranges.tsv"
mkdir -p "$state_dir"

run_limited() {
    python3 scripts/dataset/run_limited.py "$@"
}

collector_usage_kib() {
    local path
    {
        for path in "$campaign_dir" "$archive_dir" "$store_root/$campaign.tar.gz" "$store_root/$campaign.tar.gz.incoming"; do
            if [[ -e "$path" ]]; then du -sk "$path"; fi
        done
        for path in "$root/results/$campaign"-*; do
            if [[ -e "$path" && "$path" != "$archive_dir" ]]; then du -sk "$path"; fi
        done
    } | awk '{sum += $1} END {print sum+0}'
}

check_collector_cap() {
    if [[ $mode == short ]]; then
        local used_kib
        used_kib=$(collector_usage_kib)
        if (( used_kib >= 488281250 - 40 * 1024 * 1024 )); then
            echo "collector is within 40 GiB of its 500 GB peak cap; stopping downloads" >&2
            return 1
        fi
    fi
}

retrieved_count() {
    local count=0 instance zone project start stop part marker archive worker_dir
    while IFS=$'\t' read -r instance zone project start stop; do
        worker_dir="$archive_dir/worker-$start-$stop"
        for (( part=start; part<stop; part++ )); do
            marker=$(printf '%s/part-%05d.done' "$worker_dir" "$part")
            archive=$(printf '%s/part-%05d.tar.gz' "$worker_dir" "$part")
            [[ ! -f "$marker" || ! -s "$archive" || ! -s "$worker_dir/source-hashes.json" ]] || (( count+=1 ))
        done
    done < "$ranges"
    echo "$count"
}

prepare() {
    python3 -c 'import numpy, matplotlib' || {
        echo "collector Python needs numpy and matplotlib; activate its dataset virtualenv" >&2
        exit 1
    }
    mkdir -p "$store_root"
    store_free_kib=$(df -Pk "$store_root" | awk 'NR==2 {print $4}')
    if (( store_free_kib < min_free_gib * 1024 * 1024 )); then
        echo "local dataset store needs at least $min_free_gib GiB free for $mode mode" >&2
        exit 1
    fi
    if [[ $mode == short ]]; then
        python3 - "$commands_file" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, 'scripts/dataset')
from vm_inventory import parse
count = len(parse(Path(sys.argv[1])))
if count < 10:
    raise SystemExit(f'20,000-run short mode needs 10 comparable VMs; inventory has {count}')
PY
    fi
    simulator="$root/ns-3-dev/build/scratch/sat-bs-handset/ns3.48-sat-bs-handset-optimized"
    if ! python3 - "$simulator" <<'PY'
import sys
from pathlib import Path
path=Path(sys.argv[1])
raise SystemExit(0 if path.is_file() and path.open('rb').read(4)==b'\x7fELF' else 1)
PY
    then
        artifact="$root/artifacts/linux-simulator-ns3.48.tar.gz"
        [[ -s "$artifact" ]] || { echo "missing Linux simulator artifact: $artifact" >&2; exit 1; }
        tar -C "$root" -xzf "$artifact"
    fi
    if [[ ! -f "$campaign_dir/manifest.json" ]]; then
        python3 scripts/dataset/run_dataset.py --preset full --stream --observation-seconds 300 \
            --profile-count "$profile_count" --replicates "$replicates" \
            --out-dir "$campaign_dir" --run-prefix "$campaign" --plan
    fi
    python3 - "$campaign_dir" "$profile_count" "$replicates" <<'PY'
import json,sys
from pathlib import Path
manifest=json.loads((Path(sys.argv[1])/'manifest.json').read_text())
if (manifest['settings'].get('observationSeconds') != 300 or
    manifest['recordWindowS'] != [20, 320] or
    manifest['settings']['profileCount'] != int(sys.argv[2]) or
    manifest['settings']['replicates'] != int(sys.argv[3])):
    raise SystemExit('existing campaign differs from selected mode')
PY
    python3 scripts/dataset/vm_inventory.py --commands "$commands_file" \
        --campaign "$campaign_dir" --batch-runs 64 --calibration-runs 64 > "$ranges.tmp"
    mv "$ranges.tmp" "$ranges"
    tar -C "$root" -czf "$bundle.tmp" \
        scripts/dataset scratch patches experiments/queue-calibration/reference.json \
        ns-3-dev/build/lib \
        ns-3-dev/build/scratch/sat-bs-handset/ns3.48-sat-bs-handset-optimized
    mv "$bundle.tmp" "$bundle"
    echo "prepared $(wc -l < "$ranges") workers; bundle: $bundle"
    cat "$ranges"
}

check_ready() {
    [[ -s "$ranges" && -s "$bundle" ]] || {
        echo "run prepare first" >&2
        exit 2
    }
    command -v gcloud >/dev/null || {
        echo "gcloud is not installed in this shell" >&2
        exit 2
    }
}

check_vms() {
    check_ready
    local instance zone project start stop failures=0 remote cores free_kib remote_min_gib
    if [[ $mode == short ]]; then remote_min_gib=70; else remote_min_gib=300; fi
    while IFS=$'\t' read -r instance zone project start stop; do
        echo "checking SSH: $instance ($zone, $project)"
        if ! remote=$(run_limited 120 gcloud compute ssh --zone "$zone" --project "$project" "$instance" \
            --command='printf "cores="; nproc; printf "free_kib="; df -Pk ~ | awk "NR==2 {print \$4}"'); then
            echo "SSH check failed: $instance" >&2
            failures=$((failures+1))
            continue
        fi
        echo "$remote"
        cores=$(printf '%s\n' "$remote" | sed -n 's/^cores=//p' | tail -n 1)
        free_kib=$(printf '%s\n' "$remote" | sed -n 's/^free_kib=//p' | tail -n 1)
        if [[ ! $cores =~ ^[0-9]+$ || ! $free_kib =~ ^[0-9]+$ ]] ||
           (( cores < 64 || free_kib < remote_min_gib * 1024 * 1024 )); then
            echo "VM $instance needs at least 64 cores and $remote_min_gib GiB free" >&2
            failures=$((failures+1))
        fi
    done < "$ranges"
    (( failures == 0 )) || { echo "$failures VM(s) failed SSH preflight; no workers launched" >&2; return 1; }
    echo "all VMs are accessible"
}

deploy_one() {
    local instance=$1 zone=$2 project=$3 start=$4 stop=$5
    local remote_bundle="${instance}:~/wireless-simulation-worker-bundle.tar.gz"
    run_limited 1800 gcloud compute scp --zone "$zone" --project "$project" \
        "$bundle" "$remote_bundle"
    run_limited 120 gcloud compute ssh --zone "$zone" --project "$project" "$instance" \
        --command="mkdir -p ~/wireless-simulation-worker && tar -xzf ~/wireless-simulation-worker-bundle.tar.gz -C ~/wireless-simulation-worker"
    run_limited 120 gcloud compute ssh --zone "$zone" --project "$project" "$instance" \
        --command="bash ~/wireless-simulation-worker/scripts/dataset/launch_worker.sh $start $stop 64 $campaign $mode"
    echo "launched $instance parts [$start, $stop)"
}

deploy() {
    check_vms
    local pids=()
    while IFS=$'\t' read -r instance zone project start stop; do
        deploy_one "$instance" "$zone" "$project" "$start" "$stop" \
            > "$state_dir/deploy-$start-$stop.log" 2>&1 &
        pids+=("$!")
        if (( ${#pids[@]} >= 4 )); then
            wait "${pids[0]}"
            pids=("${pids[@]:1}")
        fi
    done < "$ranges"
    for pid in "${pids[@]}"; do wait "$pid"; done
    echo "all workers launched; use status or collect to follow their jobs"
}

status() {
    check_ready
    if [[ -f "$campaign_dir/stream-progress.json" ]]; then
        python3 - "$campaign_dir" <<'PY'
import json,sys
from pathlib import Path
p=json.loads((Path(sys.argv[1])/'stream-progress.json').read_text())
print(f"local tokens: {len(p['parts'])} parts complete")
PY
    fi
    if [[ -d "$archive_dir" ]]; then
        echo "local raw archives: $(retrieved_count) parts retrieved"
        df -h "$archive_dir"
    fi
    while IFS=$'\t' read -r instance zone project start stop; do
        echo "=== $instance parts [$start, $stop) ==="
        run_limited 120 gcloud compute ssh --zone "$zone" --project "$project" "$instance" \
            --command="bash ~/wireless-simulation-worker/scripts/dataset/worker_status.sh $start $stop $campaign" || true
    done < "$ranges"
}

collect() {
    check_ready
    mkdir -p "$archive_dir"
    if ! compgen -G "$archive_dir/worker-*" >/dev/null; then
        archive_free_kib=$(df -Pk "$archive_dir" | awk 'NR==2 {print $4}')
        if (( archive_free_kib < archive_min_free_gib * 1024 * 1024 )); then
            echo "raw archives need at least $archive_min_free_gib GiB free for $mode mode" >&2
            exit 1
        fi
    fi
    lock_dir="$state_dir/collect.lock.d"
    if [[ -d "$lock_dir" ]]; then
        previous_pid=$(cat "$lock_dir/pid" 2>/dev/null || echo 0)
        if kill -0 "$previous_pid" 2>/dev/null; then
            echo "another collector is already running as PID $previous_pid" >&2
            exit 1
        fi
        rm -f "$lock_dir/pid"
        rmdir "$lock_dir"
    fi
    mkdir "$lock_dir"
    echo "$$" > "$lock_dir/pid"
    rm -f "$state_dir/monitor.failed"
    cleanup_collector() {
        local code=$?
        if (( code != 0 )); then
            touch "$state_dir/monitor.failed"
            if [[ -n "${tokenizer_pid:-}" ]]; then kill "$tokenizer_pid" 2>/dev/null || true; fi
        fi
        rm -f "$lock_dir/pid"
        rmdir "$lock_dir" 2>/dev/null || true
    }
    trap cleanup_collector EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    bash scripts/dataset/local_tokenize.sh "$campaign_dir" "$archive_dir" "$ranges" "$state_dir" \
        > "$state_dir/tokenize.log" 2>&1 &
    tokenizer_pid=$!
    total_parts=$(python3 - "$campaign_dir" <<'PY'
import json,sys
from pathlib import Path
sys.path.insert(0,'scripts/dataset')
from stream_parts import planned_parts
manifest=json.loads((Path(sys.argv[1])/'manifest.json').read_text())
print(len(planned_parts(manifest,64,64)[0]))
PY
)
    while true; do
        if ! kill -0 "$tokenizer_pid" 2>/dev/null; then
            wait "$tokenizer_pid"
            break
        fi
        while IFS=$'\t' read -r instance zone project start stop; do
            worker_dir="$archive_dir/worker-$start-$stop"
            mkdir -p "$worker_dir"
            if ! remote=$(run_limited 120 gcloud compute ssh --zone "$zone" --project "$project" "$instance" \
                --command="bash ~/wireless-simulation-worker/scripts/dataset/worker_status.sh $start $stop $campaign" \
                2>> "$state_dir/heartbeat-errors.log"); then
                echo "heartbeat connection failed for $instance; retrying" >&2
                continue
            fi
            alive=0
            exit_code=pending
            heartbeat=0
            current=none
            while IFS= read -r line; do
                case "$line" in
                    alive=*) alive=${line#alive=} ;;
                    exit=*) exit_code=${line#exit=} ;;
                    heartbeat=*) heartbeat=${line#heartbeat=} ;;
                    current=*) current=${line#current=} ;;
                    done=*)
                        name=${line#done=}
                        [[ "$name" =~ ^[0-9]{5}\.done$ ]] || continue
                        part=$((10#${name%.done}))
                        (( part >= start && part < stop )) || continue
                        archive=$(printf '%s/part-%05d.tar.gz' "$worker_dir" "$part")
                        marker=$(printf '%s/part-%05d.done' "$worker_dir" "$part")
                        if [[ ! -s "$archive" || ! -f "$marker" ]]; then
                            if [[ ! -s "$worker_dir/source-hashes.json" ]]; then
                                if ! run_limited 1800 gcloud compute scp --zone "$zone" --project "$project" \
                                    "${instance}:~/wireless-simulation-worker/results/$campaign/worker-$start-$stop/source-hashes.json" \
                                    "$worker_dir/source-hashes.json.incoming"; then
                                    echo "source-hash transfer failed for $instance; retrying" >&2
                                    continue
                                fi
                                mv "$worker_dir/source-hashes.json.incoming" "$worker_dir/source-hashes.json"
                            fi
                            check_collector_cap
                            if ! run_limited 120 gcloud compute scp --zone "$zone" --project "$project" \
                                "${instance}:~/wireless-simulation-worker/results/$campaign/worker-$start-$stop/part-$(printf '%05d' "$part").done" \
                                "$marker.incoming"; then
                                echo "part $part marker transfer failed from $instance; retrying" >&2
                                continue
                            fi
                            if ! run_limited 1800 gcloud compute scp --zone "$zone" --project "$project" \
                                "${instance}:~/wireless-simulation-worker/results/$campaign/worker-$start-$stop/part-$(printf '%05d' "$part").tar.gz" \
                                "$archive.incoming"; then
                                echo "part $part transfer failed from $instance; retrying" >&2
                                continue
                            fi
                            if ! python3 scripts/dataset/verify_raw_part.py "$archive.incoming" "$marker.incoming"; then
                                echo "part $part failed checksum from $instance; retrying" >&2
                                continue
                            fi
                            mv "$archive.incoming" "$archive"
                            mv "$marker.incoming" "$marker"
                            echo "retrieved part $part from $instance"
                        fi
                        if [[ -s "$archive" && -f "$marker" ]]; then
                            remote_part="~/wireless-simulation-worker/results/$campaign/worker-$start-$stop/part-$(printf '%05d' "$part")"
                            if ! run_limited 120 gcloud compute ssh --zone "$zone" --project "$project" "$instance" \
                                --command="touch ${remote_part}.ack && rm -f ${remote_part}.tar.gz ${remote_part}.done"; then
                                echo "remote cleanup failed for part $part on $instance; retrying" >&2
                            fi
                        fi
                        ;;
                esac
            done <<< "$remote"
            if [[ "$alive" == 0 && "$exit_code" != 0 ]]; then
                [[ "$current" =~ ^[0-9]+$ ]] || current=setup
                retries_file="$state_dir/retries-$start-$stop-$current"
                retries=$(cat "$retries_file" 2>/dev/null || echo 0)
                if (( retries >= 3 )); then
                    echo "worker $instance failed after three restarts; inspect its log" >&2
                    touch "$state_dir/monitor.failed"
                    exit 1
                fi
                echo "$((retries+1))" > "$retries_file"
                echo "restarting $instance at unfinished part; attempt $((retries+1))"
                run_limited 120 gcloud compute ssh --zone "$zone" --project "$project" "$instance" \
                    --command="bash ~/wireless-simulation-worker/scripts/dataset/launch_worker.sh $start $stop 64 $campaign $mode"
            elif [[ "$alive" == 1 && "$heartbeat" =~ ^[0-9]+$ ]] && \
                 (( $(date +%s) - heartbeat > 180 )); then
                echo "stale heartbeat on $instance; process is still alive" >&2
            fi
        done < "$ranges"
        downloaded=$(retrieved_count)
        echo "heartbeat: $downloaded/$total_parts raw parts retrieved"
        if (( downloaded >= total_parts )); then
            wait "$tokenizer_pid"
            break
        fi
        sleep 60
    done
    trap - INT TERM
    [[ -f "$campaign_dir/default-dataset.json" ]] || { echo "dataset finalization is missing" >&2; exit 1; }
    package="$store_root/$campaign.tar.gz"
    if [[ ! -s "$package" ]]; then
        rm -f "$package.incoming"
        if [[ $mode == short ]]; then
            used_kib=$(collector_usage_kib)
            campaign_kib=$(du -sk "$campaign_dir" | awk '{print $1}')
            if (( used_kib + campaign_kib >= 488281250 )); then
                echo "compressed package would exceed the 500 GB collector cap" >&2
                exit 1
            fi
        fi
        tar -C "$store_root" -czf "$package.incoming" "$campaign"
        tar -tzf "$package.incoming" >/dev/null
        mv "$package.incoming" "$package"
    fi
    echo "dataset ready: $campaign_dir/default-dataset.json"
    echo "compressed dataset: $package"
    echo "original metric traces retained in $archive_dir/"
}

case "$phase" in
    all) prepare; deploy; collect ;;
    prepare) prepare ;;
    check-vms) check_vms ;;
    deploy) deploy ;;
    collect) collect ;;
    status) status ;;
    *) usage ;;
esac
