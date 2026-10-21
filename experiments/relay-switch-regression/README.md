# Relay-switch timing regression

Run from the repository root after setup:

```bash
python3 experiments/relay-switch-regression/run.py --out-dir results/relay-switch-check --jobs 4
```

The runner builds an observer around the production scenario. It replays the
background settings and RNG runs of all seven crashes from the original knob
pilot. The fixture retains traffic and geometry through the 71.6 s relay switch,
then stops at 84 s to leave time for post-switch delivery checks. Four controls
cover a single UE, a fixed relay, and accelerated CBR/backlogged TCP switches.
`--cases original-3ue-32k-seed3` selects one original failure.

The bug occurred when a shorter relay path was selected inside an already
scheduled uplink slot. End-of-slot timing compensation came too late for the
first pending allocation: it arrived early at the gNB and overlapped valid DATA
or SRS reception from background UEs. The maintained NR patch checks the live
path delay before starting each pending UL allocation, defers it by the lost
propagation delay, and updates both timing references. Smooth orbital drift
continues to use the normal slot adjustment.

The observer records actual CTRL and DATA transmission callbacks; it does not
inject traffic or alter timing. Each case must:

- Exit successfully and complete its expected relay switch.
- Keep predicted UL arrival times within the existing **100 ns** receiver
  boundary tolerance after warm-up, including the switch interval.
- Deliver positive UL SDU bytes and continue transmitting for every primary
  and background UE after the switch.

SRS has no symbol index in its control-message trace, so it is excluded from
the phase CSV. The original eight-background-UE case still exercises the
receiver state that previously aborted on SRS/control overlap.

`summary.json` contains verdicts, timing errors and delivered bytes. `logs/`
contains simulator logs and transmission timing CSVs; `configs/` stores exact
inputs. Raw scenario metrics live in the separate result directories named by
each configuration's output prefix. A manifest hashes the executable, NR
library, maintained patch and experiment inputs. Successful cases can resume
under the same manifest; use a new directory after changing the code or case
selection.

The initial fixed-model run is preserved at
[`results/relay-switch-fixed-20261021/summary.json`](../../results/relay-switch-fixed-20261021/summary.json):
all 11 cases passed, with maximum observed arrival error 24 ns. Before applying
the fix, the original three-background-UE/32 kbit/s seed 30003 fixture reproduced
the fatal at 71.606860134 s, with 68,429 ns of DATA reception remaining; its log
is preserved under `results/relay-switch-fix-20261021/baseline.log`.
