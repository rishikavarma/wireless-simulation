#!/usr/bin/env python3
"""UNUM-style 10+10 sequence windowing over RTT tokens.

Reads a bucketized (or continuous features) CSV of RTT tokens and emits
overlapping or non-overlapping windows of length ``context + prediction``
(default 10 + 10 = 20), matching UNUM's next-token training layout:

  sequence[:, :context]   — history (encoder input)
  sequence[:, context:]   — subsequent tokens to predict

Default vector layout is UNUM multi-head style for **all** collected features:
``base_rtt`` stays continuous; every other feature contributes its bucket index.
"""

from __future__ import annotations

import argparse
import csv
import json
import pickle
import sys
from pathlib import Path

import numpy as np

META_KEYS = {"token", "t_start_s", "t_end_s", "window_s", "n_samples"}


def read_commented_csv(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip() and not line.startswith("#")]
    reader = csv.DictReader(lines)
    rows = []
    for row in reader:
        parsed = {}
        for key, value in row.items():
            if key is None:
                continue
            text = (value or "").strip()
            if text == "":
                parsed[key] = None
            else:
                parsed[key] = float(text)
        rows.append(parsed)
    return list(reader.fieldnames or []), rows


def continuous_feature_names(fieldnames: list[str]) -> list[str]:
    names = []
    for name in fieldnames:
        if name in META_KEYS or name.endswith("_bucket"):
            continue
        names.append(name)
    # Prefer UNUM-ish order: base_rtt first when present.
    if "base_rtt" in names:
        names = ["base_rtt"] + [n for n in names if n != "base_rtt"]
    return names


def build_matrix(
    rows: list[dict], feature_names: list[str], mode: str
) -> tuple[np.ndarray, list[str]]:
    """Return (T, F) matrix and the per-column names used in the vectors."""
    if mode == "values":
        cols = list(feature_names)
        data = np.full((len(rows), len(cols)), np.nan, dtype=np.float64)
        for i, row in enumerate(rows):
            for j, name in enumerate(cols):
                val = row.get(name)
                if val is not None:
                    data[i, j] = float(val)
        return data, cols

    if mode == "buckets":
        cols = [f"{name}_bucket" for name in feature_names]
        data = np.full((len(rows), len(cols)), np.nan, dtype=np.float64)
        for i, row in enumerate(rows):
            for j, name in enumerate(feature_names):
                val = row.get(f"{name}_bucket")
                if val is None:
                    val = row.get(name)
                if val is not None:
                    data[i, j] = float(val)
        return data, cols

    # multi: base_rtt continuous, all other features as bucket indices (UNUM).
    cols: list[str] = []
    for name in feature_names:
        if name == "base_rtt":
            cols.append("base_rtt")
        else:
            cols.append(f"{name}_bucket")
    data = np.full((len(rows), len(cols)), np.nan, dtype=np.float64)
    for i, row in enumerate(rows):
        for j, name in enumerate(feature_names):
            if name == "base_rtt":
                val = row.get("base_rtt")
            else:
                val = row.get(f"{name}_bucket")
                if val is None:
                    val = row.get(name)
            if val is not None:
                data[i, j] = float(val)
    return data, cols


