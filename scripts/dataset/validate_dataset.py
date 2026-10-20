#!/usr/bin/env python3
"""Sanity-check a make_datasets.py train/test split for UNUM-style training.

Reports:
  * bucket collapse (unique bins vs Quantile-N target)
  * label entropy / majority-class mass
  * history→future predictability (Pearson of hist mean vs future mean)
  * lag-1 autocorrelation inside windows
  * example trajectory plots

Writes ``validation-report.json`` + PNGs under ``--out-dir``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover
    plt = None


FOCUS = [
    "base_rtt",
    "srtt_bucket",
    "thr_bucket",
    "rttvar_bucket",
    "lost_bucket",
    "pre_act_bucket",
    "sinr_dl_db_bucket",
    "sinr_ul_db_bucket",
]


def entropy(counts: np.ndarray) -> float:
    """Normalize positive histogram counts to probabilities and return their Shannon entropy in
    bits, or zero for an empty histogram.
    """
    total = counts.sum()
    if total <= 0:
        return 0.0
    p = counts[counts > 0] / total
    return float(-(p * np.log2(p)).sum())


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Center two arrays and return their normalized dot product; return NaN for fewer than two
    values or zero variance.
    """
    if a.size < 2:
        return float("nan")
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    a = a - a.mean()
    b = b - b.mean()
    denom = math.sqrt(float((a * a).sum() * (b * b).sum()))
    if denom < 1e-12:
        return float("nan")
    return float((a * b).sum() / denom)


def lag1_autocorr(windows: np.ndarray) -> float:
    """Compute Pearson correlation between adjacent token values in each nonconstant window, then
    average the defined per-window correlations.
    """
    if windows.shape[1] < 2:
        return float("nan")
    rhos = []
    for i in range(windows.shape[0]):
        x = windows[i]
        if np.std(x) < 1e-12:
            continue
        rhos.append(pearson(x[:-1], x[1:]))
    rhos = [r for r in rhos if not math.isnan(r)]
    return float(np.mean(rhos)) if rhos else float("nan")


