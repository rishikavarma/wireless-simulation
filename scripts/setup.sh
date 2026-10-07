#!/usr/bin/env bash
#
# One-shot setup for the satellite (sat-bs-handset) ns-3 scenario: install
# build dependencies, clone ns-3-dev + 5G-LENA nr, symlink the scenario,
# configure, build, and smoke-test.
#
# Usage:
#   ./scripts/setup.sh                 # full setup
#   ./scripts/setup.sh --skip-deps     # skip apt-get (already have a toolchain)
#   ./scripts/setup.sh --skip-verify   # skip the smoke-test run
#
# Tested on Ubuntu 22.04 (also works under WSL). Needs sudo for apt-get
# unless --skip-deps.

set -euo pipefail

# Drop leftover ns-3 scenario processes from a previous run.
pkill -f 'sat-bs-handset-optimized' >/dev/null 2>&1 || true
pkill -f 'ns3 run sat-bs-handset' >/dev/null 2>&1 || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
NS3_DIR="${NS3_DIR:-$ROOT_DIR/ns-3-dev}"
SCENARIO_SRC="$ROOT_DIR/scratch/sat-bs-handset"
SCENARIO_LINK="$NS3_DIR/scratch/sat-bs-handset"

SKIP_DEPS=0
SKIP_VERIFY=0

usage() {
    cat <<'EOF'
Usage: ./scripts/setup.sh [options]

Options:
  --skip-deps      Skip apt-get (use if the C++ toolchain is already installed)
  --skip-verify    Skip the smoke-test run after the build
  -h, --help       Show this help

Environment:
  NS3_DIR          ns-3 checkout path (default: ./ns-3-dev at the repo root)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-deps) SKIP_DEPS=1 ;;
        --skip-verify) SKIP_VERIFY=1 ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

# Prefer a user-local CMake (pip install --user) over an older distro one.
export PATH="$HOME/.local/bin:$PATH"

# Persist that PATH so later shells (and ns-3's cmake lookup) find pip tools.
ensure_local_bin_on_path() {
    local rc="$HOME/.bashrc"
    local marker="# wireless-simulation: user-local pip binaries (cmake)"
    local line='export PATH="$HOME/.local/bin:$PATH"'
    if [[ -f "$rc" ]] && grep -Fq "$marker" "$rc"; then
        echo "    $HOME/.local/bin already persisted in $rc"
        return
    fi
    if [[ -f "$rc" ]] && grep -Fq '.local/bin' "$rc"; then
        echo "    $HOME/.local/bin already referenced in $rc"
        return
    fi
    {
        echo ""
        echo "$marker"
        echo "$line"
    } >> "$rc"
    echo "    appended PATH export to $rc (new shells pick this up)"
}

cmake_version_ok() {
    command -v cmake >/dev/null 2>&1 || return 1
    python3 - "$1" <<'PY'
import shutil, subprocess, sys
min_ver = tuple(int(p) for p in sys.argv[1].split("."))
cmake = shutil.which("cmake")
if not cmake:
    sys.exit(1)
out = subprocess.check_output([cmake, "--version"], text=True).splitlines()[0]
ver = tuple(int(p) for p in out.split()[-1].split(".")[:3])
sys.exit(0 if ver >= min_ver else 1)
PY
}

echo "==> Installing system build dependencies"
if [[ "$SKIP_DEPS" -eq 1 ]]; then
    echo "    skipped (--skip-deps)"
else
    sudo apt-get update
    sudo apt-get install -y g++ python3 python3-pip git ninja-build pkg-config libeigen3-dev
fi

echo "==> Ensuring CMake >= 3.25"
if cmake_version_ok 3.25; then
    echo "    using $(command -v cmake) ($(cmake --version | head -n1))"
else
    echo "    distro CMake is missing or too old; installing 3.25+ via pip (user site)"
    python3 -m pip install --user "cmake>=3.25,<3.31"
    export PATH="$HOME/.local/bin:$PATH"
    if ! cmake_version_ok 3.25; then
        echo "error: cmake >= 3.25 is still not on PATH after pip install" >&2
        echo "       try: export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
        exit 1
    fi
    echo "    using $(command -v cmake) ($(cmake --version | head -n1))"
fi

echo "==> Persisting \$HOME/.local/bin on PATH"
ensure_local_bin_on_path

# Latest pair the 5G-LENA README lists as compatible. Master of each tree
# has drifted: ns-3's phased-array channel method takes beamforming vectors
# that current nr master does not pass.
NS3_TAG="ns-3.48"
NR_TAG="v5.1"

pin_checkout() {
    local dir="$1"
    local tag="$2"
    local url="$3"
    if [[ -d "$dir/.git" ]]; then
        git -C "$dir" fetch --depth 1 origin tag "$tag"
        git -C "$dir" checkout -f "$tag"
    elif [[ -e "$dir" ]]; then
        echo "error: $dir exists but is not a git checkout" >&2
        exit 1
    else
        git clone --depth 1 --branch "$tag" "$url" "$dir"
    fi
    echo "    $dir at $(git -C "$dir" describe --tags --always)"
}

echo "==> Checking out ns-3 $NS3_TAG into $NS3_DIR"
pin_checkout "$NS3_DIR" "$NS3_TAG" https://gitlab.com/nsnam/ns-3-dev.git

echo "==> Checking out 5G-LENA nr $NR_TAG into $NS3_DIR/contrib/nr"
mkdir -p "$NS3_DIR/contrib"
pin_checkout "$NS3_DIR/contrib/nr" "$NR_TAG" https://gitlab.com/cttc-lena/nr.git

echo "==> Symlinking scratch scenario into ns-3 scratch/"
mkdir -p "$NS3_DIR/scratch"
if [[ ! -d "$SCENARIO_SRC" ]]; then
    echo "error: scenario source not found: $SCENARIO_SRC" >&2
    exit 1
fi
# Drop a stale file symlink if we previously linked a single .cc.
if [[ -L "$SCENARIO_LINK" || -f "$SCENARIO_LINK" ]]; then
    rm -f "$SCENARIO_LINK"
fi
# Relative target so the link stays valid if the tree is moved together.
rel_target="$(realpath --relative-to="$(dirname "$SCENARIO_LINK")" "$SCENARIO_SRC")"
ln -sfn "$rel_target" "$SCENARIO_LINK"
echo "    $SCENARIO_LINK -> $rel_target"

echo "==> Configuring and building ns-3 (optimized; examples off)"
echo "    this can take several minutes to around an hour depending on hardware"
cd "$NS3_DIR"
# Examples are optional; leaving them off avoids SQLite-dependent NR example
# targets that are unrelated to sat-bs-handset.
./ns3 configure --disable-examples --build-profile=optimized
./ns3 build

if [[ "$SKIP_VERIFY" -eq 1 ]]; then
    echo "==> Skipping smoke test (--skip-verify)"
else
    echo "==> Smoke-testing sat-bs-handset (short run)"
    # Full defaults are 120 s; smoke.json is long enough for attach plus a few replies.
    ./ns3 run "sat-bs-handset --config=scratch/sat-bs-handset/config/smoke.json"
    echo "    smoke test passed"
fi

echo
echo "Setup complete. Next:"
echo "  cd \"${NS3_DIR}\" && ./ns3 run sat-bs-handset"
echo "  # edit scratch/sat-bs-handset/config/config.json"
