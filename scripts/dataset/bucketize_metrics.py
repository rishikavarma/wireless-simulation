#!/usr/bin/env python3
"""UNUM-style quantile bucketization for collected sat-bs-handset metrics.

Pipeline:
  1. Fold 5 ms Sage TCP rows into RTT tokens (fold_tokens.py).
  2. Carry measured minrtt as the continuous UNUM base_rtt input.
  3. Fit Quantile-N edges on every other metric column (including SINR).
  4. Emit continuous feature CSV + multi-head bucketized CSV + boundaries.

Meta columns (token timing) and base_rtt stay continuous. Other feature
columns get a ``*_bucket`` index via np.searchsorted(..., side='right'),
matching UNUM's offline ``bucket_tokenize.py`` (training path).
TCP loss features reserve class zero for zero loss; their remaining classes
use quantiles of positive observations only.
"""

from __future__ import annotations

import argparse
import csv
import json
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fold_tokens import fold, load_config, read_csv, write_tokens  # noqa: E402
from radio_tokens import attach_radio_tokens  # noqa: E402


META_KEYS = ("token", "t_start_s", "t_end_s", "window_s", "n_samples")


def is_bucket_feature(name: str) -> bool:
    """UNUM keeps measured minimum RTT continuous under the base_rtt name."""
    return name != "base_rtt"


def quantile_boundaries(arr: np.ndarray, num_buckets: int) -> list[float]:
    """UNUM ``get_quantile_boundaries``: interior percentiles, unique sorted.

    Duplicate percentiles collapse (fewer than ``num_buckets`` heads). Constant
    columns yield no edges (single bucket). No equal-width fallback.
    """
    if arr.size == 0 or num_buckets < 2:
        return []
    percentiles = np.linspace(0, 100, num_buckets + 1)[1:-1]
    edges = np.percentile(arr, percentiles)
    return sorted({float(x) for x in edges})


