# Experiment design, model and measurements

This document describes the experiment settings and measurement definitions.
Commands for building and running the dataset are in the root [README](../README.md).

## Model

The path is UE → transparent LEO relay → ground gNB/application server. The
satellite has no NR device or IP application. Live feeder and service ranges
determine propagation delay and the equivalent two-hop received spectrum.
The receiver uses 5G-LENA's analytical PHY error model, NR scheduling, HARQ and
RLC AM. TCP or UDP runs above the same radio stack.

The reference uses an 800 km orbit at 53° inclination, two relays separated by
8°, paired 10 MHz carriers at 2135 MHz DL / 1945 MHz UL on both service and
feeder links, and a handheld UE with 23 dBm conducted power and 0 dBi gain. Both ground-to-satellite paths
must meet a 30° elevation mask. Planning checks the complete run at 0.5 s
intervals with a conservative movement envelope and a 5° visibility margin.
It also checks initial uplink budget and the expected relay-switch timing.
These checks screen configurations; they do not guarantee packet delivery.

Relay switching changes the selected satellite path within the same NR cell.
It is not an inter-cell RRC handover. UE slot timing follows the changing
propagation delay. The outdoor LOS model excludes fast fading, local blockage,
vehicle-cabin attenuation, oscillator error and residual Doppler error.

## Experiment design

The default `comparison` preset in `scripts/dataset/run_dataset.py` defines
50 profiles with four matched RNG replicates each. Each simulation lasts 130 s, with application activity from
15–120 s and measurements from 20–119 s.

| Factor | Settings in the full run |
|---|---|
| Applications | Probe, file, RPC, stream, IoT, native VoIP, bulk, CBR and OnOff |
| UE motion | Stationary or 1.4, 15, 30, 50 m/s; 0.5 km and 50 km disks |
| Relay switching | Enabled/disabled, with matched control runs |
| Link attenuation | Service excess loss 0/1.5/3 dB; feeder excess loss 0/12/24 dB in selected profiles |
| Background load | One or three stationary UEs; 5/20/80 kbit/s per UE in selected profiles; relay 0 or 1; 500 m or 50 km separation |
| Transport | TCP/UDP comparisons; NewReno, CUBIC and BBR for unpaced bulk traffic |
| Direction | UL for synthetic request/reply sources; native applications support UL/DL, with both directions exercised for CBR |

This is a selected-profile design, not an exhaustive Cartesian product.
Movement radius also sets the UE's initial displacement, so each radius has a
stationary control. A short trajectory need not explore its entire disk.

Background UEs attach to the same gNB and share its scheduler, carriers and EPC.
Their relay is selected explicitly; distance alone does not select a satellite.
Each UE has its own geometry and timing. There is no independent feeder queue
or detailed two-hop co-channel interference model. Changing relay does not
remove contention at the shared gNB scheduler.

## Full dataset preset

The separately selected `--preset full` is defined by
[`full_dataset.json`](../scripts/dataset/full_dataset.json). It defaults to 2,000
profiles and 20 independent RNG repeats per profile. It derives 49 controlled
anchors from `comparison`, enabling automatic relay selection in each and
merging the now-identical baseline and relay-switch profile. Another 1,951
profiles sample joint conditions within balanced application/transport/direction strata. The
design is a stratified sample of the joint space, not its full Cartesian product.
Native apps have UL/DL strata; the five synthetic apps originate UL.
The table gives candidate levels for the additional profiles. Anchors retain
their original settings, including the 5/20/80 kbit/s background sources.

