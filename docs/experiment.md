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
8°, paired 10 MHz service carriers at 2185 MHz DL / 1995 MHz UL, and a handheld
UE with 23 dBm conducted power and 0 dBi gain. Both ground-to-satellite paths
must meet a 30° elevation mask. Planning checks the complete run at 0.5 s
intervals with a conservative movement envelope and a 5° visibility margin.
It also checks initial uplink budget and the expected relay-switch timing.
These checks screen configurations; they do not guarantee packet delivery.

Relay switching changes the selected satellite path within the same NR cell.
It is not an inter-cell RRC handover. UE slot timing follows the changing
propagation delay. The outdoor LOS model excludes fast fading, local blockage,
vehicle-cabin attenuation, oscillator error and residual Doppler error.

## Experiment design

`scripts/dataset/run_dataset.py` defines 50 profiles with four matched RNG
replicates each. Each simulation lasts 130 s, with application activity from
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

Training/test separation holds out the fourth RNG replicate as a complete
group: 150 training runs and 50 test runs. Quantile boundaries are fitted only
on training data. Transport examples have 10 history and 10 future tokens.
Raw configs, logs, execution outcomes and source hashes identify each run.
Reports, plots, slides and raw data are generated artifacts and remain ignored.
