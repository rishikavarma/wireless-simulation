#!/usr/bin/env bash
#
# Run the five TCP workloads on the config.json setup (orbit, radio, timing,
# offer rate). Only application.workload and output.prefix change.
# Each run writes results/exp-<workload>/ (TCP trace, SINR traces, summary).
#
# Usage:
#   ./scripts/run-workload-experiment.sh
#
# A workload that misses its success check still keeps its traces. The script
# exits non-zero only when a run produces no TCP trace.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
NS3_DIR="${NS3_DIR:-$ROOT_DIR/ns-3-dev}"
BASE_CFG="$ROOT_DIR/scratch/sat-bs-handset/config/config.json"
EXP_DIR="$ROOT_DIR/results/workload-experiment"
BIN="$NS3_DIR/build/scratch/sat-bs-handset/ns3.48-sat-bs-handset-optimized"

WORKLOADS=(probe file rpc stream iot)

if [[ ! -x "$BIN" ]]; then
    echo "error: ns-3 binary not found: $BIN" >&2
    echo "       run ./scripts/setup.sh first" >&2
    exit 1
fi
if [[ ! -f "$BASE_CFG" ]]; then
    echo "error: base config not found: $BASE_CFG" >&2
    exit 1
fi

mkdir -p "$EXP_DIR/configs" "$EXP_DIR/logs"

write_config() {
    local workload="$1"
    local dest="$2"
    python3 - "$BASE_CFG" "$workload" "$dest" <<'PY'
import pathlib, sys
base, workload, dest = sys.argv[1:]
text = pathlib.Path(base).read_text(encoding="utf-8")
prefix = f"exp-{workload}"
workload_old = '"workload": "probe"'
prefix_old = '"prefix": "sat-bs-handset"'
if text.count(workload_old) != 1 or text.count(prefix_old) != 1:
    sys.exit("config.json no longer has exactly one workload and one prefix field")
text = text.replace(workload_old, f'"workload": "{workload}"', 1)
text = text.replace(prefix_old, f'"prefix": "{prefix}"', 1)
pathlib.Path(dest).write_text(text, encoding="utf-8")
PY
}

run_one() {
    local workload="$1"
    local prefix="exp-${workload}"
    local cfg="$EXP_DIR/configs/${workload}.json"
    local log="$EXP_DIR/logs/${workload}.log"
    local results="$ROOT_DIR/results/${prefix}"
    write_config "$workload" "$cfg"
    echo "==> ${workload} (log ${log})"
    set +e
    (
        cd "$NS3_DIR"
        "$BIN" --config="$cfg"
    ) >"$log" 2>&1
    local status=$?
    set -e
    echo "$status" >"$EXP_DIR/logs/${workload}.exit"
    if [[ ! -f "$results/${prefix}-tcp.csv" ]]; then
        echo "    ${workload}: no TCP trace (ns-3 exit ${status})" >&2
        return 1
    fi
    echo "    ${workload}: trace kept (ns-3 exit ${status})"
    return 0
}

echo "==> Workload experiment: ${WORKLOADS[*]}"
echo "    base config ${BASE_CFG}"
echo "    binary ${BIN}"

pids=()
names=()
for workload in "${WORKLOADS[@]}"; do
    run_one "$workload" &
    pids+=("$!")
    names+=("$workload")
done

failed=0
for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
        echo "error: ${names[$i]} produced no trace" >&2
        failed=1
    fi
done

if [[ "$failed" -ne 0 ]]; then
    echo "Experiment finished with missing traces." >&2
    exit 1
fi
echo "Experiment complete. Traces: $ROOT_DIR/results/exp-<workload>/"
