# Wireless simulation (ns-3 satellite)

Transparent satellite bent-pipe with 5G-LENA NR NTN in ns-3.

```
UE (NR UE)  -- LEO relay --  Base station (ground gNB + app server)
```

- **Access:** Transparent bent-pipe. The gNB stays on the ground. Path loss and delay are the feeder hop plus the service hop through the satellite.
- **Satellite:** Transparent relay. It has no NR device and no IP apps.
- **Traffic:** UE synthetic TCP workload ↔ BS app server (echo / ACK / sized RPC response).

The radio uses a paired FDD cell: BWP 0 is DL-only (20 GHz by default) and
BWP 1 is UL-only (30 GHz by default). Each carrier has its own bent-pipe
channel and direction-specific link budget. Propagation delay is recomputed
from the live gNB–satellite and UE–satellite ranges for every transmission.
UE receive/transmit slot phases follow that moving delay, giving the
transparent-payload timing advance
`commonTA = 2*feederRange/c`, `ueSpecificTA = 2*serviceRange/c`, and
`totalTA = commonTA + ueSpecificTA`. A pass-wide `K_offset` is added to K1/K2
so grants and HARQ feedback are scheduled far enough ahead; N1/N2 remain UE
processing delays.

Ubuntu 22.04 (or WSL), C++ toolchain, CMake >= 3.25, Ninja, Python 3, Git, Eigen3 (`libeigen3-dev`), and sudo for `apt-get`.

## Setup

From the repo root:

```bash
./scripts/setup.sh
```

This installs packages, checks out **ns-3.48** and **5G-LENA nr v5.1** (the pair the nr README lists as compatible), puts `$HOME/.local/bin` on `PATH` (and persists it in `~/.bashrc` for pip CMake), symlinks `scratch/sat-bs-handset/` into ns-3, builds (optimized; ns-3 examples off), and smoke-tests a short run.

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

## Run

```bash
cd ns-3-dev
./ns3 run sat-bs-handset
```

One JSON file, loaded from the ns-3 directory. Edit that instead of passing workload flags:

| File | What it controls |
|---|---|
| `scratch/sat-bs-handset/config/config.json` | Duration, orbit, NTN radio, UE placement, TCP workload, trace output |
| `scratch/sat-bs-handset/config/smoke.json` | Same keys, short schedule used by `scripts/setup.sh` |

```bash
./ns3 run "sat-bs-handset --config=scratch/sat-bs-handset/config/config.json"
```

Keys you leave out keep the built-in defaults (the same numbers written in that file).
Run `./scripts/test-ntn-scheduling.sh` for the FDD/TA/K-offset smoke checks.

### Workloads (`application.workload`)

Every workload is TCP. Each message is a 4-byte length plus the 32-byte app header and payload.

| Workload | Behavior | Keys |
|---|---|---|
| `probe` (default) | `onoff`: exponential ON/OFF at `dataRate`. `cbr`: fixed spacing. `bursty`: Pareto ON, exponential OFF, Poisson packets | `application.probe.traffic`, `onTime`, `offTime`, `burstShape`, `application.dataRate`, `application.packetSize` |
| `file` | Truncated log-normal file, exponential reading time, Poisson chunks at `dataRate`; BS ACKs each chunk | `application.file.sizeMu`, `sizeSigma`, `sizeMax`, `readingMean`, `application.packetSize`, `application.dataRate` |
| `rpc` | WAP page: fixed request, truncated-Pareto object, geometric object count, exponential object gap and reading time | `application.rpc.reqSize`, `objectScale`, `objectShape`, `objectMax`, `objectsMean`, `objectGap`, `readingMean`, `count` (0 = until stop) |
| `stream` | 10 fps video: 8 slices, truncated-Pareto slice size and slice spacing | `application.stream.frameInterval`, `slicesPerFrame`, `sliceScale`, `sliceShape`, `sliceMax`, `sliceGapScale`, `sliceGapShape`, `sliceGapMax` |
| `iot` | Poisson reports, uniform payload | `application.iot.payloadMin`, `payloadMax`, `interval` |

