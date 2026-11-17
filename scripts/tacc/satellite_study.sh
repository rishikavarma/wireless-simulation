#!/usr/bin/env bash
# Satellite (sat-bs-handset) dataset generation and UNUM training on TACC (Vista,
# Lonestar6 or Stampede3). Run on a TACC login node from the repository copy in $WORK
# (scripts/tacc/sync.sh puts it there).
#
#   bash scripts/tacc/satellite_study.sh info      # system, allocation, queues, state; changes nothing
#   bash scripts/tacc/satellite_study.sh setup     # login node: venv, ns-3.48 + 5G-LENA v5.1 sources
#   bash scripts/tacc/satellite_study.sh build     # sbatch: patch, build, smoke run + tokenization
#   bash scripts/tacc/satellite_study.sh pilot     # sbatch: 150-run streamed dataset (end-to-end check)
#   bash scripts/tacc/satellite_study.sh dataset   # sbatch: a campaign; DATASET_ARGS selects it
#   bash scripts/tacc/satellite_study.sh train     # sbatch: Transformer delta/absolute, MLP, LSTM on DATASET
#   bash scripts/tacc/satellite_study.sh status
#   bash scripts/tacc/satellite_study.sh write     # write the batch scripts only
#
# Environment: ACCOUNT (Slurm -A), DATASET (a finalized windows directory; default: the
# pilot's default dataset), TRAIN=smoke|full (default full: the 1,505-run comparison
# budget), GPU=1 (train on the GPU queue), DATASET_ARGS (run_dataset.py arguments for
# `dataset`, e.g. "--preset full --stream --design-file scripts/dataset/short_dataset.json"),
# DATASET_NODES/HOURS, AFTER (job id the submitted job waits for). Data live in $SCRATCH/wireless-simulation (purged after 10 idle days).
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
SYSTEM=${TACC_SYSTEM:-unknown}
BASE=${SAT_SCRATCH:-$SCRATCH/wireless-simulation}
VENV=${VENV:-$BASE/venv}
PY="$VENV/bin/python"
SLURM_DIR="$BASE/slurm"
GPU=${GPU:-0}
TRAIN=${TRAIN:-full}

case "$SYSTEM" in
  vista)     CPU_Q=gg;     DEV_Q=gh-dev;      GPU_Q=gh;       CORES=144; GPUS=1; DEV_CORES=72 ;;
  ls6)       CPU_Q=normal; DEV_Q=development; GPU_Q=gpu-a100; CORES=128; GPUS=3; DEV_CORES=128 ;;
  stampede3) CPU_Q=icx;    DEV_Q=skx-dev;     GPU_Q=h100;     CORES=80;  GPUS=4; DEV_CORES=48 ;;
  *) echo "unsupported TACC_SYSTEM '$SYSTEM'" >&2; exit 2 ;;
esac
# 5G-LENA runs need a few GB each; keep ~3.5 GB of node memory per concurrent simulation.
SIM_JOBS=${SIM_JOBS:-$([[ $SYSTEM == vista ]] && echo 64 || echo $(( CORES / 2 )))}

modules() {
  case "$SYSTEM" in
    vista) cat <<'EOF'
module load gcc/14.2.0 2>/dev/null || module load gcc
module load cmake/3.31.5 2>/dev/null || true
module load eigen/3.4.0 2>/dev/null || true
module load pylauncher 2>/dev/null || true
EOF
    ;;
    # Lonestar6 defaults to Intel; its python3 module needs Intel's runtime, so with GCC
    # use the standalone python/3.12.11 module (the venv is built from it).
    ls6) cat <<'EOF'
module load gcc/13.2.0 2>/dev/null || module load gcc
module load cmake/3.31.5 2>/dev/null || true
module load eigen/3.4.0 2>/dev/null || true
module load python/3.12.11
module load pylauncher 2>/dev/null || true
EOF
    ;;
    *) cat <<'EOF'
module load gcc 2>/dev/null || true
module load cmake 2>/dev/null || true
module load eigen 2>/dev/null || true
module load python3 2>/dev/null || true
module load pylauncher 2>/dev/null || true
EOF
    ;;
  esac
}

