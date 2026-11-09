#!/usr/bin/env bash
# Build the Linux simulator from pinned source and maintained patches for VM staging.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
[[ $(uname -s) == Linux ]] || { echo 'build_linux_simulator.sh requires Linux' >&2; exit 2; }
target="$root/ns-3-dev/build/scratch/sat-bs-handset/ns3.48-sat-bs-handset-optimized"
stamp="$root/ns-3-dev/build/.wireless-simulator-source.sha256"
source_hash=$(python3 - "$root" <<'PY'
import hashlib
import sys
from pathlib import Path
root = Path(sys.argv[1])
paths = sorted([*root.glob('patches/*.patch'),
                *(p for p in (root/'scratch/sat-bs-handset').rglob('*')
                  if p.suffix in ('.cc', '.h'))])
digest = hashlib.sha256()
for path in paths:
    digest.update(str(path.relative_to(root)).encode() + b'\0')
    digest.update(path.read_bytes())
print(digest.hexdigest())
PY
)
if [[ -x "$target" && -f "$stamp" && $(cat "$stamp") == "$source_hash" ]]; then
    echo "Linux simulator build matches current source"
    exit 0
fi

# The collector may have only an extracted build tree, not an ns-3 Git checkout.
# Keep the source checkout under ignored local/ and copy only the build output.
checkout="$root/local/ns3-build"
if [[ -d "$root/ns-3-dev/.git" ]]; then
    checkout="$root/ns-3-dev"
fi
NS3_DIR="$checkout" bash "$root/scripts/setup.sh" --simulator-only

if [[ "$checkout" != "$root/ns-3-dev" ]]; then
    mkdir -p "$root/ns-3-dev/build/lib" "$root/ns-3-dev/build/scratch/sat-bs-handset"
    cp -a "$checkout/build/lib/." "$root/ns-3-dev/build/lib/"
    cp -a "$checkout/build/scratch/sat-bs-handset/ns3.48-sat-bs-handset-optimized" \
        "$root/ns-3-dev/build/scratch/sat-bs-handset/"
fi
test -x "$target"
printf '%s\n' "$source_hash" > "$stamp"
