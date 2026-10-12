#!/usr/bin/env python3
"""Build UNUM-style train/test datasets from RTT token streams.

Mirrors the UNUM embedding preprocess flow:

  1. Load continuous RTT-token feature CSVs (``*-features.csv``).
  2. Cut non-overlapping windows of length context+prediction (default 20).
  3. Shuffle sequences (seeded) and split **80% / 20%** train / test.
  4. Fit Quantile-N edges on the **train** set only (``base_rtt`` stays float).
  5. Multi-head tokenize both splits with those edges
     (``searchsorted(..., side='right')``).
  6. Write ``train`` / ``test`` ``.npz`` + ``.pkl`` and shared boundaries.

Each example:

  history = sequences[:, :10, :]   # encoder input
  future  = sequences[:, 10:, :]   # tokens to predict
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bucketize_metrics import bucketize_value, quantile_boundaries  # noqa: E402
from tokenize_sequences import (  # noqa: E402
    build_matrix,
    continuous_feature_names,
    read_commented_csv,
    window_sequences,
)


def load_continuous_matrix(paths: list[Path]) -> tuple[np.ndarray, list[str], list[str]]:
    """Concatenate one or more features CSVs along time → (T, F_cont)."""
    matrices = []
    feature_names: list[str] | None = None
    for path in paths:
        fieldnames, rows = read_commented_csv(path)
        if not rows:
            print(f"warning: empty {path}", file=sys.stderr)
            continue
        names = continuous_feature_names(fieldnames)
        if feature_names is None:
            feature_names = names
        elif names != feature_names:
            raise SystemExit(
                f"feature schema mismatch in {path}:\n  {names}\nvs\n  {feature_names}"
            )
        matrix, _ = build_matrix(rows, names, mode="values")
        matrices.append(matrix)
    if not matrices or feature_names is None:
        raise SystemExit("no continuous feature rows loaded")
    return np.concatenate(matrices, axis=0), feature_names, feature_names


def fit_boundaries_on_train(
    train_cont: np.ndarray, feature_names: list[str], num_buckets: int
) -> dict[str, list[float]]:
    """Fit UNUM quantile edges on train sequences only; skip base_rtt."""
    boundaries: dict[str, list[float]] = {}
    for j, name in enumerate(feature_names):
        if name == "base_rtt":
            boundaries[name] = []
            continue
        vals = train_cont[:, :, j].reshape(-1)
        vals = vals[~np.isnan(vals)]
        if name == "ca_state":
            uniques = sorted({float(v) for v in vals})
            boundaries[name] = [
                (uniques[i] + uniques[i + 1]) / 2.0 for i in range(len(uniques) - 1)
            ]
            continue
        boundaries[name] = quantile_boundaries(vals, num_buckets)
    return boundaries


def tokenize_multi(
    continuous: np.ndarray, feature_names: list[str], boundaries: dict[str, list[float]]
) -> tuple[np.ndarray, list[str]]:
    """UNUM multi-head: base_rtt float + bucket ids for every other feature."""
    n, t, f = continuous.shape
    out_names: list[str] = []
    cols = []
    for j, name in enumerate(feature_names):
        plane = continuous[:, :, j]
        if name == "base_rtt":
            out_names.append("base_rtt")
            cols.append(plane.astype(np.float64))
            continue
        out_names.append(f"{name}_bucket")
        edges = boundaries.get(name, [])
        flat = plane.reshape(-1)
        buckets = np.array(
            [bucketize_value(None if np.isnan(v) else float(v), edges) for v in flat],
            dtype=np.float64,
        )
        # None → nan
        buckets = np.array(
            [np.nan if b is None else b for b in buckets], dtype=np.float64
        )
        cols.append(buckets.reshape(n, t))
    return np.stack(cols, axis=-1), out_names


def split_sequences(
    sequences: np.ndarray, train_frac: float, seed: int, shuffle: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = sequences.shape[0]
    idx = np.arange(n)
    if shuffle:
        rng = np.random.default_rng(seed)
        rng.shuffle(idx)
    n_train = int(np.floor(n * train_frac))
    if n >= 2 and n_train >= n:
        n_train = n - 1
    if n >= 2 and n_train < 1:
        n_train = 1
    train_idx = idx[:n_train]
    test_idx = idx[n_train:]
    return sequences[train_idx], sequences[test_idx], idx


def save_split(
    stem: Path,
    sequences: np.ndarray,
    context: int,
    feature_names: list[str],
    source_feature_names: list[str],
    meta_extra: dict,
) -> None:
    history = sequences[:, :context, :]
    future = sequences[:, context:, :]
    stem.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        stem.with_suffix(".npz"),
        sequences=sequences,
        history=history,
        future=future,
        feature_names=np.asarray(feature_names),
        source_feature_names=np.asarray(source_feature_names),
    )
    meta = {
        "n_sequences": int(sequences.shape[0]),
        "n_features": len(feature_names),
        "feature_names": feature_names,
        "source_feature_names": source_feature_names,
        "shapes": {
            "sequences": list(sequences.shape),
            "history": list(history.shape),
            "future": list(future.shape),
        },
        **meta_extra,
    }
    with open(stem.with_suffix(".json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
        handle.write("\n")
    with open(stem.with_suffix(".pkl"), "wb") as handle:
        pickle.dump(
            {
                "sequences": sequences,
                "history": history,
                "future": future,
                "feature_names": feature_names,
                "meta": meta,
            },
            handle,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features",
        action="append",
        default=[],
        help="continuous *-features.csv (repeatable; concatenated in order)",
    )
    parser.add_argument(
        "--out-dir",
        default="",
        help="output directory (default: beside first --features)",
    )
    parser.add_argument("--prefix", default="dataset", help="output file-name prefix")
    parser.add_argument("--context", type=int, default=10)
    parser.add_argument("--prediction", type=int, default=10)
    parser.add_argument(
        "--stride",
        type=int,
        default=0,
        help="window stride (0 = seq_len, non-overlapping like UNUM)",
    )
    parser.add_argument(
        "--train-frac",
        type=float,
        default=0.8,
        help="train fraction (UNUM paper: 80/20)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--no-shuffle",
        action="store_true",
        help="chronological split (last 1-train_frac = test) instead of shuffle",
    )
    parser.add_argument(
        "--num-buckets",
        type=int,
        default=50,
        help="quantile buckets target (UNUM default 50; edges may collapse)",
    )
    args = parser.parse_args()

    if not args.features:
        raise SystemExit("pass at least one --features path")
    paths = [Path(p) for p in args.features]
    for path in paths:
        if not path.exists():
            raise SystemExit(f"not found: {path}")

    context = args.context
    prediction = args.prediction
    seq_len = context + prediction
    stride = args.stride if args.stride > 0 else seq_len
    if not 0.0 < args.train_frac < 1.0:
        raise SystemExit("--train-frac must be in (0, 1)")

    continuous_t, feature_names, _ = load_continuous_matrix(paths)
    cont_seq = window_sequences(continuous_t, seq_len, stride)
    if cont_seq.shape[0] == 0:
        raise SystemExit(
            f"need at least {seq_len} RTT tokens to form a window "
            f"(have {continuous_t.shape[0]} across {len(paths)} file(s))"
        )

    train_cont, test_cont, order = split_sequences(
        cont_seq, args.train_frac, args.seed, shuffle=not args.no_shuffle
    )
    boundaries = fit_boundaries_on_train(train_cont, feature_names, args.num_buckets)
    train_tok, col_names = tokenize_multi(train_cont, feature_names, boundaries)
    test_tok, _ = tokenize_multi(test_cont, feature_names, boundaries)

    out_dir = Path(args.out_dir) if args.out_dir else paths[0].parent
    prefix = args.prefix
    shared_meta = {
        "sources": [str(p) for p in paths],
        "mode": "multi",
        "context": context,
        "prediction": prediction,
        "seq_len": seq_len,
        "stride": stride,
        "train_frac": args.train_frac,
        "seed": args.seed,
        "shuffle": not args.no_shuffle,
        "num_buckets": args.num_buckets,
        "n_rtt_tokens": int(continuous_t.shape[0]),
        "n_sequences_total": int(cont_seq.shape[0]),
        "layout": (
            f"UNUM-style: window {seq_len}, split {args.train_frac:.0%}/"
            f"{1 - args.train_frac:.0%}, fit Quantile-{args.num_buckets} on train, "
            f"multi-head tokenize (base_rtt float + buckets). "
            f"history=[:, :{context}], future=[:, {context}:]."
        ),
    }

    save_split(
        out_dir / f"{prefix}-train",
        train_tok,
        context,
        col_names,
        feature_names,
        {**shared_meta, "split": "train"},
    )
    save_split(
        out_dir / f"{prefix}-test",
        test_tok,
        context,
        col_names,
        feature_names,
        {**shared_meta, "split": "test"},
    )

    bounds_path = out_dir / f"{prefix}-boundaries-quantile{args.num_buckets}"
    bounds_payload = {
        "method": "quantile",
        "num_buckets": args.num_buckets,
        "fit_on": "train",
        "features": {
            name: {
                "index": i,
                "edges": boundaries.get(name, []),
                "n_edges": len(boundaries.get(name, [])),
                "n_buckets": len(boundaries.get(name, [])) + 1,
            }
            for i, name in enumerate(feature_names)
        },
    }
    with open(bounds_path.with_suffix(".json"), "w", encoding="utf-8") as handle:
        json.dump(bounds_payload, handle, indent=2)
        handle.write("\n")
    with open(bounds_path.with_suffix(".pkl"), "wb") as handle:
        pickle.dump(
            {
                "by_name": boundaries,
                "by_index": {i: boundaries[name] for i, name in enumerate(feature_names)},
                "feature_names": feature_names,
                "fit_on": "train",
            },
            handle,
        )

    print(
        f"rtt_tokens={continuous_t.shape[0]} sequences={cont_seq.shape[0]} "
        f"train={train_tok.shape[0]} test={test_tok.shape[0]} "
        f"shape={tuple(train_tok.shape)} "
        f"out_dir={out_dir} prefix={prefix}"
    )
    for name in feature_names:
        edges = boundaries.get(name, [])
        if name == "base_rtt":
            print(f"  {name}: continuous (not bucketized)")
        else:
            print(f"  {name}: n_edges={len(edges)} n_buckets={len(edges) + 1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