write_job() {  # NAME QUEUE NODES HOURS BODY
  local name=$1 queue=$2 nodes=$3 hours=$4 body=$5 per_node=$CORES path="$SLURM_DIR/$1.sbatch"
  [[ $queue == "$GPU_Q" ]] && per_node=$GPUS
  [[ $queue == "$DEV_Q" ]] && per_node=$DEV_CORES
  mkdir -p "$SLURM_DIR"
  cat > "$path" <<EOF
#!/bin/bash
#SBATCH -J sat-$name
#SBATCH -o $SLURM_DIR/$name.o%j
#SBATCH -e $SLURM_DIR/$name.o%j
#SBATCH -p $queue
#SBATCH -N $nodes
#SBATCH -n $((nodes * per_node))
#SBATCH -t $hours:00:00
$([[ -n "${ACCOUNT:-}" ]] && echo "#SBATCH -A $ACCOUNT")
set -euo pipefail
$(modules)
# Eigen from the module, for ns-3/5G-LENA's find_package(Eigen3).
[[ -n "\${TACC_EIGEN_DIR:-}" ]] && export CMAKE_PREFIX_PATH="\$TACC_EIGEN_DIR\${CMAKE_PREFIX_PATH:+:\$CMAKE_PREFIX_PATH}"
source "$VENV/bin/activate"
export PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/mpl-\$USER SETUP_KEEP_SHELL_RC=1
cd "$REPO"
$body
EOF
  echo "$path"
}

