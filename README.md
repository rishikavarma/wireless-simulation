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

## RLC recovery and decoder regressions

The maintained NR patch supports RLC AM retransmission resegmentation and
missing-byte recovery, fills downlink grants with multiple bounded RLC PDUs,
and corrects scalar-channel decoder power scaling. The handheld reference uses
grant-sized initial PDUs without the historical 120-byte cap. See [implementation and regression commands](docs/rlc-am-retransmission.md)
for the supported format, recovery behavior, and extensive loss/grant tests.
UL grant prediction now accounts for the handheld UE's fixed total transmit
power and full TB size. Echo traffic stops generating before its receiver closes,
so `appDrain` can count late replies. The same document describes these fixes
and their regression commands.

## Comparison dataset and plots

Run this command from the repository root to start or resume the comparison
dataset. Add `--clean` to restart in the same directory with the same prefix:

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
It uses 49 controlled profiles with automatic relay switching enabled and 1,951
sampled joint configurations, stratified across nine applications, six TCP
algorithms or UDP, supported traffic directions, and TCP socket enlargement.
Deliberate stress profiles are currently disabled. Enabling selection
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

Use the fixed directory `results/full-training` to start or resume all stages:

```bash
python3 scripts/dataset/run_dataset.py --preset full --out-dir results/full-training --run-prefix full-training --jobs 8
```

To discard this campaign and its recorded raw outputs, then restart in the same
directory, explicitly add `--clean`:

```bash
python3 scripts/dataset/run_dataset.py --preset full \
  --out-dir results/full-training --run-prefix full-training --clean --jobs 8
```

Omit `--clean` on subsequent runs to continue partial execution; completed
simulation statuses are reused. Add `--plan` to the cleanup command to reset and
write configurations without simulating. Cleanup never runs by default and does
not delete unrelated experiments. Source/model changes still require a restart.

`--profile-count`, `--replicates` and `--design-seed` override planning defaults.
`--target-windows` can instead raise the profile count to meet an explicit
20-token-window target. For example, `--preset full --target-windows 35000000
--describe` describes **714,300 runs / 35,000,700 windows** without creating or
executing a plan. This is much larger than the default; a time-token count must
not be confused with a window count when comparing datasets.
At least 49 full-preset profiles are required for the controlled comparisons.
Deliberate stress profiles are disabled by `includeStressProfiles: false` in the
full preset; ordinary candidate ranges and finite buffers remain active. Resuming uses the
settings saved in the manifest, even if the JSON preset has since changed.
The full preset writes bounded-size transport/radio shards, rather than loading
the entire collection into memory. `dataset-index.json` and `radio-index.json`
list those shards; derive history/future by slicing each shard's `sequences`.
Quantile boundaries use a reproducible uniform sample of up to 200,000 training
tokens, with no test tokens used for fitting. The existing comparison preset
retains its monolithic archives and exact training-set quantiles.

For a bounded raw-data and memory footprint, use streaming mode. It runs a
spread-out calibration part from the first training replicate, fits shared token
boundaries, then runs and tokenizes each later part. After the token and radio
archives are saved, it removes that part's raw simulation directory. At the end
it assigns whole-replicate parts to train and test indexes. A stopped command
resumes from `stream-progress.json`; use the same batch and calibration sizes.
Streaming does not produce the raw-data comparison plots or report.

```bash
python3 scripts/dataset/run_dataset.py --preset full --stream \
  --out-dir results/full-stream-100x4 --run-prefix full-stream-100x4 \
  --profile-count 100 --replicates 4 --jobs 2 \
  --batch-runs 4 --calibration-runs 12
```

This plan has 400 runs and 19,600 non-overlapping windows, including 4,900 test
windows. The 12-run calibration part bounds retained raw files; later parts hold
four runs. For lower peak memory, use `--jobs 1 --batch-runs 2` on a new campaign.
The calibration part still uses 12 runs; reduce `--calibration-runs` if needed,
at the cost of less representative token boundaries. `--profile-count` and
`--replicates` control total work. Rerun the same command to resume.

For a shorter cross-factor collection, use the separate
[`short_dataset.json`](scripts/dataset/short_dataset.json) design:

```bash
python3 scripts/dataset/run_dataset.py --preset full --stream \
  --design-file scripts/dataset/short_dataset.json \
  --out-dir results/short-stream-3h --run-prefix short-stream-3h \
  --jobs 20 --batch-runs 20 --calibration-runs 20
```

It plans 180 profiles with two matched replicates: 360 runs in 18 parts. The
49 controlled profiles are retained. The sampled interactions limit background
users to one, use packet sizes of at least 512 bytes, and avoid the most severe
link losses; controlled background profiles still include three users. This
targets roughly three hours of simulation wall time on a 20-core machine,
excluding feature and radio processing. It is an estimate from a small pilot,
not a run-time cap. Reuse the same command to resume, and keep the design file
unchanged after planning.

