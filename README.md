# Wireless simulation (ns-3 satellite)

Transparent satellite bent-pipe with 5G-LENA NR NTN in ns-3.

```
UE (NR UE)  -- LEO relay --  Base station (ground gNB + app server)
```

- **Access:** Transparent bent-pipe. The gNB stays on the ground. Path loss and delay are the feeder hop plus the service hop through the satellite.
- **Satellite:** Transparent relay. It has no NR device and no IP apps.
- **Traffic:** UE synthetic TCP workload ↔ BS app server (echo / ACK / sized RPC response).

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

### Workloads (`application.workload`)

Every workload is TCP. Each message is a 4-byte length plus the 32-byte app header and payload.

| Workload | Behavior | Keys |
|---|---|---|
| `probe` (default) | Paced messages | `application.probe.traffic` `cbr`/`onoff`/`bursty`, `application.dataRate`, `application.packetSize` |
| `file` | Bulk upload; BS ACKs each chunk | `application.file.fileSize`, `application.packetSize`, `application.dataRate` |
| `rpc` | Closed-loop req/resp | `application.rpc.reqSize`, `application.rpc.respSize`, `application.rpc.count` (0 = until stop) |
| `stream` | Paced chunks | `application.stream.chunkSize`, `application.dataRate` |
| `iot` | Small periodic uplink reports | `application.iot.payload`, `application.iot.interval` |

| Key | Meaning | Default |
|---|---|---|
| `time.simTime` | Simulation duration (s) | 120 |
| `time.warmUp` | Seconds before the UE app starts | 10 |
| `time.appDrain` | Stop the app this many seconds before `simTime` | 5 |
| `time.measureStart` | Seconds after the app starts before trace rows are kept | 5 |
| `time.measureEnd` | Seconds before the app stops when trace rows stop | 1 |
| `application.workload` | `probe`, `file`, `rpc`, `stream`, or `iot` | `probe` |
| `application.probe.traffic` | `cbr`, `onoff`, or `bursty` | `cbr` |
| `application.dataRate` | Offer rate for probe/file/stream | `30Mbps` |
| `application.packetSize` | Probe / file chunk size (bytes), including the 32-byte header | 1400 |
| `application.file.fileSize` | File upload size (bytes) | 1048576 |
| `application.rpc.reqSize` / `application.rpc.respSize` | RPC sizes (bytes) | 256 / 4096 |
| `application.rpc.count` | RPC exchanges (0 = until app stop) | 0 |
| `application.stream.chunkSize` | Stream chunk size (bytes) | 1400 |
| `application.iot.payload` / `application.iot.interval` | IoT report size / period | 64 / `1s` |
| `satellite.altitudeKm` | LEO altitude (km), range [550, 1200] | 550 |
| `radio.ntnScenario` | 3GPP NTN scenario for `NrChannelHelper` | `NTN-Rural` |
| `topology.ueDistanceM` | UE distance from the BS (m) | 100 |
| `radio.realisticPower` | Compensate array gain for a realistic NTN link budget | false |
| `radio.nrTraces` | Enable 5G-LENA traces | false |
| `output.prefix` | Directory and file-name prefix for periodic traces | `sat-bs-handset` |

To try another workload, change `application.workload` (and the matching sizes) in `config.json`, or point `--config` at a second file.

Exit **0** when the workload’s success criterion holds (probe/stream/iot: ≥90% replies; file: transfer completed; rpc: ≥90% exchanges completed); otherwise **1**. A run that never connects also exits **1**, and its `results/` directory is removed.

### Metrics

The UE collector holds both layers. Message counts (`sent`, `recv`, bytes) are reassembled application messages. The 69 Sage features are the UE TCP socket, sampled every 5 ms from connection until the application stops. Rows and SINR reports are written only from `measureStart` after the app starts until `measureEnd` before it stops. The collector keeps updating through those buffers, so the first kept row already has a filled history and the latest SINR. A run whose UE never connects is deleted. Each kept row also has `sinr_dl_db` and `sinr_ul_db`, the latest downlink and uplink report at or before the tick. A direction is empty until its first report. If several reports land in one 5 ms gap, the row keeps the latest. The `lost` column on each row is the cumulative count. An RTT token replaces it with the increase across that window, and replaces `ca_state` with the most frequent value in the window.

Each TCP sample and each SINR report is also appended under `results/<output.prefix>/`. The prefix comes from `output.prefix` in `config.json` and is both the directory name and the file-name prefix: `<prefix>-tcp.csv`, `<prefix>-sinr-dl.csv`, `<prefix>-sinr-ul.csv`, `<prefix>-summary.txt`, and `<prefix>-app.txt`. The app file records the workload, message counts, and whether the success check passed. `results/` is gitignored.

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
scripts/fold_tokens.py       fold 5 ms traces into tokens
```
