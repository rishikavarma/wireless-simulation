# Satellite dataset simulation

An ns-3 / 5G-LENA experiment that generates transport and radio datasets through
a transparent LEO relay:

```text
UE (NR) -- selected LEO relay -- ground gNB and application server
```

The satellite carries no NR device or IP application. Propagation uses live
feeder/service ranges and a two-hop composite-SNR model. The handheld reference
uses paired 10 MHz carriers at 2185 MHz DL / 1995 MHz UL, 23 dBm UE power and
0 dBi UE antenna gain. UE slot timing follows the moving propagation delay.
Relay switching selects between two satellites within the same NR cell.

The maintained end-to-end experiment and its plots live together in
[`scripts/dataset/`](scripts/dataset/). One-off investigations, historical plot
and slide builders, and exploratory sweeps are preserved in ignored `local/`
folders. All diagnostic and regression test files are archived under ignored
`local/diagnostics/`; the dataset workflow does not depend on them.

## Setup

Requires Ubuntu 22.04 / WSL, Python 3.10+, a C++ toolchain, CMake 3.25+, Ninja,
Git and Eigen3. From the repository root:

```bash
python3 -m pip install -r requirements.txt
bash scripts/setup.sh
```

Setup pins ns-3.48 and NR v5.1, applies the versioned
[`patches/`](patches/), links the scenario into ns-3, builds it and runs a smoke
check. Use `--skip-deps` to skip package installation or `--skip-verify` to skip
the smoke run. `NS3_DIR` selects another upstream checkout for setup.
The dataset scripts currently use the repository's `ns-3-dev` path; provide a
symlink there when using another checkout. Re-running setup preserves an
already patched checkout and rejects conflicting edits or a different commit.

## Full dataset and comparison plots

Run these commands from the repository root with a **fresh output directory
and unused prefix**:

```bash
python3 scripts/dataset/run_dataset.py --out-dir results/full-example --run-prefix full-example --jobs 8
```

This creates **50 profiles × four matched RNG replicates**, each 130 s long,
with measurements over 20–119 s. It compares applications, UE movement,
background traffic, TCP variants/UDP, link attenuation and relay switching.
The analyzer selects one or two metrics per knob and produces seven comparison
pages in `results/full-example/figures/important-comparisons.pdf`.
The report builder writes `local/reports/full-example/dataset-summary.md` and
copies the figures beside it. Reports, generated figures and raw data are
ignored by Git. Slide decks and their previews are stored under ignored
`local/slides/`.

Outputs include configurations, raw traces, execution logs, per-run summaries,
metric audits, source hashes, and train/test archives. There are **73 transport
features** and a separate **23-feature radio/KPI tensor**. Splits hold out an
entire RNG replicate (150 training / 50 test runs); quantile boundaries are fit
only on training data. TCP-only observations remain **NaN for UDP**.

Saved experiment runs and their provenance hashes remain untouched by repository
reorganization. `run_dataset.py` executes planning, simulation, postprocessing,
analysis and reporting in order. It imports shared configuration and satellite
visibility calculations from `campaign_config.py`. `analyze_dataset.py` audits
the data, builds the radio tensors and creates the plots; `build_report.py`
summarizes those results. These are parts of the same full-run workflow.

To plan without launching simulations, use `--plan`. To resume individual
stages, select `--run`, `--post`, `--analyze` or `--report` (flags can be combined):

```bash
python3 scripts/dataset/run_dataset.py --out-dir results/full-example --run-prefix full-example --plan
python3 scripts/dataset/run_dataset.py --out-dir results/full-example --run --post --analyze --report --jobs 8
```

Collection tools, tests, result/fix write-ups and slide materials outside the
main workflow are preserved in ignored `local/` folders. The root README and
the experiment explanation are the only versioned Markdown files.

## Supported traffic and metrics

Applications include synthetic probe, file, RPC, stream and IoT workloads plus
native ns-3 VoIP, CBR, OnOff and iperf-style bulk traffic. They support TCP or
UDP; TCP congestion control is selected with `application.tcpVariant`.
Stationary background UEs can use either relay while sharing the gNB scheduler.

Telemetry includes TCP socket signals, per-UE geometry/timing, SINR, MCS,
analytical TB decode outcomes, HARQ ACK/NACK, BSR, RACH/RRC events, RLC/PDCP
sequence numbers, and seven interval KPIs. Optional MAC payload dumps expose
simulated packet bytes. The PHY abstraction does not produce waveform IQ,
DMRS samples, coded/scrambled bits, or channel-estimate arrays.

See [experiment design, model, metric definitions and application rates](docs/experiment.md)
for the settings and assumptions needed to interpret the dataset.

## Single scenario

```bash
cd ns-3-dev
./ns3 run "sat-bs-handset --config=scratch/sat-bs-handset/config/config.json"
```

Edit the JSON configuration under `scratch/sat-bs-handset/config/`. Results go
to `results/<output.prefix>/` at the repository root. A full run can exit 1
when an application criterion is missed; campaign tools retain completed
observations and distinguish those outcomes from crashes and missing traces.

## Repository layout

```text
scratch/sat-bs-handset/   scenario, radio/mobility, applications and telemetry
patches/                  maintained ns-3.48 / NR v5.1 changes
scripts/setup.sh          pinned upstream setup and smoke check
scripts/dataset/          campaign, processing, validation, plots and report
docs/experiment.md        experiment design, model and metric definitions
local/reports/            ignored result and investigation write-ups
local/slides/             ignored slide decks, notes and previews
local/figures/            ignored presentation/design figures
local/                    also holds archived tests, documentation and diagnostics
results/                  ignored collected datasets and raw traces
```