def bucketize_value(value: float | None, edges: list[float]) -> int | None:
    """Map an observed value to its right-sided quantile interval with searchsorted; preserve
    missing values and map constant heads to bucket zero.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if not edges:
        return 0
    return int(np.searchsorted(edges, float(value), side="right"))


def has_zero_loss_class(name: str) -> bool:
    """Identify nonnegative TCP loss features that reserve bucket zero for exact zero loss."""
    return name in ("lost", "loss_db") or name.startswith("lost_")


def feature_quantile_boundaries(
    name: str, values: np.ndarray, num_buckets: int
) -> list[float]:
    """Fit ordinary quantiles, or reserve zero plus at most N-1 positive-loss buckets.

    Loss quantiles use positive observations only. The smallest positive float
    separates zero from every representable positive value with the existing
    right-sided lookup, so saved edge-only JSON/pickle loaders remain compatible.
    Reserve the separation even for an all-zero training feature, allowing unseen
    positive test loss to remain distinct. Missing values are excluded from fitting.
    """
    if not has_zero_loss_class(name):
        return quantile_boundaries(values, num_buckets)
    if num_buckets < 2:
        raise ValueError("loss bucketization requires at least two buckets")
    observed = values[np.isfinite(values)]
    if np.any(observed < 0):
        raise ValueError(f"{name}: loss observations must be nonnegative")
    positive = observed[observed > 0]
    edges = quantile_boundaries(positive, num_buckets - 1)
    if positive.size:
        # Ties at the minimum/maximum do not provide interior severity splits.
        lo, hi = positive.min(), positive.max()
        edges = [edge for edge in edges if lo < edge < hi]
    return [float(np.nextafter(0.0, 1.0)), *edges]


def sinr_series(path: Path | None) -> np.ndarray:
    """Read the dedicated PHY CSV, collect observed SINR values into a NumPy array, and return an
    empty array for a missing trace.
    """
    if path is None or not path.exists():
        return np.asarray([], dtype=float)
    _, rows = read_csv(path)
    vals = []
    for row in rows:
        raw = row.get("sinr_db")
        if raw is None or raw == "":
            continue
        try:
            vals.append(float(raw))
        except (TypeError, ValueError):
            continue
    return np.asarray(vals, dtype=float)


def feature_keys_from_tokens(tokens: list[dict]) -> list[str]:
    """Select metric columns from the first token, omit timing metadata, and order derived
    base_rtt first and downlink/uplink SINR last.
    """
    if not tokens:
        return []
    keys = []
    for key in tokens[0]:
        if key in META_KEYS:
            continue
        keys.append(key)
    # Prefer a stable, documented order: derived base_rtt first, then Sage, SINR last.
    preferred = []
    if "base_rtt" in keys:
        preferred.append("base_rtt")
    for key in keys:
        if key in ("base_rtt", "sinr_dl_db", "sinr_ul_db"):
            continue
        preferred.append(key)
    for key in ("sinr_dl_db", "sinr_ul_db"):
        if key in keys:
            preferred.append(key)
    return preferred


def enrich_token(token: dict) -> dict:
    """Copy token and expose its measured minimum RTT as UNUM base_rtt.

    Keep the observed minimum RTT used to size the token when available. Older
    TCP traces have no explicit minimum and require the ratio-based fallback.
    """
    row = dict(token)
    if row.get("base_rtt") is not None:
        return row
    if "minrtt" in row:
        row["base_rtt"] = row["minrtt"]
        return row
    srtt = row.get("srtt")
    rtt_rate = row.get("rtt_rate")
    if srtt is not None and rtt_rate is not None:
        row["base_rtt"] = float(rtt_rate) * float(srtt)
    else:
        row["base_rtt"] = None
    return row


def build_tokens(tcp_path: Path, sinr_dl: Path | None, sinr_ul: Path | None, cfg: dict):
    """Read TCP and optional PHY traces, fold the TCP rows into time windows, and return both
    original and base-RTT-enriched tokens.
    """
    fields, tcp_rows = read_csv(tcp_path)
    sage_keys = [k for k in fields if k != "time_s"]
    for row in tcp_rows:
        # A socket without any RTT observation has no zero-millisecond RTT.
        srtt = row.get('srtt')
        if srtt is None or not np.isfinite(srtt) or srtt <= 0:
            for key in sage_keys:
                if key == 'srtt' or key.startswith('rtt'):
                    row[key] = None
    _, dl = read_csv(sinr_dl) if sinr_dl and sinr_dl.exists() else ([], [])
    _, ul = read_csv(sinr_ul) if sinr_ul and sinr_ul.exists() else ([], [])
    tokens = fold(tcp_rows, sage_keys, dl, ul, cfg)
    prefix = tcp_path.name.removesuffix('-tcp.csv')
    kpi_path = tcp_path.parent / f'{prefix}-kpis.csv'
    if cfg.get('metrics', {}).get('physical') is True and not kpi_path.exists():
        raise FileNotFoundError(f'physical telemetry is enabled but {kpi_path} is missing')
    if kpi_path.exists():
        attach_radio_tokens(tokens, tcp_path.parent, prefix)
    enriched = [enrich_token(token) for token in tokens]
    return tokens, enriched


def fit_boundaries(
    rows: list[dict],
    feature_keys: list[str],
    num_buckets: int,
    *,
    sinr_dl: Path | None = None,
    sinr_ul: Path | None = None,
) -> dict[str, list[float]]:
    """Fit one Quantile-N head per bucket feature, with a dedicated zero loss class.

    ``sinr_dl_db`` and ``sinr_ul_db`` are always separate heads. Their edges
    are fit from the dedicated PHY CSVs when present (more samples than the
    hold-last values on RTT tokens); otherwise from the token columns.
    """
    boundaries: dict[str, list[float]] = {}
    sinr_override = {
        "sinr_dl_db": sinr_series(sinr_dl),
        "sinr_ul_db": sinr_series(sinr_ul),
    }
    for name in feature_keys:
        if not is_bucket_feature(name):
            continue
        if name in sinr_override and sinr_override[name].size:
            values = sinr_override[name]
        else:
            values = np.asarray(
                [row[name] for row in rows if row.get(name) is not None], dtype=float
            )
            values = values[~np.isnan(values)]
        if name == "ca_state":
            # Already a small integer enum; keep identity buckets.
            uniques = sorted({float(v) for v in values})
            # Edges between consecutive states so searchsorted maps to the enum.
            boundaries[name] = [
                (uniques[i] + uniques[i + 1]) / 2.0 for i in range(len(uniques) - 1)
            ]
            continue
        boundaries[name] = feature_quantile_boundaries(name, values, num_buckets)
    return boundaries


def apply_boundaries(
    rows: list[dict], feature_keys: list[str], boundaries: dict[str, list[float]]
) -> list[dict]:
    """Copy continuous features and append bucket IDs for all but base_rtt.
    """
    out = []
    for row in rows:
        item = {key: row.get(key) for key in META_KEYS}
        for name in feature_keys:
            item[name] = row.get(name)
            if is_bucket_feature(name):
                item[f"{name}_bucket"] = bucketize_value(row.get(name), boundaries.get(name, []))
        out.append(item)
    return out


def write_csv(path: Path, rows: list[dict], fieldnames: list[str], comment: str) -> None:
    """Write rows in a specified column order with a leading comment; serialize missing
    observations as empty CSV cells.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(comment if comment.endswith("\n") else comment + "\n")
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {key: "" if row.get(key) is None else row.get(key) for key in fieldnames}
            )