| Key | Meaning | Default |
|---|---|---|
| `time.simTime` | Simulation duration (s) | 300 |
| `time.warmUp` | Seconds before the UE app starts | 10 |
| `time.appDrain` | Stop the app this many seconds before `simTime` | 5 |
| `time.measureStart` | Seconds after the app starts before trace rows are kept | 5 |
| `time.measureEnd` | Seconds before the app stops when trace rows stop | 1 |
| `application.workload` | `probe`, `file`, `rpc`, `stream`, or `iot` | `probe` |
| `application.probe.traffic` | `cbr`, `onoff`, or `bursty` | `onoff` |
| `application.probe.onTime` / `offTime` | Mean ON and OFF durations | `1s` / `1s` |
| `application.probe.burstShape` | Pareto shape of a bursty ON period | 1.5 |
| `application.dataRate` | Probe peak rate, and the mean file-chunk rate; also expected capacity for BDP queue sizing | `30Mbps` |
| `application.packetSize` | Probe packet and file chunk size (bytes), including the 32-byte header | 1400 |
| `application.tcpVariant` | ns-3 `TcpCongestionOps` (`TcpNewReno`, `TcpCubic`, `TcpBbr`, `TcpVegas`, `TcpWestwoodPlus`, `TcpHybla`, `TcpHighSpeed`, `TcpIllinois`, `TcpYeah`, `TcpHtcp`, `TcpBic`, `TcpVeno`, `TcpScalable`, `TcpLedbat`) | `TcpNewReno` |
| `application.queueSizeBdp` | Queue / RLC TX buffer as a multiple of BDP (`dataRate × baseRTT`), typically `0.5`–`16` (floored at 10 KiB) | `2` |
| `application.aqm` | Root queue disc on the PGW↔BS hop: `DropTail` / `FIFO`, `CoDel`, `FqCoDel`, `PIE`, `RED` | `DropTail` |
| `application.file.sizeMu` / `sizeSigma` / `sizeMax` | Truncated log-normal file size, ln(bytes), cap in bytes | 14.45 / 0.35 / 5000000 |
| `application.file.readingMean` | Exponential mean pause after each fully ACKed file | `5s` |
| `application.rpc.reqSize` | Fixed request size (bytes) | 76 |
| `application.rpc.objectScale` / `objectShape` / `objectMax` | Truncated Pareto response size | 71.7 / 1.1 / 1400 |
| `application.rpc.objectsMean` | Geometric mean before the +1 offset | 2 |
| `application.rpc.objectGap` / `readingMean` | Exponential mean between objects, then between pages | `1.6s` / `5.5s` |
| `application.rpc.count` | RPC exchanges (0 = until app stop) | 0 |
| `application.stream.frameInterval` / `slicesPerFrame` | Frame period and slices in each frame | `100ms` / 8 |
| `application.stream.sliceScale` / `sliceShape` / `sliceMax` | Truncated Pareto slice payload (bytes) | 20 / 1.2 / 125 |
| `application.stream.sliceGapScale` / `sliceGapShape` / `sliceGapMax` | Truncated Pareto gap between slices (seconds) | 0.0025 / 1.2 / 0.0125 |
| `application.iot.payloadMin` / `payloadMax` | Uniform report size (bytes), header included | 48 / 128 |
| `application.iot.interval` | Exponential mean time between reports | `1s` |
| `satellite.altitudeKm` | LEO altitude (km), range [550, 1200] | 550 |
| `radio.ntnScenario` | 3GPP NTN scenario for `NrChannelHelper` | `NTN-Rural` |
| `radio.dlFrequencyHz` / `ulFrequencyHz` | Paired FDD carrier frequencies | 20e9 / 30e9 |
| `radio.dlBandwidthHz` / `ulBandwidthHz` | Per-direction FDD bandwidths | 400e6 / 400e6 |
| `topology.ueDistanceM` | UE distance from the BS (m) | 100 |
| `radio.realisticPower` | Compensate array gain for a realistic NTN link budget | false |
| `radio.nrTraces` | Enable 5G-LENA traces | false |
| `output.prefix` | Directory and file-name prefix for periodic traces | `sat-bs-handset` |

To try another workload, change `application.workload` (and the matching sizes) in `config.json`, or point `--config` at a second file.

Exit **0** when the workload’s success criterion holds (probe/stream/iot: ≥90% replies; file: at least one file completed; rpc: ≥90% exchanges completed); otherwise **1**. A run that never connects also exits **1**, and its `results/` directory is removed.

### Metrics

The UE collector holds both layers. Message counts (`sent`, `recv`, bytes) are reassembled application messages. The 69 Sage features are the UE TCP socket, sampled every 5 ms from connection until the application stops. Rows and SINR reports are written only from `measureStart` after the app starts until `measureEnd` before it stops. The collector keeps updating through those buffers, so the first kept row already has a filled history and the latest SINR. A run whose UE never connects is deleted. Each kept row also has `sinr_dl_db` and `sinr_ul_db`, the latest downlink and uplink report at or before the tick. A direction is empty until its first report. If several reports land in one 5 ms gap, the row keeps the latest. The `lost` column on each row is the cumulative count. Token folding sizes each window from the **current min RTT** (`rtt_rate * srtt`, `tokenRttLock: false`), accumulates 5 ms samples until that wall time, then emits a token with a **time-weighted mean** (`sum(f·dt)/sum(dt)`); `lost` is the window delta and `ca_state` is the mode. Set `tokenRttLock` true to freeze min RTT from the first sample (UNUM CC style), or `tokenRttColumn` to `srtt` to size windows on smoothed RTT.

Each TCP sample and each SINR report is also appended under `results/<output.prefix>/`. The prefix comes from `output.prefix` in `config.json` and is both the directory name and the file-name prefix: `<prefix>-tcp.csv`, `<prefix>-sinr-dl.csv`, `<prefix>-sinr-ul.csv`, `<prefix>-summary.txt`, and `<prefix>-app.txt`. The app file records the workload, message counts, and whether the success check passed. `results/` is gitignored.

