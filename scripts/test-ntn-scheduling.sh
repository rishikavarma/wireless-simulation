#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS3_DIR="${NS3_DIR:-$ROOT_DIR/ns-3-dev}"
BIN="$NS3_DIR/build/scratch/sat-bs-handset/ns3.48-sat-bs-handset-optimized"
CFG="$ROOT_DIR/scratch/sat-bs-handset/config/smoke.json"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

(cd "$NS3_DIR" && ./ns3 build sat-bs-handset)
"$BIN" --config="$CFG" >"$LOG" 2>&1

if rg -q 'NS_FATAL|Cannot TX while RX|already receiving|overlap' "$LOG"; then
    cat "$LOG"
    echo "NTN PHY overlap check failed" >&2
    exit 1
fi

python3 - "$LOG" "$ROOT_DIR/results/smoke" <<'PY'
import math
import pathlib
import re
import sys

log = pathlib.Path(sys.argv[1]).read_text()
results = pathlib.Path(sys.argv[2])

timing = re.search(
    r"oneWayDelayS=(\S+) commonTaS=(\S+) ueSpecificTaS=(\S+) "
    r"totalTaS=(\S+) maxRttS=(\S+) ntnKOffsetSlots=(\d+)",
    log,
)
assert timing, "missing NTN timing decomposition"
one_way, common, specific, total, max_rtt = map(float, timing.groups()[:5])
k_offset = int(timing.group(6))
assert math.isclose(common + specific, total, rel_tol=0, abs_tol=1e-12)
assert math.isclose(2 * one_way, total, rel_tol=0, abs_tol=1e-12)
assert k_offset >= math.ceil(max_rtt / 0.001)
final = re.search(r"result bent-pipe final .*oneWayDelayS=(\S+) totalTaS=(\S+)", log)
assert final, "missing final live-geometry timing sample"
final_one_way, final_total = map(float, final.groups())
assert not math.isclose(final_one_way, one_way, rel_tol=0, abs_tol=1e-9)
assert math.isclose(2 * final_one_way, final_total, rel_tol=0, abs_tol=1e-7)
assert "FDD dl=" in log and " ul=" in log
assert "delivery=1" in log

for name in ("smoke-tcp.csv", "smoke-sinr-dl.csv", "smoke-sinr-ul.csv"):
    rows = (results / name).read_text().splitlines()
    assert len(rows) > 2, f"{name} has no samples"

print("NTN scheduling smoke checks passed")
PY