| Factor | Full-preset candidate values |
|---|---|
| Applications | All nine; sizes, timing and source rates also vary where supported |
| TCP | NewReno, CUBIC, BBR, Vegas, Hybla, WestwoodPlus, crossed with each application |
| UDP | Every application; TCP-only feature heads remain NaN |
| Source pacing | 0.5, 2, 10, 32, 128, 512 kbit/s for paced workloads; TCP bulk remains backlogged |
| Packet/write size | 128, 256, 512, 1200 B for probe/file/native bulk/CBR/OnOff |
| Queue budget | 0.5, 1, 2, 4, 8, 16 × measured clear-link capacity/unloaded-RTT BDP; 10 KiB minimum |
| Motion | Stationary or 0.5, 1.4, 5, 10, 15, 30, 50 m/s; disks of 0.1, 0.5, 5, 20, 50 km |
| Background | 0, 1, 3, 8 stationary UEs; 2, 10, 32, 128 kbit/s per UE; relay 0/1; 0.5, 5, 50 km separation |
| Geometry | Orbit altitude fixed at 800 km, inclination 53°, relay spacing 8°; gateway north/east offsets −20, 0, 20 km |
| Paired service bandwidth | 10 or 20 MHz per direction; carrier center frequencies stay fixed |
| Attenuation | Service 0, 0.75, 1.5, 2.25, 3 dB; feeder 0, 6, 12, 18, 24, 30 dB |
| Switching | Automatic relay selection enabled in every configuration, within the same NR cell |
| File | Median content 16 kB/128 kB/1 MB; reading means 0.1/1/3/10 s |
| RPC | Requests 64/128/512 B; response scale 64/128/512 B; reading means 0.2/1.5/5 s |
| Stream | 5/15/30 frames/s; 1/2/4/8 slices/frame; content scales 40/120/500 B, cap 1000 B |
| IoT | Minimum body 64/128/512 B, maximum twice that; mean intervals 0.05/0.2/1.5/5 s |

Inactive rate knobs are not randomized: VoIP keeps its native codec-traffic
defaults, and `dataRate` does not cap TCP bulk or pace RPC/stream/IoT. Native
OnOff retains its fixed 1 s ON/1 s OFF phases. RLC AM, the 120-byte new-PDU cap,
LOS assumptions and the absence of waveform/Doppler outputs remain part of the
model; changing bandwidth does not make this a peak-capacity measurement.

