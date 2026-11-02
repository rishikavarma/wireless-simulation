# Bucket prediction experiment (prepared, not run)

The training script is [`scripts/models/train_unum_buckets.py`](../scripts/models/train_unum_buckets.py).
It uses the finalized `windows-profile-c10-p10-s20` dataset from the short
campaign. The script has **not** been run or deployed. It is ready to run after
the collector finishes and the archive is fetched and unpacked.

The model follows the main ideas in [UNUM (NSDI 2026)](https://www.usenix.org/system/files/nsdi26-chen-jiayi.pdf):
feature-wise bucket embeddings, a causal Transformer context encoder, learned
future queries in a Transformer decoder, and a separate classification head
for each bucketized feature. The model predicts all ten future RTT-scale tokens
from ten context tokens. The training objective combines per-feature cross
entropy with a small ordinal penalty. This is a compact adaptation for this
dataset, not a reproduction of the paper's controller experiments.

Three model sizes are trained with early stopping. Validation holds out whole
profiles from the training split; the existing profile-held-out test split is
used once after selecting the lowest validation bucket MSE. The input includes
continuous `base_rtt` and any other direct-valued features. Only `_bucket`
features enter the bucket prediction score. Missing future feature values are
excluded from the score. Missing context values get a learned input embedding;
the two baselines fall back to that feature's training-set median bucket.

The two baselines predict every future step by (1) carrying forward the final
context bucket and (2) rounding the mean bucket over observed context tokens.
The reported MSE is the mean squared difference between predicted and true
**bucket indices**, using the same observed targets for all three methods.
Because features can have different numbers of bins, the outputs also include
per-feature scores. Lower MSE is better.

When authorized to run, use a new output directory:

```bash
tar -xzf local/final-dataset/short-distributed-300-20k.tar.gz -C local/final-dataset
python3 scripts/models/train_unum_buckets.py \
  --dataset local/final-dataset/short-distributed-300-20k/windows-profile-c10-p10-s20 \
  --output local/unum-bucket-experiment
```

The default run evaluates compact, balanced, and wide Transformer settings,
sampling up to 500,000 training windows per epoch and 100,000 validation windows
per evaluation. The held-out test evaluation uses every test window. Options
include `--epochs`, `--patience`, `--batch-size`, `--train-windows-per-epoch`,
`--validation-windows`, and `--device`.

Outputs are `tuning.json`, selected checkpoint, `comparison.json`,
`feature-mse.csv`, `horizon-mse.csv`, and four PNG plots. The plots compare
per-feature MSE, MSE across the ten future steps, improvement over persistence
for each feature, and a feature-by-horizon MSE heatmap. No scores or plots exist
until the script is run.
