#!/usr/bin/env bash
# Copy this working tree (uncommitted changes included) to $WORK/wireless-simulation on a TACC system,
# then optionally run a satellite_study.sh action there.
#
#   bash scripts/tacc/sync.sh                 # copy only
#   bash scripts/tacc/sync.sh info            # copy, then run `satellite_study.sh info` remotely
#
# TACC_HOST is an ~/.ssh/config alias (default: tacc) that shares one authenticated
# connection, so the password + MFA token is asked once (see docs/home-wifi/unum-study.md):
#   Host tacc
#     HostName login1.vista.tacc.utexas.edu   # a fixed login node, so the shared socket matches
#     User <tacc-username>
#     ControlMaster auto
#     ControlPath ~/.ssh/cm-%r@%h:%p
#     ControlPersist 8h
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
HOST=${TACC_HOST:-tacc-vista}
REMOTE_WORK=$(ssh "$HOST" 'echo $WORK')
DEST="$REMOTE_WORK/wireless-simulation"
ssh "$HOST" "mkdir -p '$DEST'"
rsync -az --delete \
  --exclude .git --exclude ns-3-dev --exclude results --exclude local --exclude artifacts --exclude .venv --exclude __pycache__ --exclude "*.tar*" --exclude "codex-session-*" --exclude "2026-*" \
  --exclude '*.pdf' --exclude '*.pptx' \
  "$ROOT/" "$HOST:$DEST/"
echo "synced to $HOST:$DEST"
if [[ $# -gt 0 ]]; then
  ssh "$HOST" "cd '$DEST' && bash -l -c 'bash scripts/tacc/satellite_study.sh $*'"
fi
