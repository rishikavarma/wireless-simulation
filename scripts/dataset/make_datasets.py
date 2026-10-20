#!/usr/bin/env python3
"""Build UNUM-style train/test datasets from RTT token streams.

Mirrors the UNUM embedding preprocess flow, with **run-level** holdout:

  1. Load continuous RTT-token feature CSVs (``*-features.csv``), one per run.
  2. Cut non-overlapping windows of length context+prediction (default 20)
     *within each run* (never across run boundaries).
  3. Split **whole runs** 80/20 train / test (seeded). Optional knob-space
     holdout (e.g. high ``dataRateMbps`` / ``computeDelayUs``) goes to test.
  4. Fit Quantile-N edges on the **train** set only (``base_rtt`` stays float).
     TCP loss uses a zero class followed by positive-only quantiles.
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
from bucketize_metrics import (  # noqa: E402
    bucketize_value,
    feature_quantile_boundaries,
    has_zero_loss_class,
)
from tokenize_sequences import (  # noqa: E402
    build_matrix,
    continuous_feature_names,
    read_commented_csv,
    window_sequences,
)


def prefix_from_features_path(path: Path) -> str:
    """Recover a run identifier by stripping the known features CSV suffix, falling back to the
    ordinary filename stem.
    """
    name = path.name
    if name.endswith("-features.csv"):
        return name[: -len("-features.csv")]
    return path.stem


def load_run_windows(
    paths: list[Path], seq_len: int, stride: int
) -> tuple[np.ndarray, list[str], list[str], list[int]]:
    """Read continuous feature tables with matching schemas, cut windows separately within each
    run, and concatenate them with one source run ID per window.
    """
    matrices = []
    run_ids: list[str] = []
    n_tokens: list[int] = []
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
        windows = window_sequences(matrix, seq_len, stride)
        if windows.shape[0] == 0:
            print(
                f"warning: {path} has {matrix.shape[0]} tokens "
                f"(need {seq_len}); skipping",
                file=sys.stderr,
            )
            continue
        matrices.append(windows)
        prefix = prefix_from_features_path(path)
        run_ids.extend([prefix] * windows.shape[0])
        n_tokens.append(matrix.shape[0])
    if not matrices or feature_names is None:
        raise SystemExit("no continuous feature rows loaded")
    return np.concatenate(matrices, axis=0), feature_names, run_ids, n_tokens


def fit_boundaries_on_train(
    train_cont: np.ndarray, feature_names: list[str], num_buckets: int
) -> dict[str, list[float]]:
    """Flatten each training feature across windows/tokens and fit quantile edges; keep base_rtt
    continuous, place congestion-state edges between observed states, and reserve
    a zero class before positive-only quantiles for TCP loss features.
    """
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
        boundaries[name] = feature_quantile_boundaries(name, vals, num_buckets)
    return boundaries


def tokenize_multi(
    continuous: np.ndarray, feature_names: list[str], boundaries: dict[str, list[float]]
) -> tuple[np.ndarray, list[str]]:
    """Keep base_rtt continuous and map all other feature planes to right-sided bucket IDs using
    shared training edges, preserving missing observations as NaN.
    """
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
        cols.append(buckets.reshape(continuous.shape[0], continuous.shape[1]))
    return np.stack(cols, axis=-1), out_names


def load_run_metadata(meta_path: Path | None, manifest_path: Path | None) -> dict:
    """Index manifest config/RNG/knob metadata by run prefix, then overlay executed outcome
    records when available.
    """
    by_prefix: dict[str, dict] = {}
    if manifest_path and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for rec in manifest.get("runs", []):
            prefix = rec["prefix"]
            by_prefix.setdefault(prefix, {}).update(
                {
                    "prefix": prefix,
                    "config": rec.get("config"),
                    "rngRun": rec.get("rngRun"),
                    "knobs": rec.get("knobs", {}),
                }
            )
    if meta_path and meta_path.exists():
        outcomes = json.loads(meta_path.read_text(encoding="utf-8"))
        for rec in outcomes.get("runs", []):
            prefix = rec["prefix"]
            by_prefix.setdefault(prefix, {}).update(rec)
    return by_prefix


def holdout_run_ids(
    unique_runs: list[str],
    run_meta: dict[str, dict],
    holdout_knobs: list[str],
    holdout_frac: float,
) -> set[str]:
    """Rank runs by each requested knob and union the highest holdout fraction into a forced test
    set; disable this behavior when the fraction is nonpositive.
    """
    forced: set[str] = set()
    if holdout_frac <= 0.0 or not holdout_knobs:
        return forced
    n_force = max(1, int(round(len(unique_runs) * holdout_frac)))
    for knob in holdout_knobs:
        scored = []
        for prefix in unique_runs:
            knobs = run_meta.get(prefix, {}).get("knobs", {})
            if knob not in knobs:
                continue
            scored.append((float(knobs[knob]), prefix))
        scored.sort(reverse=True)
        for _, prefix in scored[:n_force]:
            forced.add(prefix)
    return forced


def split_by_run(
    sequences: np.ndarray,
    run_ids: list[str],
    train_frac: float,
    seed: int,
    forced_test: set[str],
) -> tuple[np.ndarray, np.ndarray, list[str], list[str], list[str], list[str]]:
    """Shuffle eligible run IDs with the supplied seed, combine them with forced test runs,
    ensure both splits are nonempty, and select windows by whole-run membership.
    """
    unique = sorted(set(run_ids))
    eligible = [r for r in unique if r not in forced_test]
    rng = np.random.default_rng(seed)
    order = list(eligible)
    rng.shuffle(order)

    n_test_target = max(1, int(round(len(unique) * (1.0 - train_frac))))
    test_runs = set(forced_test)
    # Fill remaining test slots from the shuffled eligible pool.
    for prefix in order:
        if len(test_runs) >= n_test_target:
            break
        test_runs.add(prefix)
    # Ensure train is non-empty when possible.
    if len(test_runs) >= len(unique) and len(unique) >= 2:
        # Keep at least one train run: drop the last forced-or-picked from test
        # if every run would otherwise be test.
        spill = [r for r in unique if r in test_runs and r not in forced_test]
        if spill:
            test_runs.remove(spill[-1])
        elif forced_test:
            test_runs.remove(next(iter(forced_test)))

    train_runs = [r for r in unique if r not in test_runs]
    test_runs_list = sorted(test_runs)
    if not train_runs or not test_runs_list:
        raise SystemExit(
            f"run-level split failed: train_runs={len(train_runs)} "
            f"test_runs={len(test_runs_list)} (need ≥1 each; got {len(unique)} runs)"
        )

    train_run_set = set(train_runs)
    train_mask = np.array([r in train_run_set for r in run_ids], dtype=bool)
    test_mask = ~train_mask
    train_ids = [r for r, m in zip(run_ids, train_mask) if m]
    test_ids = [r for r, m in zip(run_ids, test_mask) if m]
    return (
        sequences[train_mask],
        sequences[test_mask],
        train_ids,
        test_ids,
        train_runs,
        test_runs_list,
    )


def save_split(
    stem: Path,
    sequences: np.ndarray,
    context: int,
    feature_names: list[str],
    source_feature_names: list[str],
    run_ids: list[str],
    truncated: list[bool],
    meta_extra: dict,
) -> None:
    """Slice sequences into history and future arrays, then write compressed NumPy, pickle, and
    JSON artifacts with run IDs and split metadata.
    """
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
        run_ids=np.asarray(run_ids),
        truncated=np.asarray(truncated, dtype=bool),
    )
    meta = {
        "n_sequences": int(sequences.shape[0]),
        "n_features": len(feature_names),
        "feature_names": feature_names,
        "source_feature_names": source_feature_names,
        "run_ids": run_ids,
        "truncated": truncated,
        "n_truncated": int(sum(truncated)),
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
                "run_ids": run_ids,
                "truncated": truncated,
                "meta": meta,
            },
            handle,
        )


def main() -> int:
    """Load continuous windows, split complete runs, fit quantile edges on training data,
    tokenize both splits, and save their arrays and shared boundaries.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features",
        action="append",
        default=[],
        help="continuous *-features.csv (repeatable; one file per run)",
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
        help="fraction of *runs* for train (default 0.8)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--num-buckets",
        type=int,
        default=50,
        help="quantile buckets target (UNUM default 50; edges may collapse)",
    )
    parser.add_argument(
        "--manifest",
        default="",
        help="dataset manifest.json (knobs / rngRun / config paths)",
    )
    parser.add_argument(
        "--run-outcomes",
        default="",
        help="run-outcomes.json from collect-dataset (exit codes, truncated)",
    )
    parser.add_argument(
        "--holdout-knob",
        action="append",
        default=[],
        help="knob whose highest values are forced into test "
        "(repeatable; default: dataRateMbps, computeDelayUs)",
    )
    parser.add_argument(
        "--holdout-frac",
        type=float,
        default=0.15,
        help="fraction of runs forced into test per --holdout-knob (0 disables)",
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

    cont_seq, feature_names, run_ids, n_tokens = load_run_windows(paths, seq_len, stride)
    if cont_seq.shape[0] == 0:
        raise SystemExit(
            f"need at least {seq_len} RTT tokens in some run "
            f"(files={len(paths)})"
        )

    out_dir = Path(args.out_dir) if args.out_dir else paths[0].parent
    manifest_path = Path(args.manifest) if args.manifest else out_dir / "manifest.json"
    outcomes_path = (
        Path(args.run_outcomes) if args.run_outcomes else out_dir / "run-outcomes.json"
    )
    run_meta = load_run_metadata(outcomes_path, manifest_path)

    holdout_knobs = args.holdout_knob or ["dataRateMbps", "computeDelayUs"]
    unique_runs = sorted(set(run_ids))
    forced_test = holdout_run_ids(
        unique_runs, run_meta, holdout_knobs, args.holdout_frac
    )

    train_cont, test_cont, train_ids, test_ids, train_runs, test_runs = split_by_run(
        cont_seq, run_ids, args.train_frac, args.seed, forced_test
    )

    truncated_by_run = {}
    for p in unique_runs:
        meta = run_meta.get(p, {})
        if "truncated" in meta:
            truncated_by_run[p] = bool(meta["truncated"])
        else:
            code = meta.get("exitCode", 0)
            truncated_by_run[p] = code not in (0, 1, None)
    train_trunc = [truncated_by_run.get(r, False) for r in train_ids]
    test_trunc = [truncated_by_run.get(r, False) for r in test_ids]

    boundaries = fit_boundaries_on_train(train_cont, feature_names, args.num_buckets)
    train_tok, col_names = tokenize_multi(train_cont, feature_names, boundaries)
    test_tok, _ = tokenize_multi(test_cont, feature_names, boundaries)

    prefix = args.prefix
    shared_meta = {
        "sources": [str(p) for p in paths],
        "mode": "multi",
        "split_unit": "run",
        "context": context,
        "prediction": prediction,
        "seq_len": seq_len,
        "stride": stride,
        "train_frac": args.train_frac,
        "seed": args.seed,
        "num_buckets": args.num_buckets,
        "n_rtt_tokens": int(sum(n_tokens)),
        "n_sequences_total": int(cont_seq.shape[0]),
        "n_runs_total": len(unique_runs),
        "train_runs": train_runs,
        "test_runs": test_runs,
        "holdout_knobs": holdout_knobs,
        "holdout_frac": args.holdout_frac,
        "forced_test_runs": sorted(forced_test),
        "layout": (
            f"UNUM-style: window {seq_len} within run, split runs "
            f"{args.train_frac:.0%}/{1 - args.train_frac:.0%}, "
            f"fit Quantile-{args.num_buckets} on train, "
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
        train_ids,
        train_trunc,
        {**shared_meta, "split": "train"},
    )
    save_split(
        out_dir / f"{prefix}-test",
        test_tok,
        context,
        col_names,
        feature_names,
        test_ids,
        test_trunc,
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
                "zero_class": has_zero_loss_class(name),
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

    # Overlap check: no run on both sides.
    overlap = set(train_runs) & set(test_runs)
    if overlap:
        raise SystemExit(f"run leakage across split: {sorted(overlap)}")

    print(
        f"rtt_tokens={sum(n_tokens)} sequences={cont_seq.shape[0]} "
        f"runs={len(unique_runs)} train_runs={len(train_runs)} "
        f"test_runs={len(test_runs)} "
        f"train={train_tok.shape[0]} test={test_tok.shape[0]} "
        f"truncated_train={sum(train_trunc)} truncated_test={sum(test_trunc)} "
        f"forced_test={len(forced_test)} "
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
