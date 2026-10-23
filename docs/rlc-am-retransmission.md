# RLC AM recovery and scalar-channel decoding

The NR module's `NrRlcAm` implements the existing LTE-style, 10-bit sequence-number
format. The local patch adds retransmission resegmentation within that format;
it does not migrate the module to the native NR 12/18-bit formats.

## Retransmission state

Each unacknowledged sequence number retains its canonical original PDU. A
separate set of half-open payload intervals records bytes pending retransmission.
Whole-PDU and byte-range NACKs queue and merge these intervals. Emitting a
fragment consumes pending bytes without releasing the original. A positive ACK
releases the original and all associated state.

Every retransmission fits its actual RLC transmission opportunity, including the
serialized RLC header. The sender preserves the original sequence number and
absolute byte offsets, recalculates framing/length indicators at SDU boundaries,
and marks the segment containing the original payload's final byte. A segment
can be split again if later grants shrink. A grant too small for a header and one
payload byte produces no transmission.

Retry counts apply to recovery rounds, rather than individual emitted fragments.
A NACK for an already-pending range does not create another round. Maximum-retry
notification is raised once and deferred to the next simulator event so an RRC
callback cannot invalidate an in-progress buffer update.

The deprecated `TxOpportunityForRetxAlwaysBigEnough` attribute remains accepted
for configuration compatibility, but it no longer permits oversized PDUs.

## Reception and feedback

The receiver stores nonoverlapping payload slices by offset, discards duplicate
bytes, and waits for complete coverage from byte zero through the known final
byte. Receiving a last segment alone does not imply completion. Reassembly
preserves SDU boundaries, produces exactly one complete PDU for the existing SDU
reassembler, and preserves its RLC entity/timing tag. Peer reestablishment clears
partial state; the existing peer-entity guard rejects delayed old-epoch PDUs.

STATUS reports support multiple missing ranges for a sequence number, including
an unknown tail. If all ranges for one sequence number cannot fit a STATUS grant,
the receiver uses a whole-PDU NACK when possible. Otherwise ACK_SN stops before
that sequence number. Reporting only some holes must never implicitly acknowledge
the omitted holes. Truncated control headers are rejected.

Scheduler buffer reports count bytes pending transmission, rather than copies
retained solely for ACK recovery. Zero reports clear stale demand. Poll and
reordering timers recover from lost data/feedback, and expiry of the STATUS
prohibit timer advertises a waiting report.

The model retains its 15-bit segment offsets. Initial PDU data fields are bounded
to 32,768 bytes so any retransmitted byte can be represented.

## Grant packing and initial PDU sizing

The handheld reference scenario no longer sets the historical 120-byte
initial-PDU cap. It uses the model default, `MaxDataFieldBytes=0`, which lets
initial PDUs fit the available grant up to the format's 32,768-byte data-field
limit. Retransmissions can resegment if a later grant is smaller. Downlink
MAC can request multiple RLC PDUs per logical-channel allocation, charging
all MAC subheaders to that allocation and respecting the total transport-block
budget. It stops immediately when RLC makes no progress.

This removes the previous one-small-PDU-per-grant bottleneck. The optional
`MaxDataFieldBytes` attribute remains available for controlled experiments;
setting it changes segmentation overhead and the amount of data that fits in
the finite RLC transmit window. Uncapped lossy controls validated recovery before
removing the scenario override. Capacity and queue calibration must be
regenerated for this baseline. Historical validation artifacts describe their
original capped reference and explicit uncapped controls.

## Decoder correction

For scalar channels without an explicit channel matrix, the single-port fallback
matrix now contains `sqrt(received PSD)` per resource block. Its squared norm
therefore has the power units expected by the interference/error calculation.
It preserves inactive RBs and never reads the transmitter's current PSD after
propagation delay. Later transmissions cannot change the decoder input of an
already received burst. Explicit channel matrices retain their existing path.

## Regression checks

### UL grant prediction and application drain

The handheld scalar channel uses a fixed total UE transmit power shared across
used RBs. `UlGrantPowerAwareMcs` normalizes a PUSCH observation by its occupied
RB count, then predicts SINR for each candidate grant. AMC evaluates the full
candidate TB at the existing 10% error target. Allocation metrics and the emitted
DCI use the same grant-specific MCS. If no MCS meets that target, AMC marks the
candidate infeasible. OFDMA retains an existing usable narrower grant rather
than widening past the UE's power budget; refused RBs remain available to other
UEs. An outage UE that cannot form a usable grant is deferred. CQI expiry
restores the starting MCS; a
fixed-MCS control bypasses adaptation. HARQ retains its original TB parameters.

This prediction assumes a flat SISO channel and fixed total power over active
RBs. The module option defaults off. The scenario enables it for the handheld
scalar channel when uplink power control is disabled and power allocation is
`UniformPowerAllocUsed`. Fixed-PSD and uplink-power-control configurations keep
their configured adaptation path. This is not a general frequency-selective
channel predictor or a replacement for a full UE power-control model.

For the echo workloads, `trafficStop` now calls `StopGenerating`; the client
retains reception, pending TCP sends and feedback until `simStop`. New probes,
file transfers, RPC requests, stream frames and IoT reports cannot start during
the drain. Replies can still complete an existing file or RPC. Direct-traffic
sources retain their separate lifecycle.

The grant regression checks 3,975 past/future allocation transitions across
power levels, widths and symbol counts, plus CQI expiry, disabled prediction,
deep fades, grouped RBs and partial observation masks. The allocator
allocation checks also cover weak/strong UE sharing, unusable grants and
all-UE outage without loops or overlapping allocations. The application
regression checks TCP/UDP for all five workloads, a connection still opening at
the cutoff, backpressure, and idempotent generation shutdown.

From the repository root:

```sh
cd ns-3-dev
./ns3 configure --enable-tests --enable-examples '--filter-module-examples-and-tests=nr;internet;spectrum'
./ns3 build nr-test test-runner sat-bs-handset
./ns3 run 'test-runner --suite=nr-test-rlc-am-resegmentation --fullness=EXTENSIVE --verbose'
./ns3 run 'test-runner --suite=nr-spectrum-phy-power-matrix --verbose'
./ns3 run 'test-runner --suite=nr-gnb-mac-grant-fill --verbose'
./ns3 run 'test-runner --suite=nr-test-rlc-am-status --verbose'
./ns3 run 'test-runner --suite=nr-rlc-header --verbose'
./ns3 run 'test-runner --suite=nr-ul-grant-power --verbose'
./ns3 build echo-drain-regression
./ns3 run --no-build echo-drain-regression
```

The resegmentation suite enumerates 1,498 short split/overlap cases, checks legacy
and range-STATUS encoding, truncation, limited feedback grants, one-byte recovery,
maximum offsets, retry counters, and partial-state reestablishment. Its extensive
mode runs 1,024 reproducible lossy scenarios covering 61,056 SDUs. These include
changing grants, data and feedback loss, duplicates, reordering, capped/uncapped
initial PDUs, sequence-number wrap/reuse, and complete release of acknowledged
state. Every delivered payload is compared byte-for-byte in order.

The matrix regression checks all 16 later-TX occupancy masks for a four-RB burst,
no live TX PSD, inactive RBs, and received-power scaling. The packing regression
enumerates 513 LC budgets with eight RLC progress/cap settings and short/long MAC
subheaders, plus transport-block limits and zero-progress termination.

Validation artifacts and full-duration NTN controls are written separately under
`results/rlc-resegmentation-fix/`. Historical sweep results and queue calibration
are not rewritten. Grant/drain correction evidence is recorded separately under
`results/ul-grant-drain-fix/`.