### UNUM-style tokens and buckets

After a run, fold the 5 ms Sage TCP stream into min-RTT tokens and quantile-bucketize **every** collected metric (all Sage TCP columns, derived `base_rtt = rtt_rate * srtt`, and downlink / uplink SINR as **separate** heads `sinr_dl_db` and `sinr_ul_db`). Default is Quantile-50 like UNUM: duplicate percentile edges collapse (fewer buckets on short/flat traces). SINR edges are fit from the PHY CSVs:

```bash
python3 scripts/bucketize_metrics.py \
  --config scratch/sat-bs-handset/config/config.json \
  --tcp results/<prefix>/<prefix>-tcp.csv \
  --sinr-dl results/<prefix>/<prefix>-sinr-dl.csv \
  --sinr-ul results/<prefix>/<prefix>-sinr-ul.csv \
  --num-buckets 50
```

| Output | What it is |
|---|---|
| `<prefix>-tokens.csv` | RTT-window aggregates of the full Sage feature set |
| `<prefix>-features.csv` | Same tokens plus derived `base_rtt`, continuous values |
| `<prefix>-boundaries-quantile50.json` / `.pkl` | Fitted quantile edges per feature name (and by index in the pickle) |
| `<prefix>-bucketized.csv` | Multi-head form: each feature keeps its value and a `*_bucket` index (includes SINR) |
| `<prefix>-unum6.csv` | Compact UNUM-6 subset (`base_rtt`, `srtt`, `thr`, `rttvar`, `lost`, `pre_act`) with buckets |

Then window RTT tokens into UNUM-style **10 + 10** sequences (history → tokens to predict):

```bash
python3 scripts/tokenize_sequences.py \
  --bucketized results/<prefix>/<prefix>-bucketized.csv
```

| Output | What it is |
|---|---|
| `<prefix>-seq20.npz` / `.pkl` | `sequences` `(N, 20, F)`, `history` `(N, 10, F)`, `future` `(N, 10, F)` |
| `<prefix>-seq20.json` | Feature names, stride, shapes |

Default vector layout is UNUM multi-head: `base_rtt` continuous, every other feature its bucket index. Non-overlapping windows use stride 10 (`--stride 1` for dense sliding). Needs ≥ 20 RTT tokens.

For UNUM-style **train / test** (80/20, boundaries fit on train only):

```bash
python3 scripts/make_datasets.py \
  --features results/<prefix>/<prefix>-features.csv \
  --out-dir results/<prefix> \
  --prefix <prefix>
```

| Output | What it is |
|---|---|
| `<prefix>-train.npz` / `.pkl` | `(N_train, 20, F)` multi-head sequences + `history` / `future` |
| `<prefix>-test.npz` / `.pkl` | Same for the held-out 20% |
| `<prefix>-boundaries-quantile50.json` / `.pkl` | Edges fit on **train** only, then applied to both splits |

| Group | Count | What it is |
|---|---|---|
| `metrics.sage.now` | 4 | `srtt`, `rttvar`, `thr` (delivery rate), `ca_state` (Open / Disorder / Cwr / Recovery / Loss) |
| `rtt`, `thr`, `rtt_rate`, `rtt_var`, `inflight`, `lost` | 54 | Each signal's average, minimum, and maximum over the last 10 (`s`), 200 (`m`), and 1,000 (`l`) observations |
| `metrics.sage.derived` | 11 | `time_delta`, `rtt_rate`, `loss_db`, `acked_rate`, `dr_ratio`, `bdp_cwnd`, `dr`, `cwnd_unacked_rate`, `dr_max`, `dr_max_ratio`, `pre_act` |

`rtt_rate` is minimum RTT / current smoothed RTT. `pre_act` is log2 of the congestion-window change since the previous sample. Scales are printed on the `metrics.sage` line (`srtt` is microseconds/1e5, throughput features are a fraction of 100 Mbps). The separate SINR files still list every report, and the end-of-run summary is still average, minimum, and maximum.

App header is 32 bytes (`packetSize` and the other payload sizes must be ≥ 32).

## Layout

```
scratch/sat-bs-handset/
  sat-bs-handset.cc          main
  config/                    config.json and smoke.json
  ue/                        TCP client
  bs/                        TCP server
  topology/                  mobility and the NR NTN install
  metrics/                   UE TCP features and message counts, BS counts, PHY SINR, report
  common/                    app header, TCP framing, JSON loader, radio presets
scripts/setup.sh
scripts/fold_tokens.py         fold 5 ms traces into RTT tokens
scripts/bucketize_metrics.py   Quantile bucketization of all collected features (incl. SINR)
scripts/tokenize_sequences.py  UNUM 10+10 sequence windows over RTT tokens
scripts/make_datasets.py       UNUM 80/20 train/test + fit-on-train bucketization
```