info() {
  echo "system $SYSTEM; cpu=$CPU_Q ($CORES cores) dev=$DEV_Q gpu=$GPU_Q; repo $REPO; data $BASE"
  /usr/local/etc/taccinfo 2>/dev/null || true
  [[ -x "$PY" ]] && "$PY" -c "import numpy, torch; print('numpy', numpy.__version__, 'torch', torch.__version__)" || echo "venv not set up yet"
  ls "$REPO"/ns-3-dev/build/scratch/sat-bs-handset/*sat-bs-handset* 2>/dev/null | head -1 || echo "sat-bs-handset not built yet"
}

setup() {
  eval "$(modules)"
  mkdir -p "$BASE"
  [[ -x "$PY" ]] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  if [[ $SYSTEM == vista ]]; then
    "$VENV/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cu129
  else
    "$VENV/bin/pip" install -q 'torch>=2.2,<3'
  fi
  "$VENV/bin/pip" install -q numpy matplotlib 'pandas>=2' paramiko
  # Chronos and Transformers are only for the foundation-model baselines; optional here.
  "$VENV/bin/pip" install -q 'transformers>=4.41,<6' 'chronos-forecasting>=2.2,<3' 'accelerate>=1.1,<2' \
    'einops>=0.7,<1' || echo "note: chronos/transformers not installed (not needed by train_unum_buckets.py)"
  # Compute nodes may have no outbound network: fetch sources here, build in a batch job.
  [[ -d "$REPO/ns-3-dev/.git" ]] || git clone -q --depth 1 --branch ns-3.48 https://gitlab.com/nsnam/ns-3-dev.git "$REPO/ns-3-dev"
  mkdir -p "$REPO/ns-3-dev/contrib"
  [[ -d "$REPO/ns-3-dev/contrib/nr/.git" ]] || git clone -q --depth 1 --branch v5.1 https://gitlab.com/cttc-lena/nr.git "$REPO/ns-3-dev/contrib/nr"
  "$PY" -c "import numpy, torch; print('venv ok: torch', torch.__version__)"
}

build_job() {
  # setup.sh: --simulator-only skips pip and its smoke step (both assume a non-venv
  # Python); the smoke run and tokenization are repeated here inside the venv.
  write_job build "$CPU_Q" 1 03 "bash scripts/setup.sh --skip-deps --simulator-only
cd ns-3-dev
./ns3 run 'sat-bs-handset --config=scratch/sat-bs-handset/config/smoke.json'
cd '$REPO'
prefix=\$(python -c 'import json; print(json.load(open(\"scratch/sat-bs-handset/config/smoke.json\"))[\"output\"][\"prefix\"])')
python scripts/dataset/bucketize_metrics.py --config scratch/sat-bs-handset/config/smoke.json \\
  --tcp results/\$prefix/\$prefix-tcp.csv --sinr-dl results/\$prefix/\$prefix-sinr-dl.csv \\
  --sinr-ul results/\$prefix/\$prefix-sinr-ul.csv --num-buckets 50
echo 'smoke run and tokenization passed'"
}

dataset_job() {  # NAME NODES HOURS ARGS
  write_job "$1" "$CPU_Q" "$2" "$3" "python scripts/dataset/run_dataset.py $4 --jobs $SIM_JOBS"
}

pilot_job() {
  dataset_job pilot 1 06 "--preset full --stream --profile-count 50 --replicates 3 --observation-seconds 20 \
--out-dir '$BASE/pilot' --run-prefix tacc-pilot --batch-runs $SIM_JOBS --calibration-runs 12"
}

train_job() {
  local cmds="$SLURM_DIR/train.cmds" budget dataset out device threads cores
  if [[ $TRAIN == smoke ]]; then
    budget="--trials compact --epochs 1 --patience 1 --batch-size 128 --train-windows-per-epoch 2000 --validation-windows 500 --test-windows 2000"
  else  # 1,505-run comparison training budget
    budget="--trials w256 --epochs 60 --patience 60 --batch-size 256 --train-windows-per-epoch 250000 --validation-windows 20000 --test-windows 200000"
  fi
  # The streamed campaign's default sample-split windows (default-dataset.json "path").
  dataset=${DATASET:-$BASE/pilot/windows-random-c10-p10-step1-10}
  out="$BASE/models/$TRAIN"
  if [[ $GPU == 1 ]]; then device=cuda; threads=8; cores=1; else device=cpu; threads=$(( CORES / 4 )); cores=$threads; fi
  local lines="" spec arch target
  for spec in transformer:delta transformer:absolute mlp:absolute lstm:absolute; do
    IFS=: read -r arch target <<<"$spec"
    lines+="$([[ $GPU == 1 ]] || echo "$cores,")env OMP_NUM_THREADS=$threads python $REPO/scripts/models/train_unum_buckets.py --dataset \$DATASET_DIR --output $out/$arch-$target --architecture $arch --target $target $budget --device $device > $out/$arch-$target.log 2>&1"$'\n'
  done
  local launcher
  if [[ $GPU == 1 ]]; then
    launcher="python -c \"import os, pylauncher; pylauncher.GPULauncher('$cmds', gpuspernode=$GPUS, workdir='$SLURM_DIR/pyl-train-'+os.environ['SLURM_JOB_ID'], debug='job')\""
  else
    launcher="python -c \"import os, pylauncher; pylauncher.ClassicLauncher('$cmds', cores='file', workdir='$SLURM_DIR/pyl-train-'+os.environ['SLURM_JOB_ID'], debug='job')\""
  fi
  local queue=$CPU_Q nodes=1 hours=$([[ $TRAIN == smoke ]] && echo 01 || echo 48)
  if [[ $GPU == 1 ]]; then queue=$GPU_Q; nodes=$(( (4 + GPUS - 1) / GPUS )); fi
  write_job "train-$TRAIN" "$queue" "$nodes" "$hours" "DATASET_DIR=$dataset
[[ -f \"\$DATASET_DIR/dataset-index.json\" ]] || { echo \"not a finalized windows dataset: \$DATASET_DIR\"; exit 1; }
mkdir -p '$out'
# Finished models (comparison.json present) are skipped on resubmission.
cat > '$cmds' <<'CMDS'
${lines}CMDS
sed -i \"s|\\\$DATASET_DIR|\$DATASET_DIR|g\" '$cmds'
python - '$cmds' <<'PYEOF'
import re, sys, pathlib
path = pathlib.Path(sys.argv[1])
keep = [l for l in path.read_text().splitlines()
        if not (pathlib.Path(re.search(r'--output (\\S+)', l).group(1)) / 'comparison.json').exists()]
path.write_text(''.join(l + '\\n' for l in keep))
print(len(keep), 'training jobs to run')
PYEOF
[[ -s '$cmds' ]] && $launcher"
}

status() {
  squeue -u "$USER" -o '%.10i %.14j %.8P %.8T %.10M %.5D %R' || true
  ls "$BASE"/models/*/*/comparison.json 2>/dev/null || true
  [[ -f "$BASE/pilot/default-dataset.json" ]] && cat "$BASE/pilot/default-dataset.json" || true
}

# AFTER=<job id> makes a submitted job wait for that job. TACC's sbatch wrapper also prints
# "--> Verifying ..." lines on stdout, so keep only the job id.
submit() { sbatch --parsable ${AFTER:+--dependency=afterok:$AFTER} "$1" | grep -Eo '^[0-9]+' | tail -n 1; }

case "${1:-}" in
  info) info ;;
  setup) setup ;;
  build) submit "$(build_job)" ;;
  pilot) submit "$(pilot_job)" ;;
  dataset) [[ -n "${DATASET_ARGS:-}" ]] || { echo "set DATASET_ARGS" >&2; exit 2; }
           submit "$(dataset_job dataset "${DATASET_NODES:-1}" "${DATASET_HOURS:-48}" "$DATASET_ARGS")" ;;
  train) submit "$(train_job)" ;;
  status) status ;;
  write) build_job; pilot_job; train_job ;;
  *) sed -n '2,22p' "$0"; exit 2 ;;
esac