def analyze_feature(
    name: str, plane: np.ndarray, context: int, n_buckets_target: int | None
) -> dict:
    """Measure observed range, diversity, history/future correlation, and lag-one correlation;
    flag bucket heads with few classes or a dominant class.
    """
    flat = plane.reshape(-1)
    flat = flat[~np.isnan(flat)]
    uniq = np.unique(flat)
    hist_mean = plane[:, :context].mean(axis=1)
    fut_mean = plane[:, context:].mean(axis=1)
    pred_r = pearson(hist_mean, fut_mean)
    ac1 = lag1_autocorr(plane)

    is_bucket = name.endswith("_bucket") or name == "ca_state"
    report: dict = {
        "name": name,
        "n_values": int(flat.size),
        "n_unique": int(uniq.size),
        "min": float(flat.min()) if flat.size else None,
        "max": float(flat.max()) if flat.size else None,
        "std": float(flat.std()) if flat.size else None,
        "hist_future_corr": pred_r,
        "lag1_autocorr": ac1,
        "collapsed": False,
    }
    if is_bucket and flat.size:
        # Integer bucket occupancy.
        lo, hi = int(flat.min()), int(flat.max())
        counts = np.bincount(flat.astype(int) - lo)
        ent = entropy(counts.astype(np.float64))
        majority = float(counts.max() / counts.sum())
        target = n_buckets_target or max(hi - lo + 1, 1)
        report.update(
            {
                "n_buckets_used": int(uniq.size),
                "n_buckets_target": int(target),
                "entropy_bits": ent,
                "majority_frac": majority,
                "collapsed": uniq.size <= max(3, target // 10) or majority >= 0.85,
            }
        )
    return report


def load_npz(path: Path) -> tuple[np.ndarray, list[str], int]:
    """Load sequence arrays and feature names from a NumPy split, reading the history length from
    its JSON sidecar when present.
    """
    data = np.load(path, allow_pickle=True)
    names = [str(x) for x in data["feature_names"]]
    meta_path = path.with_suffix(".json")
    context = 10
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        context = int(meta.get("context", 10))
    return data["sequences"], names, context


def bucket_targets(bounds_path: Path | None) -> dict[str, int]:
    """Read realized class counts from the boundaries JSON and expose them under both continuous
    and bucket-suffixed feature names.
    """
    if not bounds_path or not bounds_path.exists():
        return {}
    payload = json.loads(bounds_path.read_text(encoding="utf-8"))
    out = {}
    for name, info in payload.get("features", {}).items():
        out[name] = int(info.get("n_buckets", 0))
        out[f"{name}_bucket"] = int(info.get("n_buckets", 0))
    return out


def plot_trajectories(
    sequences: np.ndarray,
    names: list[str],
    context: int,
    out_path: Path,
    n_examples: int = 8,
) -> None:
    """Plot a seeded sample of focus-feature windows with a vertical marker separating history
    from future tokens.
    """
    if plt is None:
        return
    want = [n for n in FOCUS if n in names]
    if not want:
        return
    rng = np.random.default_rng(0)
    idxs = rng.choice(sequences.shape[0], size=min(n_examples, sequences.shape[0]), replace=False)
    cols = min(4, len(want))
    rows = int(math.ceil(len(want) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(3.2 * cols, 2.4 * rows), squeeze=False)
    t = np.arange(sequences.shape[1])
    for ax, name in zip(axes.ravel(), want):
        j = names.index(name)
        for i in idxs:
            ax.plot(t, sequences[i, :, j], color="C0", alpha=0.35, lw=1)
        ax.axvline(context - 0.5, color="0.4", ls="--", lw=0.8)
        ax.set_title(name, fontsize=9)
        ax.set_xlabel("token")
        ax.grid(True, alpha=0.25)
    for ax in axes.ravel()[len(want) :]:
        ax.axis("off")
    fig.suptitle(
        f"Sample windows (n={len(idxs)}); dashed = history|future split",
        fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_predictability_scatter(
    sequences: np.ndarray, names: list[str], context: int, out_path: Path
) -> None:
    """Plot each focus feature history mean against its future mean, subsampling large sets and
    showing the persistence diagonal.
    """
    if plt is None:
        return
    want = [n for n in FOCUS if n in names][:6]
    if not want:
        return
    cols = 3
    rows = int(math.ceil(len(want) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(3.4 * cols, 3.0 * rows), squeeze=False)
    for ax, name in zip(axes.ravel(), want):
        j = names.index(name)
        plane = sequences[:, :, j]
        x = plane[:, :context].mean(axis=1)
        y = plane[:, context:].mean(axis=1)
        # Subsample for plot speed
        if x.size > 3000:
            pick = np.linspace(0, x.size - 1, 3000).astype(int)
            x, y = x[pick], y[pick]
        ax.scatter(x, y, s=4, alpha=0.25, c="C0", edgecolors="none")
        lo = float(min(x.min(), y.min()))
        hi = float(max(x.max(), y.max()))
        ax.plot([lo, hi], [lo, hi], "k--", lw=0.8, alpha=0.6)
        r = pearson(x, y)
        ax.set_title(f"{name}\nr={r:.3f}", fontsize=9)
        ax.set_xlabel("hist mean")
        ax.set_ylabel("future mean")
        ax.grid(True, alpha=0.25)
    for ax in axes.ravel()[len(want) :]:
        ax.axis("off")
    fig.suptitle("History mean → future mean (diagonal = perfect persistence)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_bucket_occupancy(
    sequences: np.ndarray, names: list[str], out_path: Path
) -> None:
    """Histogram finite observed bucket IDs for focus heads without replacing missing samples."""
    if plt is None:
        return
    want = [n for n in FOCUS if n.endswith("_bucket") and n in names]
    if not want:
        return
    cols = 2
    rows = int(math.ceil(len(want) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.0 * cols, 2.6 * rows), squeeze=False)
    for ax, name in zip(axes.ravel(), want):
        j = names.index(name)
        values = sequences[:, :, j].reshape(-1)
        # Sparse PHY reports and relay interruptions legitimately leave missing
        # SINR tokens. Plot observed buckets without imputing collected metrics.
        flat = values[np.isfinite(values)].astype(int)
        if not flat.size:
            ax.set_title(f"{name} (no observed buckets)", fontsize=9)
            continue
        lo, hi = int(flat.min()), int(flat.max())
        counts = np.bincount(flat - lo)
        ax.bar(np.arange(lo, lo + len(counts)), counts, color="C0", width=0.85)
        ax.set_title(f"{name} (uniq={len(np.unique(flat))})", fontsize=9)
        ax.set_xlabel("bucket id")
        ax.set_ylabel("count")
        ax.grid(True, axis="y", alpha=0.25)
    for ax in axes.ravel()[len(want) :]:
        ax.axis("off")
    fig.suptitle("Bucket occupancy (collapsed = one dominant bar)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_corr_bars(features: list[dict], out_path: Path) -> None:
    """Compare focus-feature history/future and lag-one correlations as horizontal bars,
    displaying undefined correlations as zero.
    """
    if plt is None:
        return
    focus = [f for f in features if f["name"] in FOCUS]
    if not focus:
        focus = features[:12]
    names = [f["name"] for f in focus]
    corr = [f["hist_future_corr"] if f["hist_future_corr"] == f["hist_future_corr"] else 0.0 for f in focus]
    ac = [f["lag1_autocorr"] if f["lag1_autocorr"] == f["lag1_autocorr"] else 0.0 for f in focus]
    y = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(7.5, max(3.0, 0.35 * len(names))))
    ax.barh(y - 0.18, corr, height=0.35, label="hist→future corr", color="C0")
    ax.barh(y + 0.18, ac, height=0.35, label="lag-1 autocorr", color="C1")
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlabel("correlation")
    ax.set_xlim(-0.05, 1.05)
    ax.axvline(0.5, color="0.5", ls=":", lw=0.8)
    ax.legend(loc="lower right", fontsize=8)
    ax.set_title("Predictability proxies (higher = more temporally structured)")
    ax.grid(True, axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def main() -> int:
    """Load the generated train/test splits, assess training-feature diversity and temporal
    structure, and write JSON summaries and diagnostic plots.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("results/dataset"),
        help="directory with dataset-train.npz / dataset-test.npz",
    )
    parser.add_argument("--prefix", default="dataset")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="default: <data-dir>/validation",
    )
    parser.add_argument("--context", type=int, default=0, help="0 = read from meta")
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    out_dir = (args.out_dir or (data_dir / "validation")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    train_path = data_dir / f"{args.prefix}-train.npz"
    test_path = data_dir / f"{args.prefix}-test.npz"
    if not train_path.exists():
        raise SystemExit(f"missing {train_path}")

    train_seq, names, context = load_npz(train_path)
    if args.context > 0:
        context = args.context
    test_seq = None
    if test_path.exists():
        test_seq, _, _ = load_npz(test_path)

    bounds = None
    for cand in sorted(data_dir.glob(f"{args.prefix}-boundaries-quantile*.json")):
        bounds = cand
        break
    targets = bucket_targets(bounds)

    features = []
    for j, name in enumerate(names):
        src = name.removesuffix("_bucket") if name.endswith("_bucket") else name
        features.append(
            analyze_feature(
                name,
                train_seq[:, :, j],
                context,
                targets.get(name) or targets.get(src),
            )
        )

    collapsed = [f for f in features if f.get("collapsed")]
    predictable = sorted(
        features,
        key=lambda f: (
            -(f["hist_future_corr"] if f["hist_future_corr"] == f["hist_future_corr"] else -1),
            -(f["lag1_autocorr"] if f["lag1_autocorr"] == f["lag1_autocorr"] else -1),
        ),
    )

    report = {
        "data_dir": str(data_dir),
        "train_shape": list(train_seq.shape),
        "test_shape": list(test_seq.shape) if test_seq is not None else None,
        "context": context,
        "n_features": len(names),
        "n_collapsed": len(collapsed),
        "collapsed_features": [f["name"] for f in collapsed],
        "focus": [f for f in features if f["name"] in FOCUS],
        "top_predictable": [
            {
                "name": f["name"],
                "hist_future_corr": f["hist_future_corr"],
                "lag1_autocorr": f["lag1_autocorr"],
                "n_unique": f["n_unique"],
            }
            for f in predictable[:15]
        ],
        "least_predictable": [
            {
                "name": f["name"],
                "hist_future_corr": f["hist_future_corr"],
                "lag1_autocorr": f["lag1_autocorr"],
                "n_unique": f["n_unique"],
            }
            for f in predictable[-10:]
        ],
        "features": features,
        "verdict": {
            "enough_windows": train_seq.shape[0] >= 500,
            "collapse_rate": len(collapsed) / max(1, len(features)),
            "mean_hist_future_corr_focus": float(
                np.nanmean(
                    [
                        f["hist_future_corr"]
                        for f in features
                        if f["name"] in FOCUS
                    ]
                )
            ),
            "notes": [],
        },
    }
    notes = report["verdict"]["notes"]
    if report["verdict"]["collapse_rate"] > 0.25:
        notes.append(
            "Many features are collapsed (≤3 effective bins or ≥85% majority). "
            "Longer sims / wider traffic & link-margin ranges help."
        )
    mean_r = report["verdict"]["mean_hist_future_corr_focus"]
    if mean_r == mean_r and mean_r >= 0.5:
        notes.append(
            f"Focus features look temporally structured (mean hist→future r={mean_r:.2f})."
        )
    elif mean_r == mean_r:
        notes.append(
            f"Focus features show weak persistence (mean hist→future r={mean_r:.2f}); "
            "prediction may be hard or labels too noisy."
        )

    report_path = out_dir / "validation-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    plot_trajectories(train_seq, names, context, out_dir / "sample-trajectories.png")
    plot_predictability_scatter(
        train_seq, names, context, out_dir / "hist-vs-future.png"
    )
    plot_bucket_occupancy(train_seq, names, out_dir / "bucket-occupancy.png")
    plot_corr_bars(features, out_dir / "predictability-bars.png")

    # Compact canvas-friendly summary
    summary = {
        "train_windows": int(train_seq.shape[0]),
        "test_windows": int(test_seq.shape[0]) if test_seq is not None else 0,
        "n_features": len(names),
        "n_collapsed": len(collapsed),
        "collapse_rate": report["verdict"]["collapse_rate"],
        "mean_hist_future_corr_focus": mean_r,
        "focus": report["focus"],
        "top_predictable": report["top_predictable"][:8],
        "notes": notes,
    }
    (out_dir / "validation-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )

    print(
        f"windows_train={train_seq.shape[0]} features={len(names)} "
        f"collapsed={len(collapsed)}/{len(features)} "
        f"focus_hist_future_r={mean_r:.3f}"
    )
    print("collapsed:", ", ".join(f["name"] for f in collapsed[:20]) or "(none)")
    print(f"report: {report_path}")
    print(f"plots:  {out_dir}/*.png")
    for note in notes:
        print(f"note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