The full dataset has not been run. The queue calibration below uses short
ns-3 experiments; the broader cross-factor configurations still need a pilot
before collecting all 40,000 runs. Raw trace storage and runtime grow with traffic/load;
sharding bounds processing memory, not total disk use.

## Repeatable knob comparison experiment

[`experiments/knob-sweep/`](experiments/knob-sweep/) is a separate controlled
experiment for inspecting every full-dataset knob. It does not launch the
40,000-run dataset. Run it from the repository root. The output directory is
fixed at `results/knob-sweep`; this command starts or resumes the saved plan:

```bash
python3 experiments/knob-sweep/run.py --jobs 8
```

To delete this experiment and its recorded raw outputs, then restart in the same
directory using the current accepted queue calibration:

```bash
python3 experiments/knob-sweep/run.py --clean \
  --reference experiments/queue-calibration/reference.json --jobs 8
```

Afterward, resume with the first command, omitting both `--clean` and
`--reference`. Add `--plan-only` to the cleanup command to prepare configurations
without simulating. `--out-dir` optionally selects another fixed directory.
Cleanup includes interrupted raw outputs and nested automatic calibration
outputs; unrelated experiments and original observations reused from another
sweep remain.

The runner builds the scenario and calibration observer, runs the sweeps, and
renders the report. Without `--reference`, a new plan measures its own clear-link
queue reference. Supplied references must match the current model; stale
references are rejected. After source/model changes, use `--clean` or a fresh
directory rather than combining observations from different versions.

The design covers **33 sweeps**, using all candidate values from
[`full_dataset.json`](scripts/dataset/full_dataset.json), plus separate north/east
gateway offsets, all applications, TCP/UDP, six TCP algorithms, data direction
and relay-switching controls. The background-rate sweep also includes the
comparison anchors' 5/20/80 kbit/s levels. Additional controls sweep queue budgets
at 20 MHz and background UE count with automatic relay switching enabled.
The `socket-enlargement` sweep compares default TCP sockets against enlarged
finite send/receive buffers using identical backlogged traffic and queue budgets.
The sweep does not include the deliberate failure profiles. Each sweep holds its other settings
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

The original background sweeps hold relay switching **disabled at every level**,
including the zero-background control, to isolate contention. The additional
`backgroundCountSwitching` sweep enables it at every count level. The earlier pilot exposed
an NR PHY timing bug when a switch shortened the relay path: an already scheduled
uplink allocation retained the old timing advance and arrived during another
UE's data or SRS reception. The maintained NR patch now rephases pending uplink
allocations before transmission, updating both the slot origin and stored path
delay. The receiver's 100 ns boundary tolerance is unchanged. Failed pilot logs
and existing plots retain their original provenance.

[`experiments/relay-switch-regression/`](experiments/relay-switch-regression/)
replays all seven original failing settings through the production scenario and
checks actual uplink transmit timing plus post-switch delivery for every UE:

```bash
python3 experiments/relay-switch-regression/run.py --out-dir results/relay-switch-check --jobs 4
```

Four additional controls cover fixed-relay operation, a single UE, and short CBR
and backlogged TCP runs. Use a new output directory after changing the model;
the runner saves source/binary hashes and rejects a changed experiment on resume.

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

The maintained TCP patch retains the earliest unacknowledged timestamp when
an ACK is delayed, and advances the last acknowledged sequence for piggybacked
ACKs. Its `tcp-timestamp` regressions cover both delayed ACK pairs and repeated
bidirectional exchanges. Run them in the patched ns-3 checkout:

```bash
cd ns-3-dev
./ns3 configure --enable-tests --enable-examples --filter-module-examples-and-tests=internet
./ns3 build test-runner -j 4
./test.py --no-build -s tcp-timestamp
```

Resume an unchanged experiment by repeating its command **without `--clean`** and without initial
`--reference`, `--seeds`, `--only` or `--tcp-buffer-policy` options. Completed runs are reused; changed
sources/binaries/configs are rejected. To restart in the same directory, use
`--clean` (optionally with `--plan-only`). Cleanup includes only recorded raw
outputs and nested automatic calibration outputs; other experiments and original
observations reused from another sweep remain. Replot without rebuilding or simulating:

```bash
python3 experiments/knob-sweep/run.py --analyze-only
# Also verify metric extraction against the preserved compressed raw traces:
python3 experiments/knob-sweep/run.py --analyze-only --reextract
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

The transport CSV and full dataset also export four causal progress features:

| Feature | Meaning |
|---|---|
| `tcp_pending_bytes` | TCP bytes queued or awaiting acknowledgment |
| `tcp_ack_age_s` | Time since forward ACK progress; missing before the first observed advance |
| `tcp_no_progress_s` | Time continuously backlogged without ACK progress; zero when idle |
| `tcp_rtt_update_age_s` | Time since a changed positive `LastRTT` trace value; this does not count every RTT sample |

These fields are missing for UDP. Use backlog and progress together: silence from
an idle IoT/RPC flow is not an outage. Whole-run total outage time is a retrospective
outcome requiring an explicit service/timeout policy; it should not be used as a
causal input for predicting the same run's future. Held SRTT remains estimator
state. New `*-progress.png` pages show delivery, backlog and ACK progress for
each seed. Heatmaps hatch partially observed seed aggregates and export exact
counts in `observed-seed-coverage.csv`.
Cumulative stall time up to the current sample can be causal, but still needs a
defined threshold and active-demand rule. The current continuous stall age
avoids choosing a universal outage threshold or counting intentional idle time.

Connected-mode random access after unanswered scheduling requests preserves the
established C-RNTI and bearers. Msg3 carries a C-RNTI control element under its
temporary grant identity; a real new-data grant resolves access. Failed Msg3
resolution times out and retries without falsely reporting recovery. After
building `sat-bs-handset` and `test-runner`, run the focused full-duration
seed/control checks from the repository root:

```bash
python3 experiments/relay-recovery-regression/run.py --out-dir results/relay-recovery-regression --jobs 6
python3 experiments/relay-recovery-regression/analyze.py --out-dir results/relay-recovery-regression
```

The runner resumes completed cases only when the recorded model and binary
hashes match. It preserves configs, logs, source hashes, per-case outcomes and
losslessly compressed raw CSVs. Its 25 full-duration cases cover four background
counts with switching and three seeds, fixed-relay controls, 6/9 dB attenuation,
finite scheduling-request retries, UDP, enlarged-socket bulk TCP and a 20 MHz
control.

Healthy paced TCP cases must retain their established C-RNTI, resume delivery
after switching and deliver more than 99% of accepted bytes by cutoff. Weak-link
cases remain observations and are not required to meet that delivery threshold.
TCP progress fields must be nonnegative, with zero no-progress age when idle
and missing values for UDP. Native application reports must state their actual
workload and positive-byte criterion.

The analyzer compares background counts and attenuation using 18 unique runs.
It renders individual-seed delivery/progress pages, summary panels and heatmaps
with observation coverage. It exports all 77 transport features through the
production token/window/quantile functions, holds out complete seed-30003 runs,
fits boundaries on training runs only and checks missingness preservation.
This small export checks the pipeline; it is not a replacement training dataset.

UL schedulers also enforce the UE's minimum useful data grant: 15 bytes
(5-byte buffer report, 3-byte MAC header, 7-byte RLC opportunity). Smaller
grants previously transmitted buffer reports repeatedly without draining data,
creating artificial weak-link stalls. Scheduler allocation, resource reaping
and DCI creation now use the same minimum in TDMA and OFDMA.
These model changes require regenerated
results and a current queue calibration; old plots remain historical results.

Native suites `nr-connected-ra` and `nr-ul-grant-power` exercise the underlying
MAC and scheduler fixes. The first tests actual C-RNTI Msg3 bytes, established
identity preservation, decoded control-element handling, malformed
control-element rejection, finite timeout and grant-based resolution. The
second includes small backlogs and requires every emitted useful UL grant to
fit the 15-byte minimum, alongside grant-power and resource-accounting checks.

Native application success means **some accepted and delivered bytes**, not
completion or a delivery quality guarantee. Source writes and TCP sink reads
cannot be compared as message counts, and unpaced TCP may retain accepted bytes
in buffers at cutoff. Reports state the actual native workload and byte-based
criterion. Assess delivery shortfall, progress and recovery separately from this
smoke-test flag. Failed outcomes remain in the dataset.

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

- Unloaded path RTT: native TCP CBR, 128 B at 1 kbit/s, with
  `TcpSocket::DelAckCount=1` **only in the calibration probes**. The observer
  records actual socket ACK settings and post-handshake `LastRTT` samples.
  The reference is the median of the three per-run minimum RTTs. It includes
  radio scheduling and processing, while excluding receiver delayed-ACK waiting;
  it is neither geometric propagation RTT nor rolling SRTT. Matched default
  delayed-ACK controls are recorded separately. Workload sweeps retain their
  default delayed ACKs.
- Service capacity: UDP CBR, 512 B, offered loads of 512, 2048, 8192, 16384
  and 32768 kbit/s, with a fixed 256 KiB queue. **Every seed** must show a
  plateau within 15% between the top two offered loads, and the highest offered
  load must exceed delivered PDCP rate by at least 50%. A matched 512 KiB
  queue control at the highest load checks buffer sensitivity for every seed.
  Capacity is received PDCP SDU rate, with application goodput reported separately.
  It measures service under the current scheduler, errors and RLC recovery;
  the historical 120-byte initial-PDU cap is removed.

Current accepted reference after the connected-access and minimum UL grant corrections:
[88 completed runs and validation](results/queue-calibration-20261023-recovery/report.md),
[capacity and RTT plot](results/queue-calibration-20261023-recovery/calibration.png).

| Bandwidth | Direction | PDCP service capacity | Immediate-ACK path RTT | Directional BDP |
| --- | --- | ---: | ---: | ---: |
| 10 MHz | UL | 388.8 kbit/s | 138 ms | 6.55 KiB |
| 10 MHz | DL | 5240.2 kbit/s | 137 ms | 87.63 KiB |
| 20 MHz | UL | 410.6 kbit/s | 138 ms | 6.92 KiB |
| 20 MHz | DL | 10504.2 kbit/s | 137 ms | 175.67 KiB |

All 88 simulations completed with one relay switch each. The top two loads
and the larger-buffer controls produce identical delivered rates within each
seed. The previous delayed-ACK/capped-model reference is preserved in
[the old reference snapshot](results/queue-calibration-20261023-path-rtt/previous-reference.json).
The full knob sweep and full dataset have not been rerun against this reference.

Accepted per-buffer budgets, in bytes:

| Bandwidth | 0.5x | 1x | 2x | 4x | 8x | 16x |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 MHz | 44,869 | 89,738 | 179,476 | 358,951 | 717,902 | 1,435,804 |
| 20 MHz | 89,943 | 179,885 | 359,769 | 719,538 | 1,439,076 | 2,878,152 |

The 10 KiB floor remains available but does not bind any of these levels.
The common budget now follows the restored downlink capacity; excluding ACK
waiting does not imply smaller queues when measured service capacity changes.
The main knob sweep retains configured TCP endpoint limits except in the explicit
`socket-enlargement` sweep. That sweep compares `default` against
`socket-enlargement`, using a common finite power-of-two send/receive buffer at
least `2 * (largest calibrated queue + largest path BDP)` (currently 8 MiB each).
A flattened queue-size curve remains valid behavior when the default TCP socket
cannot put enough data in flight to fill a larger queue. On a fresh plan,
`--tcp-buffer-policy socket-enlargement` also enlarges sockets throughout the
other TCP sweeps; the socket knob still retains its explicit default control.
Per-run settings and exact limits are saved in the configurations. UDP and
workload delayed ACKs are unaffected.

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
metric audits, source hashes, and train/test archives. There are **77 transport
features** (73 legacy heads plus four TCP progress heads) and a separate **28-feature radio/KPI tensor** (historical collections
had 23). Splits hold out entire
RNG replicates (150 training / 50 test runs in the comparison preset); quantile
boundaries are fit only on training data. TCP-only observations remain **NaN for UDP**.

The full preset retains weak startup links, visibility gaps, failed connections,
zero delivery, and background starvation. Geometry/budget predictions are saved
as diagnostics. Clear-link queue calibration keeps its strict service checks.
The 14 deliberate stress profiles are currently disabled in the full preset.
Mixed TCP profiles sample `socket-enlargement` at both `default` and
`socket-enlargement` levels within each application, congestion-control and
direction stratum. Controlled anchors retain configured defaults. UDP keeps its
configured sockets. Per-run socket policy and byte limits are saved in the
manifest and configuration; both policies retain finite buffers.

`RLC_TX_DROP` events and four per-interval RLC queue-drop counters record actual
rejected SDUs/bytes by direction. They also appear in the radio tensor, alongside
path visibility. These are RLC transmit-queue drops, not all network losses.
`dataset-outcomes.json` retains each run's exit/application label; analysis adds
delivery, visibility and overflow summaries. Exit 1 is a completed application
failure and stays in both splits. Simulator crashes are retained for
investigation and block export. Empty connection windows keep their place in
the 100 ms grid with missing transport measurements; an unmeasured RTT is never
filled with zero. A native application's positive-delivery criterion can pass
despite heavy loss, so use delivery shortfall and drop counts too.

Run the focused 34-run check (including a loaded multi-UE case) without launching a full campaign:

```bash
python3 -m unittest discover -s scripts/dataset -p 'test_*.py'
python3 experiments/failure-regression/run.py --out-dir results/failure-check-new --plan --run --check --jobs 4
```

The [latest failure audit](results/failure-audit-20261023/report.md) verifies all
34 runs in the exported splits, including four application failures and 16 runs
with primary-UE RLC overflow. The audit also documents the remaining limits of
the radio-failure model.

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