Full profiles, including controlled anchors, use an explicit byte budget from
[`experiments/queue-calibration/reference.json`](../experiments/queue-calibration/reference.json).
Separate UL/DL experiments measure saturated PDCP SDU rate and post-handshake
unloaded data RTT at 10 and 20 MHz. Each bandwidth uses the larger directional
BDP as its common per-buffer reference. Multipliers are applied independently of
application pacing, with the existing 10 KiB minimum. The manifest records the
nominal factor and actual `queueSizeBytes`; lower factors can be clamped by the
floor. Attenuation/background changes retain this clear-link reference. See the
[root README calibration procedure](../README.md#queue-calibration-experiments)
for loads, repeats, acceptance checks and measurements. Comparison profiles
retain their legacy offered-rate/geometric-RTT sizing when no explicit byte
budget is supplied.

Relay selection follows the existing visibility, path advantage, hysteresis and
time-to-trigger rules. Full-preset admission selects geometries predicting one
switch inside the measurement window. The `comparison` preset retains its
original switch-disabled controls.

Full-preset admission checks use a conservative envelope covering both the
primary UE's disk and background separation. Candidates outside visibility,
bootstrap-budget or switch-time constraints are rejected before configurations
are emitted. Only admitted configurations enter the manifest; those checks do
not prove successful application delivery. Poor-performance interaction runs
remain observations when they finish without crashing, including zero-delivery
background flows. Geometry constraints also mean not every listed combination
can be admitted.

All profiles share the same 20 RNG run numbers (10001–10020); these are matched
comparisons across profiles, with independent repeats within each profile.
Replicates 16–19 (RNG 10017–10020) are held out as a group. This tests unseen
RNG realizations of the configuration distribution, not unseen knob values.
The 49 anchor profiles supply the controlled plots; a separate coverage page
shows descriptive distributions across mixed conditions. The full preset's
switching page shows observed protocol and geometric RTT before/after the event;
it has no switch-disabled control and does not establish the causal benefit of
switching. The comparison preset retains its matched switching/control plot.

### Size and relation to UNUM

The [first UNUM paper, Appendix 8.1](https://www.usenix.org/system/files/nsdi26-chen-jiayi.pdf)
reports over 35 million unique samples, crossing applications and Linux
congestion-control algorithms across network conditions, with an 80/20 split.
Its Table 1 includes 6–192 Mbit/s bottlenecks, 10–160 ms base RTTs and
0.5–16 BDP queues; it also uses adaptive-video workloads and real bandwidth
traces. That motivates wider interactions and training volume here.

Our default full plan has 40,000 × 990 = **39.6 million distinct time bins**;
each bin produces one transport state vector. With non-overlapping windows of
20 tokens, each run supplies 49 windows: **1.96 million windows**, split into
1,568,000 training / 392,000 test windows. The last ten tokens per run are not
part of a complete window. A time bin is not a guaranteed unique feature vector.

These counts are an explicit size objective, not equivalence to UNUM's sample
definition or a reproduction of its dataset. Our tokens are fixed at 100 ms
(shared with UDP); UNUM aggregates at RTT scale. The satellite model lacks
adaptive bitrate control and replay of real bandwidth traces. Radio bandwidth
in MHz is also not an application bottleneck rate in Mbit/s. More runs cannot
remove those differences.

Use `--target-windows` to request a minimum number of complete examples rather
than time bins. A target of 35,000,000 windows with 20 repeats and 49 windows/run
requires 35,715 profiles × 20 = **714,300 runs**, producing 35,000,700 windows.
`--describe` calculates this without generating configurations or launching ns-3.
The default full setting stays at 40,000 runs; changing the window target is
explicit and increases actual collection volume, rather than repeating windows.

### Full-preset storage

The full preset processes at most 100 runs per transport/radio shard. Transport
shards hold `sequences`, feature names, run IDs and truncation flags; slice the
first/last ten tokens for history/future. The two index JSON files list all
shards and run identities. Quantile fitting uses a seeded uniform random-priority
sample of up to 200,000 **training time tokens**, never held-out tokens; the
actual sample count and training-token count are recorded. The comparison
preset retains its existing archives and exact quantile fitting. The sharded
layout requires an index-aware training loader, rather than a loader expecting
a single `dataset-train.npz` file.

## Metric coverage

Collection uses `metrics.physical: true`; optional `metrics.payload: true`
dumps serialized MAC PDU bytes. No waveform or bit-level decoder is executed.

| Requested signal | Available measurement or limitation |
|---|---|
| MSG 326, pre-FEC payload | Serialized MAC PDU bytes and byte counts at UE PHY TX; scheduled TB size is separate. Not a complete encoded/padded TB bitstream. |
| MSG 327, coded/scrambled bits | Unavailable: no bit-level CRC attachment, LDPC encoder or scrambler output. |
| MSG 27–30, PUSCH IQ, DMRS and channel estimates | Unavailable: no sampled waveform or pilot-based channel estimation/interpolation. |
| MSG 31, decoded bits | Original simulated MAC PDU bytes after an analytical decode-success decision; not decoder-produced bit arrays. |
| SINR, MCS and CRC outcome | Allocated-RB mean SINR, MCS, TB size, RV, analytical TBLER and modeled pass/fail per TB. CRC pass/fail is not a computed CRC check. |
| HARQ | ACK/NACK generation, direction, RNTI and process ID; TX allocations also include RV/NDI. Generated feedback does not imply successful feedback reception. |
| BSR | UE control TX/RX events and four quantized logical-channel-group buffer levels, not exact queued bytes. |
| RACH/RRC | Control events and connection/reconfiguration state metadata. No PRACH waveform or full ASN.1 message bodies. Same-cell relay selection creates no RRC handover. |
| RLC/PDCP | TX/RX sequence numbers, size, LCID, direction and RNTI; AM RLC has 10-bit SNs and PDCP has 12-bit SNs, with wrapping. |
| Timing/frequency offset | Geometric delay, configured timing advance, modeled UL slot-phase error, speed and position. No RF synchronization or frequency-offset estimate. |

TB error probability depends on SINR, MCS, TB size and HARQ history. Successful
reception is sampled from that probability. Original packet bytes are retained;
corrupted bit sequences are not fabricated. UL measurements are filtered to
the primary UE's actual RNTI, and repeated per-packet reports of one TB are
deduplicated for radio comparisons.

## Seven interval KPIs

`<prefix>-kpis.csv` reports each UE over `metrics.intervalS` (default 1 s),
including idle periods and a final partial interval. UE index 0 is the primary.
These are local simulator measurements, without a live FlexRIC/E2 connection.

| KPI | Definition |
|---|---|
| `DRB.PdcpSduVolumeDL_kB`, `DRB.PdcpSduVolumeUL_kB` | Interval bytes submitted to data-bearer PDCP, excluding its 2-byte header, divided by 1000. These are kilobytes, not kilobits. |
| `DRB.RlcSduDelayDl_us` | Mean completed DL SDU delay from PDCP submission before RLC service to reassembled delivery back to PDCP; includes queuing, segmentation, retransmission and propagation. No completed SDU gives NaN. |
| `RRU.PrbTotDl`, `RRU.PrbTotUl` | Sum of RBs in actual data TX allocations, including retransmissions; repeated scheduling counts again. Control/SRS are excluded. These are allocation totals, not utilization percentages. Companion PRB-symbol counts include allocation duration. |
| `DRB.UEThpDl_Mbps`, `DRB.UEThpUl_Mbps` | Delivered PDCP SDU bits divided by the actual interval and 1e6; idle time stays in the denominator. |

PDCP volumes/rates include network and transport overhead. They differ from
application payload goodput. Raw allocation and protocol-event files allow
volume and PRB totals to be reconciled independently.

## Application data rates

Rates below describe source settings before IP/transport/radio overhead.
Delivered rate depends on the stack and available capacity.
The [application rate reference](application-rates.md) explains every supported
workload, its packet framing, active/average rates, direction and configuration
controls in detail.

| Application | Source behavior and rate controls |
|---|---|
| Probe | 512-byte bodies at 10 kbit/s, one every 409.6 ms, echoed DL. ON/OFF and bursty modes are also supported. |
| File | Log-normal payload, 16 kB median, sigma 0.3, 128 kB cap; 512-byte bodies including a 32-byte header. Chunks paced at 10 kbit/s while active, then 3 s mean reading time after completion. |
| RPC | 128-byte requests; bounded-Pareto responses with scale 128 B, shape 2, cap 1400 B; mean three objects/page, 0.2 s object gaps and 1.5 s reading time. Completion gates the next request; no fixed offered bit rate. |
| Stream | Five frames/s, two slices/frame; bounded-Pareto payloads with scale 40 B, shape 2, cap 200 B, plus a 32-byte header. Expected body rate about 7.89 kbit/s; `dataRate` does not pace it. |
| IoT | Uniform 64–128 B bodies with exponential 1.5 s mean interval: expected body rate 0.512 kbit/s. |
| VoIP | Native NGMN generator: 12.2 kbit/s during speech, 15 B silence descriptors every 160 ms, voice activity 0.5, mean 2 s talk spurts. No synthetic echo. |
| Bulk/iperf-style | Native TCP BulkSend/PacketSink, unpaced and backlogged. UDP uses a paced native OnOff source at configured `dataRate`. This is not the external iperf protocol. |
| CBR | Native OnOff source configured always ON, 512 B at 10 kbit/s. |
| OnOff | Native source with 1 s ON / 1 s OFF, 10 kbit/s during ON, approximately 5 kbit/s over repeated cycles. |

The five synthetic workloads add a 4-byte length prefix per body under both
TCP and UDP. Stream therefore offers about 8.21 kbit/s including framing, and
IoT about 0.533 kbit/s. Native applications have no custom 32-byte/4-byte
framing. File/RPC retain application-level response dependencies, and UDP uses
the same RLC AM bearer as TCP.

## Outputs and dataset split

Raw files contain TCP samples, direction-specific SINR, TB allocations/outcomes,
protocol events, interval KPIs and per-UE paths. TCP is sampled every 5 ms;
SRTT is expressed in units of 100 ms in the CSV. Dedicated PHY files retain
reports separately from held values on transport samples.

The full run folds transport samples into fixed 100 ms tokens for both TCP
and UDP. The transport dataset has 73 features; TCP-only fields remain NaN for
UDP. Separate radio arrays contain 23 continuous features over 99 one-second
measurement intervals, with shape `[runs, 99, 23]`. Idle rates/counts are zero;
unobserved delay, SINR, MCS and BSR values are NaN.

For the comparison preset, training/test separation holds out the fourth RNG replicate as a complete
group: 150 training runs and 50 test runs. Quantile boundaries are fitted only
on training data. Transport examples have 10 history and 10 future tokens.
Raw configs, logs, execution outcomes and source hashes identify each run.
Reports, plots, slides and raw data are generated artifacts and remain ignored.