def window_sequences(
    matrix: np.ndarray, seq_len: int, stride: int
) -> np.ndarray:
    """Slide windows of length seq_len over time axis → (N, seq_len, F)."""
    t_len, n_feat = matrix.shape
    if t_len < seq_len:
        return np.zeros((0, seq_len, n_feat), dtype=matrix.dtype)
    starts = range(0, t_len - seq_len + 1, stride)
    return np.stack([matrix[s : s + seq_len] for s in starts], axis=0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucketized",
        default="",
        help="bucketized CSV from bucketize_metrics.py (preferred)",
    )
    parser.add_argument(
        "--features",
        default="",
        help="continuous features CSV (used if --bucketized omitted)",
    )
    parser.add_argument(
        "--out",
        default="",
        help="output stem (writes .npz and .json; default beside input)",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=10,
        help="history length (UNUM default 10)",
    )
    parser.add_argument(
        "--prediction",
        type=int,
        default=10,
        help="future length to predict (UNUM default 10)",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=0,
        help="window stride in tokens (0 = context length, non-overlapping 10+10)",
    )
    parser.add_argument(
        "--mode",
        choices=("multi", "buckets", "values"),
        default="multi",
        help="vector layout: multi=base_rtt float + other buckets (UNUM); "
        "buckets=all bucket ids; values=all continuous",
    )
    args = parser.parse_args()

    if not args.bucketized and not args.features:
        raise SystemExit("pass --bucketized and/or --features")

    src = Path(args.bucketized) if args.bucketized else Path(args.features)
    if args.bucketized and Path(args.bucketized).exists():
        src = Path(args.bucketized)
        mode = args.mode
    else:
        src = Path(args.features)
        mode = "values" if args.mode == "multi" else args.mode
        if args.mode == "multi":
            print(
                "note: no bucketized CSV; falling back to continuous --mode values",
                file=sys.stderr,
            )

    if not src.exists():
        raise SystemExit(f"input not found: {src}")

    fieldnames, rows = read_commented_csv(src)
    if not rows:
        raise SystemExit(f"no rows in {src}")

    feature_names = continuous_feature_names(fieldnames)
    if not feature_names:
        raise SystemExit(f"no feature columns in {src}")

    matrix, col_names = build_matrix(rows, feature_names, mode)
    context = args.context
    prediction = args.prediction
    seq_len = context + prediction
    stride = args.stride if args.stride > 0 else context
    if stride < 1:
        raise SystemExit("--stride must be >= 1 (or 0 for context default)")

    sequences = window_sequences(matrix, seq_len, stride)
    history = sequences[:, :context, :]
    future = sequences[:, context:, :]

    out_stem = Path(args.out) if args.out else src.with_name(
        src.name.replace("-bucketized.csv", "")
        .replace("-features.csv", "")
        .removesuffix(".csv")
        + f"-seq{seq_len}"
    )
    out_stem.parent.mkdir(parents=True, exist_ok=True)

    npz_path = out_stem.with_suffix(".npz")
    np.savez_compressed(
        npz_path,
        sequences=sequences,
        history=history,
        future=future,
        feature_names=np.asarray(col_names),
        source_feature_names=np.asarray(feature_names),
    )

    meta = {
        "source": str(src),
        "mode": mode,
        "context": context,
        "prediction": prediction,
        "seq_len": seq_len,
        "stride": stride,
        "n_rtt_tokens": len(rows),
        "n_sequences": int(sequences.shape[0]),
        "n_features": len(col_names),
        "feature_names": col_names,
        "source_feature_names": feature_names,
        "shapes": {
            "sequences": list(sequences.shape),
            "history": list(history.shape),
            "future": list(future.shape),
        },
        "layout": (
            "Each row is one training example. history = first "
            f"{context} RTT tokens; future = next {prediction} RTT tokens "
            "to predict (UNUM 10+10)."
        ),
    }
    json_path = out_stem.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
        handle.write("\n")

    pkl_path = out_stem.with_suffix(".pkl")
    with open(pkl_path, "wb") as handle:
        pickle.dump(
            {
                "sequences": sequences,
                "history": history,
                "future": future,
                "feature_names": col_names,
                "meta": meta,
            },
            handle,
        )

    print(
        f"rtt_tokens={len(rows)} sequences={sequences.shape[0]} "
        f"shape={tuple(sequences.shape)} "
        f"history={tuple(history.shape)} future={tuple(future.shape)} "
        f"mode={mode} stride={stride} "
        f"out_npz={npz_path} out_json={json_path} out_pkl={pkl_path}"
    )
    if sequences.shape[0] == 0:
        print(
            f"warning: need at least {seq_len} RTT tokens to form a window "
            f"(have {len(rows)})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
