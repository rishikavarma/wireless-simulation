# Wireless simulation (ns-3 satellite)

Wi-Fi access + 5G-LENA NR NTN feeder with on-satellite compute in ns-3.

```
UE (STA)  -- Wi-Fi / Friis --  Base station (AP + NR gNB)  -- NR NTN --  Satellite (NR UE)
               10.1.2.0/24                                      compute + reply
```

- **Access (UE ↔ BS):** 802.11. The UE never has a satellite radio.
- **Feeder (BS ↔ sat):** 5G-LENA NR with a 3GPP NTN channel. The satellite is the NR UE (IP endpoint in space); the base station is the ground gNB + Wi-Fi gateway.
- **Traffic:** UE request → BS gateway → sat compute → BS gateway → same UE.

Ubuntu 22.04 (or WSL), C++ toolchain, CMake >= 3.25, Ninja, Python 3, Git, Eigen3 (`libeigen3-dev`), and sudo for `apt-get`.

## Setup

From the repo root:

```bash
./scripts/setup.sh
```

This installs packages, clones `ns-3-dev` if needed, clones the **5G-LENA `nr`** module into `ns-3-dev/contrib/nr`, puts `$HOME/.local/bin` on `PATH` (and persists it in `~/.bashrc` for pip CMake), symlinks `scratch/sat-bs-handset/` into ns-3, **applies local ns-3 patches**, builds (optimized; ns-3 examples off), and smoke-tests a short run.

```bash
./scripts/setup.sh --skip-deps      # skip apt-get
./scripts/setup.sh --skip-verify    # skip the smoke test
./scripts/setup.sh --help
```

Use `NS3_DIR` if ns-3 lives somewhere other than `./ns-3-dev`:

```bash
NS3_DIR=/path/to/ns-3-dev ./scripts/setup.sh --skip-deps
```

`ns-3-dev/` is gitignored — it is an upstream clone, not part of this repo’s source.

### Local ns-3 patches

`./scripts/setup.sh` applies these after cloning (skips if already applied). To apply or reverse by hand from the repo root:

```bash
git -C ns-3-dev apply ../ns3-icmp-no-route-recursion.patch
git -C ns-3-dev apply ../ns3-ping-seq-on-failed-send.patch

git -C ns-3-dev apply -R ../ns3-icmp-no-route-recursion.patch
git -C ns-3-dev apply -R ../ns3-ping-seq-on-failed-send.patch
```

| Patch | What it fixes |
|---|---|
| `ns3-icmp-no-route-recursion.patch` | RFC 1812 guard: stop recursive ICMP net-unreachable storms when a route drops an ICMP error (can stall sims after fading / route loss). |
| `ns3-ping-seq-on-failed-send.patch` | `Ping` only advances ICMP sequence / `m_sent` after a successful `SendTo`. Without this, early pings that fail for lack of a route can abort on `m_sent.at(seq)`. |

The next scenario rebuild picks up the affected ns-3 libraries automatically.

## Run

```bash
cd ns-3-dev
./ns3 run sat-bs-handset
```

Useful command-line knobs (see `scratch/sat-bs-handset/sat-bs-handset.cc` for the full list):

| Flag | Meaning | Default |
|---|---|---|
| `--simTime` | Simulation duration (s) | 120 |
| `--warmUp` | Seconds before the UE app starts | 10 |
| `--appDrain` | Stop the app this many seconds before `simTime` | 5 |
| `--satAltitudeKm` | LEO altitude above Earth (km), range [550, 1200] | 550 |
| `--satInclinationDeg` | LEO inclination (degrees) | 53 |
| `--ntnScenario` | 3GPP NTN scenario for `NrChannelHelper` | `NTN-Rural` |
| `--traffic` | UE traffic pattern: `cbr`, `onoff`, or `bursty` | `cbr` |
| `--dataRate` | Peak UDP request rate while ON | `30Mbps` |
| `--onTime` | ON duration for onoff/bursty | onoff=`1s`, bursty=`100ms` |
| `--offTime` | OFF duration for onoff/bursty | onoff=`1s`, bursty=`400ms` |
| `--packetSize` | UDP payload size (bytes) | 1400 |
| `--computeDelayUs` | On-satellite compute delay before reply (µs) | 0 |
| `--ueDistanceM` | UE distance from the BS (m) | 100 |
| `--realisticPower` | Compensate array gain for a realistic NTN link budget | false |
| `--nrTraces` | Enable 5G-LENA traces | false |

`dataRate` is the **peak** send rate while the UE is ON. For `onoff` (default duty 0.5) and `bursty` (default duty 0.2) the long-run average offered load is lower.

Example short runs:

```bash
./ns3 run "sat-bs-handset --simTime=30 --warmUp=8 --appDrain=2 --traffic=cbr --dataRate=5Mbps"
./ns3 run "sat-bs-handset --simTime=30 --warmUp=8 --appDrain=2 --traffic=onoff --dataRate=10Mbps"
./ns3 run "sat-bs-handset --simTime=30 --warmUp=8 --appDrain=2 --traffic=bursty --dataRate=25Mbps"
```

The process exits **0** when the UE receives at least 90% of the replies it requested (and sent > 0); otherwise **1**.

## Layout

```
scratch/sat-bs-handset/                # multi-file scenario (symlinked into ns-3-dev/scratch/)
  sat-bs-handset.cc                    # main: CLI, wire nodes/apps, run
  CMakeLists.txt                       # nested sources (required for subdirs)
  ue/                                  # ground UE apps (add more UEs here)
    ue-offload-client.{h,cc}
  bs/                                  # base-station apps (add more BSs here)
    bs-offload-gateway.{h,cc}
  satellite/                           # on-satellite apps
    sat-compute-server.{h,cc}
  topology/                            # per-role installers (mobility, NR, Wi-Fi)
    mobility.{h,cc}
    nr-ntn-feeder.{h,cc}
    wifi-access.{h,cc}
  common/                              # shared NTN helpers
    ntn-helpers.{h,cc}
scripts/setup.sh                       # deps, clone ns-3 + nr, patches, build, smoke test
ns3-icmp-no-route-recursion.patch      # local ns-3 fix (ICMP no-route recursion)
ns3-ping-seq-on-failed-send.patch      # local ns-3 fix (Ping seq on failed send)
```