def write_boundaries(
    path: Path, feature_keys: list[str], boundaries: dict[str, list[float]], num_buckets: int
) -> None:
    """Save named and indexed quantile edges as JSON and pickle, including each feature index and
    realized bucket count.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "method": "quantile",
        "num_buckets": num_buckets,
        "features": {
            name: {
                "index": i,
                "edges": boundaries.get(name, []),
                "n_edges": len(boundaries.get(name, [])),
                "n_buckets": len(boundaries.get(name, [])) + 1,
                "zero_class": has_zero_loss_class(name)
                and boundaries.get(name, [])[:1] == [float(np.nextafter(0.0, 1.0))],
            }
            for i, name in enumerate(feature_keys) if is_bucket_feature(name)
        },
    }
    with open(path.with_suffix(".json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    # Preserve positions in the full feature vector; base_rtt has no edges.
    pickle_payload = {
        "by_name": {
            name: boundaries.get(name, [])
            for name in feature_keys if is_bucket_feature(name)
        },
        "by_index": {
            i: boundaries.get(name, [])
            for i, name in enumerate(feature_keys) if is_bucket_feature(name)
        },
        "feature_names": list(feature_keys),
    }
    with open(path.with_suffix(".pkl"), "wb") as handle:
        pickle.dump(pickle_payload, handle)


def load_boundaries(path: Path, feature_keys: list[str]) -> dict[str, list[float]]:
    """Load named JSON/pickle edges or legacy index-keyed pickle edges; give missing feature
    names an empty edge list.
    """
    if path.suffix == ".json":
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        features = payload.get("features", {})
        out = {}
        for name in feature_keys:
            if not is_bucket_feature(name):
                continue
            if name in features:
                out[name] = list(features[name]["edges"])
            else:
                out[name] = []
        return out
    with open(path, "rb") as handle:
        raw = pickle.load(handle)
    if isinstance(raw, dict) and "by_name" in raw:
        return {name: list(raw["by_name"].get(name, [])) for name in feature_keys if is_bucket_feature(name)}
    # Legacy UNUM-6 pickle {idx: edges} — map by position if names unavailable.
    if isinstance(raw, dict) and raw and all(isinstance(k, int) for k in raw):
        return {
            name: list(raw.get(i, [])) for i, name in enumerate(feature_keys)
            if is_bucket_feature(name)
        }
    return {name: list(raw.get(name, [])) for name in feature_keys if is_bucket_feature(name)}


def main() -> int:
    """Fold configured traces, load or fit feature quantiles, and write continuous features,
    diagnostic buckets, boundaries, and the UNUM-6 view.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="scratch/sat-bs-handset/config/config.json",
        help="config.json (output.token* keys used for RTT folding)",
    )
    parser.add_argument("--tcp", required=True, help="5 ms TCP snapshot CSV")
    parser.add_argument("--sinr-dl", default="", help="optional downlink SINR CSV")
    parser.add_argument("--sinr-ul", default="", help="optional uplink SINR CSV")
    parser.add_argument("--out-dir", default="", help="output directory (default: beside --tcp)")
    parser.add_argument(
        "--prefix",
        default="",
        help="output file-name prefix (default: stem of --tcp without -tcp)",
    )
    parser.add_argument(
        "--num-buckets",
        type=int,
        default=50,
        help="quantile buckets per feature (UNUM default 50)",
    )
    parser.add_argument(
        "--boundaries",
        default="",
        help="reuse existing boundaries (.json or .pkl) instead of fitting",
    )
    parser.add_argument(
        "--fit-tcp",
        action="append",
        default=[],
        help="extra TCP CSVs used only to fit boundaries (repeatable)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    tcp_path = Path(args.tcp)
    out_dir = Path(args.out_dir) if args.out_dir else tcp_path.parent
    prefix = args.prefix or tcp_path.name.replace("-tcp.csv", "").removesuffix(".csv")
    sinr_dl = Path(args.sinr_dl) if args.sinr_dl else out_dir / f"{prefix}-sinr-dl.csv"
    sinr_ul = Path(args.sinr_ul) if args.sinr_ul else out_dir / f"{prefix}-sinr-ul.csv"

    tokens, rows = build_tokens(tcp_path, sinr_dl, sinr_ul, cfg)
    if not rows:
        raise SystemExit(f"no RTT tokens could be built from {tcp_path}")

    feature_keys = feature_keys_from_tokens(rows)

    fit_rows = list(rows)
    for extra in args.fit_tcp:
        _, extra_rows = build_tokens(
            Path(extra),
            Path(args.sinr_dl) if args.sinr_dl else None,
            Path(args.sinr_ul) if args.sinr_ul else None,
            cfg,
        )
        fit_rows.extend(extra_rows)

    if args.boundaries:
        boundaries = load_boundaries(Path(args.boundaries), feature_keys)
    else:
        boundaries = fit_boundaries(
            fit_rows,
            feature_keys,
            args.num_buckets,
            sinr_dl=sinr_dl if sinr_dl.exists() else None,
            sinr_ul=sinr_ul if sinr_ul.exists() else None,
        )

    bucketed = apply_boundaries(rows, feature_keys, boundaries)

    # Refresh full Sage RTT token CSV.
    fields, _ = read_csv(tcp_path)
    sage_keys = [k for k in fields if k != "time_s"]
    tokens_path = out_dir / f"{prefix}-tokens.csv"
    extra_keys = [key for key in tokens[0] if key not in META_KEYS and
                  key not in sage_keys and key not in ('base_rtt', 'sinr_dl_db', 'sinr_ul_db')]
    write_tokens(tokens_path, tokens, sage_keys + extra_keys, cfg)

    continuous_fields = [*META_KEYS, *feature_keys]
    continuous_path = out_dir / f"{prefix}-features.csv"
    write_csv(
        continuous_path,
        rows,
        continuous_fields,
        "# MinRTT tokens: TCP/SINR and primary-UE radio events in each interval; "
        "KPI/path values are the latest completed observations at token end.\n",
    )

    bounds_stem = out_dir / f"{prefix}-boundaries-quantile{args.num_buckets}"
    write_boundaries(bounds_stem, feature_keys, boundaries, args.num_buckets)

    bucket_fields = [*META_KEYS]
    for name in feature_keys:
        bucket_fields.append(name)
        if is_bucket_feature(name):
            bucket_fields.append(f"{name}_bucket")
    bucket_path = out_dir / f"{prefix}-bucketized.csv"
    write_csv(
        bucket_path,
        bucketed,
        bucket_fields,
        f"# Multi-head quantile-{args.num_buckets} bucketization of collected "
        "features; base_rtt remains continuous.\n",
    )

    # Compact UNUM-6 view kept for convenience / paper alignment.
    unum6_present = [
        n for n in ("base_rtt", "srtt", "thr", "rttvar", "lost", "pre_act") if n in feature_keys
    ]
    unum6_path = out_dir / f"{prefix}-unum6.csv"
    unum6_fields = [*META_KEYS]
    for name in unum6_present:
        unum6_fields.append(name)
        if is_bucket_feature(name):
            unum6_fields.append(f"{name}_bucket")
    write_csv(
        unum6_path,
        bucketed,
        unum6_fields,
        "# UNUM-6 subset: continuous base_rtt plus buckets for the other features. "
        "Full set is in *-features.csv / *-bucketized.csv.\n",
    )

    print(
        f"rtt_tokens={len(tokens)} features={len(feature_keys)} "
        f"buckets={args.num_buckets} "
        f"out_tokens={tokens_path} out_features={continuous_path} "
        f"out_boundaries={bounds_stem}.json out_bucketized={bucket_path} "
        f"out_unum6={unum6_path}"
    )
    for name in feature_keys:
        if not is_bucket_feature(name):
            continue
        edges = boundaries.get(name, [])
        print(f"  {name}: n_edges={len(edges)} n_buckets={len(edges)+1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
