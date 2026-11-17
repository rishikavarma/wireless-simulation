# Running on TACC

`satellite_study.sh` builds the scenario, collects datasets and trains the UNUM bucket
forecasters on TACC (Vista, Lonestar6 or Stampede3). Queues are chosen from `$TACC_SYSTEM`.

**Allocation and machine.** The account `rishikavk` uses project ASC26140 on Vista.

| Vista queue | Node | Cost |
|---|---|---|
| `gg` | 144 Grace Arm cores, 237 GB | 0.33 SU per node-hour |
| `gh` | 72 cores and one H200 | 1 SU per node-hour |
| `gh-dev` | Grace-Hopper, 2 h limit | 1 SU per node-hour |

ns-3 is built with `gcc/14.2.0`, `cmake/3.31.5` and `eigen/3.4.0`. Python is the
system 3.9, in a venv at `$SCRATCH/wireless-simulation/venv`.

## One-time access

Add to `~/.ssh/config` on your own machine:

```
Host tacc-vista
  HostName login1.vista.tacc.utexas.edu
  User rishikavk
  ControlMaster auto
  ControlPath ~/.ssh/cm-%r@%h:%p
  ControlPersist 8h
```

Run `ssh -fN tacc-vista` once and enter your password and TACC token. Later `ssh` and
`rsync` calls reuse that connection for 8 hours.

## Commands

```bash
bash scripts/tacc/sync.sh                     # working tree -> $WORK/wireless-simulation (no data)
ssh tacc-vista 'cd $WORK/wireless-simulation && bash scripts/tacc/satellite_study.sh info'
ssh tacc-vista 'cd $WORK/wireless-simulation && bash scripts/tacc/satellite_study.sh setup'   # login node
ssh tacc-vista 'cd $WORK/wireless-simulation && ACCOUNT=ASC26140 bash scripts/tacc/satellite_study.sh build'
ssh tacc-vista 'cd $WORK/wireless-simulation && ACCOUNT=ASC26140 bash scripts/tacc/satellite_study.sh pilot'
ssh tacc-vista 'cd $WORK/wireless-simulation && ACCOUNT=ASC26140 TRAIN=smoke bash scripts/tacc/satellite_study.sh train'
```

* **`setup`** builds the venv and clones ns-3.48 and 5G-LENA v5.1 on the login node,
  because compute nodes may have no outbound network. Chronos 2.x needs Python 3.10 or newer and
  is skipped; `train_unum_buckets.py` does not use it.
* **`build`** runs `scripts/setup.sh --skip-deps --simulator-only` (patches, configure,
  build), then the smoke config and its bucketization. `SETUP_KEEP_SHELL_RC=1` stops
  `setup.sh` from editing `~/.bashrc`.
* **`pilot`** is a 150-run streamed full-preset campaign: 50 profiles × 3 RNG
  replicates, 20 s measured. It is the smallest plan with train/validation/test splits.
  Its sample-split windows are the default training dataset.
* **`dataset`** runs any `run_dataset.py` campaign, for example
  `DATASET_ARGS="--preset full --stream --design-file scripts/dataset/short_dataset.json --out-dir $SCRATCH/wireless-simulation/short --run-prefix short"`.
* **`train`** runs Transformer (delta and absolute targets), MLP and LSTM on `DATASET`
  through PyLauncher.
  * `TRAIN=full` uses the 1,505-run comparison training budget (w256, 60 epochs).
  * `TRAIN=smoke` is 1 epoch.
  * `GPU=1` uses `gh`.
  * Finished models are skipped on resubmission.

**Chaining.** `AFTER=<job id>` makes a submitted job wait for another, for example
`AFTER=<pilot id> TRAIN=smoke ... train`.

**Storage.** Data and models live in `$SCRATCH/wireless-simulation`, which is purged
after 10 days without access. Copy results you keep to `$WORK`.

**Existing datasets.** The 1,505-run datasets and the 70 GB `local/final-dataset` archive
are not on TACC. To train on them, copy them with `rsync` to
`$SCRATCH/wireless-simulation/` and pass `DATASET=<windows directory>`.
