# All application rate details

This reference describes all nine applications supported by the experiment.
Values below describe the `comparison` preset and the application settings in the
49 switching-enabled full-preset anchors in
the [dataset planner](../scripts/dataset/run_dataset.py),
using [shared profile settings](../scripts/dataset/campaign_config.py). A single
scenario can have different values; its saved JSON configuration is authoritative.
See the [README](../README.md) for running instructions and the
[experiment explanation](experiment.md) for the network model.
The separate [full preset](experiment.md#full-dataset-preset) also varies source
rates, sizes and interarrival times; its ranges are saved with each plan.
The simulator also uses `dataRate` to calculate queue budgets. The full preset
rescales the queue multiplier to a separate reference rate so it can vary buffer
size independently of source pacing, including for unpaced native TCP bulk.

## What a rate means

All rates use decimal units: 1 kbit/s = 1000 bit/s and 1 kB = 1000 bytes.
An application's configured source rate is a target, not guaranteed delivery.
TCP backpressure, application dependencies and network capacity can reduce it.

The five synthetic workloads (probe, file, RPC, stream and IoT) have a **32-byte
application header inside each body**, plus a **4-byte length prefix per body**
on both TCP and UDP. Body rates include that header; useful-content rates exclude
it. Framed rates include the prefix, but still exclude IP, TCP/UDP and radio
overhead. The four native workloads have neither of these custom headers.

**Application goodput** measures bytes received by the destination application,
excluding IP/transport/radio overhead. For native applications these are the
bytes delivered to `PacketSink`; TCP retransmissions are not counted again.
Synthetic `bytesRx` instead counts decoded reply bodies, including their custom
header: probe/stream/IoT receive echoes, file receives ACKs and RPC receives
response objects. These counters cannot all be interpreted as one-way UL goodput.

**PDCP throughput** measures delivered data at the radio protocol boundary. It
includes network/transport headers and packets retransmitted by TCP. Its value
can therefore differ from application goodput and does not prove delivery to the
final application. RLC AM and HARQ operate underneath both TCP and UDP.

## Rates at a glance

Application activity is scheduled for 15–120 s in a 130 s run. The comparison
window is 20–119 s; packet timing and connection establishment affect realized
finite-window rates.

| Application | Current source setting | Meaning of the long-term rate | Traffic direction |
|---|---|---|---|
| Probe | 512-byte bodies, 10 kbit/s, CBR | 10 kbit/s body; 10.078 kbit/s framed, before backpressure | UL source and equal-size DL echoes |
| File | 10 kbit/s body pacing while sending; 3 s mean reading pause | No fixed sustained rate; pauses and completion reduce it | UL file chunks, DL application ACKs |
| RPC | 128-byte requests and random-size responses | Completion/think-time limited; `dataRate` does not pace it | UL requests, DL objects |
| Stream | 5 frames/s, 2 random-size slices/frame | About 7.893 kbit/s body / 8.213 kbit/s framed | UL slices and equal-size DL echoes |
| IoT | Uniform 64–128-byte bodies; 1.5 s mean interval | 0.512 kbit/s body / 0.533 kbit/s framed in expectation | UL reports and equal-size DL echoes |
| VoIP | Native NGMN voice/silence generator | 12.2 kbit/s speech; 0.75 kbit/s silence; variable aggregate | One-way UL or DL; UL in full-run profiles |
| Bulk / iperf-style | TCP unpaced; UDP paced at 10 kbit/s | TCP is capacity/congestion limited; UDP is fixed-rate | One-way UL or DL; UL in full-run profiles |
| CBR | Native always-ON source, 512 bytes, 10 kbit/s | Fixed-rate source target | UL and DL both exercised |
| OnOff | Native source, 512 bytes, 10 kbit/s during ON; 1 s ON / 1 s OFF | About 5 kbit/s over complete cycles | One-way UL or DL; UL in full-run profiles |

## Probe

`application.workload: "probe"` uses `packetSize: 512` and `dataRate: "10kbps"`.
The packet spacing is `512 × 8 / 10000 = 0.4096 s`. Of each body, 480 bytes are
content and 32 bytes are the synthetic header. The nominal content rate is
therefore 9.375 kbit/s. Each reply echoes the entire body and adds its own prefix.

`application.probe.traffic` also supports `onoff` and `bursty`. Their ON/OFF
durations are random; a nominal duty-weighted source rate is
`dataRate × meanOn / (meanOn + meanOff)`. Bursty mode also uses exponential
packet gaps. The full run selects continuous CBR probe traffic.

## File

`application.file` selects a log-normal **file content size** with
`sizeMu = ln(16000)`, `sizeSigma = 0.3`, and rejection above `sizeMax: 128000`.
Its median is 16 kB and its mean is approximately 16.74 kB.

Each full chunk carries 480 content bytes plus the 32-byte header. The final
chunk can be shorter. Gaps are exponential with mean
`chunkBodyBytes × 8 / dataRate`; 10 kbit/s is the active body pacing target,
not a file-transfer completion rate. Framing adds 4 bytes per chunk.

The server returns a 32-byte application ACK body per chunk. Only after all
chunk ACKs arrive does the sender start an exponential reading pause with mean
`readingMean: "3s"`, then start another file. TCP backpressure, final-chunk
effects, ACK delays and reading pauses determine the sustained rate. Missing
application ACKs can stall a UDP file; switching to UDP does not remove the
application's completion dependency.

## RPC

`application.rpc` uses 128-byte request bodies. Response body sizes follow a
bounded Pareto distribution with `objectScale: 128`, `objectShape: 2`, and
`objectMax: 1400`: approximately 234.55 bytes on average before integer rounding.
Both request and response sizes include the 32-byte header and receive a
4-byte prefix on the wire.

`objectsMean: 2` means a geometric count **plus one**, giving three objects per
page on average. Requests are sequential: the next object waits for the current
response, then an exponential gap with mean `objectGap: "0.2s"`. Between pages,
the mean reading time is `readingMean: "1.5s"`. `count: 0` means no configured
request-count limit. `dataRate` does not control RPC; RTT, response completion
and these gaps determine its source rate. `computeDelayUs: 0` adds no server
think time in the full run.

## Stream

`application.stream.frameInterval: "200ms"` and `slicesPerFrame: 2` target ten
slices/s. Content size is bounded Pareto with scale 40 B, shape 2 and cap 200 B:
approximately 66.67 B per slice before integer rounding. Each slice then adds
the 32-byte header and 4-byte prefix.

- Content: `10 × 66.67 × 8 ≈ 5.333 kbit/s`.
- Body: `10 × (66.67 + 32) × 8 ≈ 7.893 kbit/s`.
- Framed: `10 × (66.67 + 32 + 4) × 8 ≈ 8.213 kbit/s`.

Within-frame gaps are bounded Pareto with scale 2.5 ms, shape 1.2 and cap 10 ms.
`dataRate` and the top-level `packetSize` do not pace or size the slices.
Backpressure and unfinished/skipped slices can reduce the realized rate.
This is synthetic frame traffic with echoes, not a video codec or player.

## IoT

`application.iot` uses inclusive uniform body sizes of 64–128 B, averaging 96 B,
and exponential gaps with mean `interval: "1.5s"`.

- Content: `(96 − 32) × 8 / 1.5 ≈ 0.341 kbit/s`.
- Body: `96 × 8 / 1.5 = 0.512 kbit/s`.
- Framed: `(96 + 4) × 8 / 1.5 ≈ 0.533 kbit/s`.

`dataRate` does not control this workload. Random report counts create
substantial variation over a short run, and the server echoes each report.

## Native VoIP

`application.workload: "voip"` installs 5G-LENA's `TrafficGeneratorNgmnVoip`.
The pinned generator defaults are 40-byte speech packets paced at 12.2 kbit/s,
15-byte silence descriptors every 160 ms (0.75 kbit/s), voice activity factor
0.5, and 2 s mean talk spurts. State transitions are evaluated every 20 ms.

Speech packet spacing is `40 × 8 / 12200 ≈ 26.23 ms` in this implementation.
A simple 50/50 speech/silence reference is 6.475 kbit/s, but packet timers,
state changes and finite duration affect the realized average. This is a voice
traffic model, not encoded audio. JSON `dataRate` and `packetSize` do not override
these native VoIP defaults; changing them requires exposing generator attributes.
There is no application echo.

## Native bulk / iperf-style

`application.workload: "iperf"` with TCP installs `BulkSendApplication` and
`PacketSink`. `MaxBytes: 0` leaves it continuously backlogged and `packetSize`
sets the write size (512 B here). `dataRate` does not cap TCP bulk sending.
The full run compares NewReno, CUBIC and BBR using this same source.

With UDP, the implementation uses an always-ON native `OnOffApplication` at
`dataRate` and `packetSize`, currently 10 kbit/s and 512 B. Consequently the TCP
and UDP bulk profiles have different offered-load rules; they are not a matched
transport experiment. This is not the external iperf/iperf3 protocol.

## Native CBR and OnOff

`application.workload: "cbr"` uses an always-ON native `OnOffApplication` with
512-byte packets at 10 kbit/s: one packet every 409.6 ms. There is no echo or
custom application framing. For another fixed rate, change `dataRate` and
`packetSize`; for example, 160 B at 64 kbit/s gives a 20 ms interval.

`application.workload: "onoff"` uses the same native source but with fixed
1 s ON and 1 s OFF phases. Its rate is 10 kbit/s during ON and nominally
5 kbit/s over complete cycles; initial phase, packet timers, backpressure and
the observation window affect the actual average. These native phase lengths
are currently fixed in the installer, rather than using `application.probe`
ON/OFF settings.

Both applications accept `transport: "tcp"` or `"udp"` and `direction: "ul"`
or `"dl"`. Native CBR with identical size/rate settings is the matched TCP/UDP
comparison in the overall plots.

## Background UE traffic

Background UEs use native one-way UL CBR over UDP, with **256-byte** application
packets. Selected profiles offer 5, 20 or 80 kbit/s per UE, corresponding to
409.6, 102.4 or 25.6 ms packet spacing. Three-UE profiles use 5 or 20 kbit/s
per UE, giving 15 or 60 kbit/s total. These rates exclude protocol overhead.
Background sources start at 16 s and stop at 120 s. Their byte counters are in
`background-<index>-application.csv`, separately from the primary application's.

## Reading the TCP/UDP plot

`primary-application.csv` records cumulative source/sink application bytes and
one-second received-rate samples for native workloads. Goodput in the plot is
`8 × (rxBytesAt119 − rxBytesAt20) / 99 / 1000`, in kbit/s.

The second panel uses all source bytes and the final receiver count after
draining: `100 × (sourceBytes − receivedBytes) / sourceBytes`. It is the
**delivery shortfall at the cutoff**, not proof of permanent packet loss. The
native sink is scheduled to remain active for 9 s after the source stops.
Per-packet application delay is not present in these saved traces. TCP SRTT
cannot substitute for UDP delay; TCP-only metrics remain NaN for UDP.

RNG runs are shown individually, with their median as the bar (four repeats in
the comparison preset, twenty by default in the full preset). PDCP
metrics remain useful for explaining radio behavior, but they are not substituted
for application goodput in this transport comparison.
