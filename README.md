# Satellite dataset simulation

An ns-3 / 5G-LENA experiment that generates transport and radio datasets through
a transparent LEO relay:

```text
UE (NR) -- selected LEO relay -- ground gNB and application server
```

The satellite carries no NR device or IP application. Propagation uses live
feeder/service ranges and a two-hop composite-SNR model. The handheld reference
uses paired 10 MHz carriers at 2135 MHz DL / 1945 MHz UL for both service and
feeder links, 23 dBm UE power and 0 dBi UE antenna gain. The `handheld` profile
retains handset behavior without the band limits of `handheld-n256`. UE slot timing follows the moving propagation delay.
Relay switching selects between two satellites within the same NR cell.

The maintained end-to-end experiment and its plots live together in
[`scripts/dataset/`](scripts/dataset/). One-off investigations, historical plot
and slide builders, and exploratory sweeps are preserved in ignored `local/`
folders. Diagnostic and regression test files are archived under ignored
`local/diagnostics/`; the dataset workflow does not depend on them. Maintained
queue-calibration experiments live separately in `experiments/queue-calibration/`.

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

## Comparison dataset and plots

Run these commands from the repository root with a **fresh output directory
and unused prefix**:

```bash
python3 scripts/dataset/run_dataset.py --preset comparison --out-dir results/full-example --run-prefix full-example --jobs 8
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

The 200 runs are a selected set of controlled comparisons, not every combination
of the available knobs. Adding support for a knob does not multiply that design.
`comparison` remains the default preset, preserving the existing configurations.

## Broader full dataset (separate setting)

`--preset full` reads [`scripts/dataset/full_dataset.json`](scripts/dataset/full_dataset.json).
Its default is **2,000 configurations × 20 matched RNG repeats = 40,000 runs**.
It uses 49 controlled profiles with automatic relay switching enabled, plus
1,951 sampled joint configurations, stratified across nine applications, six
TCP algorithms or UDP, and supported traffic directions. Enabling selection
merges the formerly separate baseline and switch profile. Source/load rates,
queue budgets, movement, bandwidth and attenuation vary together. Orbit altitude
stays fixed at **800 km**, with 53° inclination and 8° relay spacing; automatic
relay selection stays enabled in every full-preset configuration.

Expected size is **39.6 million distinct 100 ms time bins** and **1.96 million
non-overlapping 20-token windows** (10 history + 10 future). These are planned
counts, not collected data or a claim that every feature vector will be unique.
The held-out four RNG repeats give an 80/20 split: 32,000/8,000 runs.
See [full-preset ranges and comparison with UNUM](docs/experiment.md#full-dataset-preset)
for the scope and limits of that comparison.

Inspect the setting without writing configurations or launching simulations:

```bash
python3 scripts/dataset/run_dataset.py --preset full --describe
```

To write configurations only, when ready:

```bash
python3 scripts/dataset/run_dataset.py --preset full --out-dir results/full-training --run-prefix full-training --plan
```

Later, explicitly execute that saved plan and the remaining stages:

```bash
python3 scripts/dataset/run_dataset.py --out-dir results/full-training --run --post --analyze --report --jobs 8
```

`--profile-count`, `--replicates` and `--design-seed` override planning defaults.
`--target-windows` can instead raise the profile count to meet an explicit
20-token-window target. For example, `--preset full --target-windows 35000000
--describe` describes **714,300 runs / 35,000,700 windows** without creating or
executing a plan. This is much larger than the default; a time-token count must
not be confused with a window count when comparing datasets.
At least 49 full-preset profiles are retained for controlled comparisons. Resuming uses the
settings saved in the manifest, even if the JSON preset has since changed.
The full preset writes bounded-size transport/radio shards, rather than loading
the entire collection into memory. `dataset-index.json` and `radio-index.json`
list those shards; derive history/future by slicing each shard's `sequences`.
Quantile boundaries use a reproducible uniform sample of up to 200,000 training
tokens, with no test tokens used for fitting. The existing comparison preset
retains its monolithic archives and exact training-set quantiles.

The full dataset has not been run. The queue calibration below uses short
ns-3 experiments; the broader cross-factor configurations still need a pilot
before collecting all 40,000 runs. Raw trace storage and runtime grow with traffic/load;
sharding bounds processing memory, not total disk use.

## Repeatable knob comparison experiment

[`experiments/knob-sweep/`](experiments/knob-sweep/) is a separate controlled
experiment for inspecting every full-dataset knob. It does not launch the
40,000-run dataset. Run it from the repository root:

```bash
python3 experiments/knob-sweep/run.py --out-dir results/knob-sweep-new --jobs 8
```

Use a fresh directory after changing the model. The command builds the scenario
and calibration observer, measures a fresh clear-link queue reference, runs the
sweeps, and renders the report. If a matching accepted reference already exists,
provide `--reference path/to/reference.json` on the initial command to reuse it.
The versioned older reference must be recalibrated after the carrier-frequency
change; the runner rejects stale references rather than relabeling old results.

The design covers **30 sweeps**, using all candidate values from
[`full_dataset.json`](scripts/dataset/full_dataset.json), plus separate north/east
gateway offsets, all applications, TCP/UDP, six TCP algorithms, data direction
and relay-switching controls. The background-rate sweep also includes the
comparison anchors' 5/20/80 kbit/s levels. Each sweep holds its other settings
fixed; queue and TCP-algorithm comparisons use backlogged bulk traffic, while
shared network sweeps use native TCP CBR at 32 kbit/s. Application-specific
sweeps use the corresponding workload. Identical configurations are shared
between sweeps. Three matched independent RNG runs (30001–30003) are used per
unique configuration; each simulation is 130 s with measurements over 20–119 s.
Both service and feeder links use **2135 MHz DL / 1945 MHz UL**.

The preserved [controlled run](results/knob-sweep-controls-20261020/report.md)
contains 97 configurations × three seeds = **291 observations**. Open its
[summary pages](results/knob-sweep-controls-20261020/figures/knob-comparisons.pdf)
or [time/value heatmaps](results/knob-sweep-controls-20261020/figures/knob-timeseries.pdf).
Three 1 MB file cases remain unfinished at the run cutoff; their measurements
are included and flagged. Seven crashes from the earlier switching/background
pilot are preserved separately in its `reused-provenance/` audit.

Background sweeps hold relay switching **disabled at every level**, including
the zero-background control, to isolate contention. An expanded pilot exposed
an NR PHY abort when relay switching overlaps UL control/data or SRS reception;
the separate switching sweep still compares enabled/disabled operation without
background UEs. The combined switching/background interaction remains a model
limitation. Failed pilot logs are preserved rather than included as completed
measurements.

Outputs remain under the chosen directory:

- `report.md`: metric definitions, PHY-metric rationale, observations and plots.
- `figures/overview.png`: largest paired effect of each knob, with physical units.
- `figures/knob-comparisons.pdf`: eight summary panels per knob, showing medians,
  individual seed results and their full range; the reference level is marked.
- `figures/knob-timeseries.pdf`: time-versus-value heatmaps, with shared scales
  within each panel and relay-switch times marked. Individual PNG/PDF pages
  are saved alongside the combined PDFs.
- `run-summary.csv` and `level-summary.csv`: per-run measurements, observation
  counts, and matched-seed differences from each sweep's reference.
- `manifest.json`, `source-snapshot/`, `calibration-reference.json`, `configs/`,
  `logs/`, `raw/`, `series/`: provenance, exact inputs, compressed original
  traces and selected metric curves. Raw CSV compression is lossless.
- `analysis-provenance.json` and `analysis-code/`: the exact plotting revision
  and its standalone `render.py --out-dir ...` entry point. Each presentation
  revision gets its own snapshot alongside the original collection sources.

Transport panels show **50 ms rolling mean SRTT**, **50 ms rolling mean RTTVAR**,
**new TCP loss detections over the trailing 50 ms**, and cumulative detections
since measurement start. RTTVAR is an absolute-error estimator in ms, not
variance in ms²; loss is detected lost bytes divided by MSS, not permanent
application packet loss. PHY panels show primary-UE UL/DL received SINR,
scheduled MCS and observed TB decode-failure fraction in the data direction.
MCS reveals adaptation to link quality; decode failures reveal residual radio
errors that RLC/TCP may recover. Direction-specific measurements and PDCP rates
are also exported. TCP fields remain unavailable for UDP, and missing PHY
attempts are never plotted as zero errors. Heatmaps display 1 s bins; selected
TCP curves sampled every 5 ms with 50 ms windows remain in `series/*.npz`.

Resume an unchanged experiment by repeating its command **without** initial
`--reference`, `--seeds` or `--only` options. Completed runs are reused; changed
sources/binaries/configs are rejected. Replot without rebuilding or simulating:

```bash
python3 experiments/knob-sweep/run.py --out-dir results/knob-sweep-new --analyze-only
# Also verify metric extraction against the preserved compressed raw traces:
python3 experiments/knob-sweep/run.py --out-dir results/knob-sweep-new --analyze-only --reextract
# A smaller new experiment, using a current accepted queue reference:
python3 experiments/knob-sweep/run.py --out-dir results/knob-links-new --reference path/to/reference.json --only serviceExcessLossDb feederExcessLossDb --jobs 8
# Reuse exact configuration/seed matches from an earlier experiment:
python3 experiments/knob-sweep/run.py --out-dir results/knob-sweep-next --reference path/to/reference.json --reuse results/knob-sweep-previous --jobs 8
python3 -m unittest discover -s experiments/knob-sweep -p 'test_*.py'
```

`--seeds` overrides the RNG list for a new plan; `--plan-only` saves a plan
without running its sweeps (a current queue reference is still required or
automatically measured). Seed ranges are descriptive, not confidence intervals.
Complete low/zero-delivery runs stay in the plots and validation results.
Movement changes geometry; this model does not introduce Doppler/fast fading.
`--reuse` checks physical source/library/binary hashes, exact configuration and
seed, measurement definitions, metric-extractor hashes, and raw/cache checksums.
It keeps original run identities and provenance; immutable raw traces are
hard-linked where possible. Changed physical models require fresh simulations.

## Queue calibration experiments

[`experiments/queue-calibration/`](experiments/queue-calibration/) contains a
separate reproducible experiment design, runner, read-only RTT observer and
validated reference. It does not launch the full dataset. Setup links and builds
the calibration target alongside the main scenario. For an existing checkout:

```bash
ln -s ../../experiments/queue-calibration ns-3-dev/scratch/queue-calibration
cd ns-3-dev
./ns3 build queue-calibration sat-bs-handset
cd ..
python3 experiments/queue-calibration/calibrate.py --out-dir results/queue-calibration-new --plan --run --analyze --reference experiments/queue-calibration/reference.json --jobs 8
```

Use a fresh output directory. To resume, omit `--plan`; `--analyze` alone reads
completed traces. `--extend` appends added load points from settings after a
completed sweep, preserving prior configuration and source provenance.
Generated configurations, logs, per-run traces and the detailed
summary and calibration plots remain under ignored `results/`. The small
versioned reference keeps the measured values and provenance available on
another machine.

The design holds altitude at **800 km**, the UE stationary in a 500 m disk,
clear-link attenuation and no background UEs. Relay switching remains enabled.
It measures UL and DL at both **10 and 20 MHz**, with three independent RNG runs
(20001–20003, separate from the full dataset). Every simulation lasts 130 s;
measurements use 20–119 s and exclude five seconds on either side of the switch.

- Unloaded RTT: native TCP CBR, 128 B at 1 kbit/s. An observer attaches after
  warm-up and records socket `LastRTT` changes, excluding the SYN handshake.
  The reference is the median of the three per-run minimum data RTTs. This
  includes scheduling, processing and the configured TCP delayed ACK behavior;
  it is neither geometric propagation RTT nor rolling SRTT.
- Service capacity: UDP CBR, 512 B, offered loads of 8, 32, 128, 512 and
  2048 kbit/s, plus an 8192 kbit/s confirmation for the 20 MHz uplink, with a
  fixed 64 KiB queue. The two highest loads must give
  delivered rates within 15%, and the highest load must exceed delivery by at
  least 50%. A 128 KiB control at the highest load checks buffer sensitivity.
  Capacity uses received PDCP SDU rate, with application goodput reported
  separately. This is sustainable service in this model, including its
  scheduler, errors, RLC AM and 120 B new-PDU limit; it is not peak radio bitrate.

Measured reference (three RNG runs per case; 80 completed calibration runs,
including an earlier buffer control retained when extending the load sweep):

| Bandwidth | Direction | PDCP service capacity | Minimum data RTT | Directional BDP |
| --- | --- | ---: | ---: | ---: |
| 10 MHz | UL | 337.1 kbit/s | 337 ms | 13.87 KiB |
| 10 MHz | DL | 251.8 kbit/s | 308 ms | 9.47 KiB |
| 20 MHz | UL | 715.2 kbit/s | 337 ms | 29.42 KiB |
| 20 MHz | DL | 251.8 kbit/s | 308 ms | 9.47 KiB |

The final two offered loads change delivery by less than **0.02%** in every
case; doubling the buffer produces the same delivered rate for the control RNG.
All 80 runs completed successfully with one relay switch each. The separate
12-run pilot is retained under ignored results. An additional legacy-default
regression reproduced saved application, TCP, SINR, TB, event and KPI traces
byte for byte. All 2,000 full-preset profiles also passed geometry admission
and calibrated-budget checks, with switching enabled and altitude fixed at
800 km. The full dataset was not executed.

Actual full-preset per-buffer budgets are:

| Bandwidth | 0.5× | 1× | 2× | 4× | 8× | 16× |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 MHz | 10,240 B | 14,199 B | 28,398 B | 56,795 B | 113,589 B | 227,177 B |
| 20 MHz | 15,065 B | 30,129 B | 60,258 B | 120,516 B | 241,032 B | 482,063 B |

At 10 MHz, the nominal 0.5× budget is clamped to 10 KiB (about 0.72× the
reference BDP). The other listed factors remain distinct.

For each bandwidth, queue reference BDP is the **larger of the measured UL and
DL products** `capacity_bits_per_second * unloaded_RTT_seconds / 8` in bytes.
Both directions and all applications use the same byte budget at that bandwidth.
Full-preset configurations set `application.queueSizeBytes` to the reference
multiplied by 0.5, 1, 2, 4, 8 or 16, rounded up, with the existing **10 KiB floor**.
The manifest records the nominal multiplier and actual bytes. A floor-clamped
value must not be described as its nominal BDP multiple. This is a per-buffer
limit applied to the configured RLC/device queues, not one shared memory pool.

The full preset loads this measured reference instead of the former arbitrary
10 Mbit/s sizing rate. Background load and attenuation vary against this fixed
clear-link reference; they do not silently resize buffers. Planning rejects a
reference whose scenario/patch hashes no longer match. Recalibrate after model
changes. Existing comparison configurations keep their original queue sizing
unless an explicit byte budget is supplied.

## What the dataset helper modules do

[`dataset_presets.py`](scripts/dataset/dataset_presets.py) decides **what to
collect**: comparison versus full settings, reproducible knob combinations,
geometry admission, calibrated queue bytes, RNG repeats and held-out repeats.
It lets the 200-run comparison and 40,000-run design share one collection
pipeline. A preset describes configurations; it does not run simulations itself.

[`dataset_storage.py`](scripts/dataset/dataset_storage.py) decides **how to
export the collected data**: bounded batches of transport/radio NPZ shards,
indexes describing those shards, and consistent train/test membership. For the
full setting it fits quantile bins from up to 200,000 training tokens and applies
those same bins to both splits. UDP TCP-only fields remain NaN. The comparison
setting keeps its existing single-file archives.

These files are implementation modules, not extra dataset copies. Keeping them
separate makes the runner smaller. Their functions are needed: presets preserve
a reproducible design, while sharding avoids assembling all 40,000 runs in RAM.
The functions could live in the runner, but the separate files are not an
additional execution step or a new ns-3 dependency.

## Processing and outputs

Outputs include configurations, raw traces, execution logs, per-run summaries,
metric audits, source hashes, and train/test archives. There are **73 transport
features** and a separate **23-feature radio/KPI tensor**. Splits hold out entire
RNG replicates (150 training / 50 test runs in the comparison preset); quantile
boundaries are fit only on training data. TCP-only observations remain **NaN for UDP**.

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
main workflow are preserved in ignored `local/` folders. Versioned Markdown is
limited to this README, the experiment explanation and application rate reference.

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

See [experiment design, model and metric definitions](docs/experiment.md) and
[all application rate details](docs/application-rates.md) for source rates,
packet sizes, directions and the distinction between goodput and PDCP throughput.
The TCP/UDP plot uses matched native CBR anchors: application goodput over 20–119 s
and undelivered source bytes at run end, with each RNG repeat shown.

## Single scenario

```bash
cd ns-3-dev
./ns3 run "sat-bs-handset --config=scratch/sat-bs-handset/config/config.json"
```

Edit the JSON configuration under `scratch/sat-bs-handset/config/`. Results go
to `results/<output.prefix>/` at the repository root. A full run can exit 1
when an application criterion is missed; dataset tools retain completed
observations and distinguish those outcomes from crashes and missing traces.

## Repository layout

```text
scratch/sat-bs-handset/   scenario, radio/mobility, applications and telemetry
patches/                  maintained ns-3.48 / NR v5.1 changes
scripts/setup.sh          pinned upstream setup and smoke check
scripts/dataset/          presets, collection, processing, validation, plots and report
experiments/queue-calibration/ maintained capacity/RTT calibration and reference
docs/experiment.md        experiment design, model and metric definitions
docs/application-rates.md source behavior, configured rates and interpretation
local/reports/            ignored result and investigation write-ups
local/slides/             ignored slide decks, notes and previews
local/figures/            ignored presentation/design figures
local/                    also holds archived tests, documentation and diagnostics
results/                  ignored collected datasets and raw traces
```
