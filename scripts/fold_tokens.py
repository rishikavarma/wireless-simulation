#!/usr/bin/env python3
"""Fold 5 ms metric snapshots into tokens.

The ns-3 run keeps writing one TCP row every 5 ms. This script reads those
rows (and optional SINR traces) and writes one token per window. The window
comes from output.token* in config.json.
"""

import argparse
import csv
import json
import sys
from pathlib import Path


def load_config(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def cfg_get(root, dotted, default=None):
    node = root
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def read_csv(path):
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
    rows.sort(key=lambda row: row["time_s"])
    return list(reader.fieldnames or []), rows


def hold_last(samples, end_us):
    """Latest sinr_db at or before end_us. None if none yet."""
    found = None
    for sample in samples:
        if microseconds(sample["time_s"]) <= end_us:
            found = sample["sinr_db"]
        else:
            break
    return found


def microseconds(seconds):
    return int(round(seconds * 1e6))


def mean(values):
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def most_frequent(values):
    """Most common value. A tie keeps the one that appears latest in the window."""
    present = [value for value in values if value is not None]
    if not present:
        return None
    counts = {}
    for value in present:
        counts[value] = counts.get(value, 0) + 1
    best = None
    best_count = -1
    for value in present:
        count = counts[value]
        if count >= best_count:
            best = value
            best_count = count
    return best


def difference(values):
    """Increase from the first sample in the window to the last."""
    present = [value for value in values if value is not None]
    if not present:
        return None
    if len(present) == 1:
        return 0.0
    return present[-1] - present[0]


def fold(tcp_rows, feature_keys, sinr_dl, sinr_ul, cfg):
    mode = cfg_get(cfg, "output.tokenMode", "rtt")
    fixed_s = float(cfg_get(cfg, "output.tokenFixedS", 0.1))
    min_s = float(cfg_get(cfg, "output.tokenMinS", 0.005))
    aggregate = cfg_get(cfg, "output.tokenAggregate", "mean")
    rtt_column = cfg_get(cfg, "output.tokenRttColumn", "srtt")
    seconds_per_unit = float(cfg_get(cfg, "output.tokenRttSecondsPerUnit", 0.1))
    if mode not in ("rtt", "fixed"):
        raise SystemExit(f"output.tokenMode must be rtt or fixed, got {mode}")
    if aggregate not in ("mean", "last"):
        raise SystemExit(f"output.tokenAggregate must be mean or last, got {aggregate}")

    tokens = []
    index = 0
    count = len(tcp_rows)
    times_us = [microseconds(row["time_s"]) for row in tcp_rows]
    while index < count:
        start_us = times_us[index]
        if mode == "fixed":
            window_s = fixed_s
        else:
            rtt_value = tcp_rows[index].get(rtt_column) or 0.0
            window_s = max(min_s, rtt_value * seconds_per_unit)
        end_us = start_us + microseconds(window_s)
        group = []
        while index < count and times_us[index] < end_us:
            group.append(tcp_rows[index])
            index += 1
        if not group:
            index += 1
            continue
        token = {
            "token": float(len(tokens)),
            "t_start_s": start_us / 1e6,
            "t_end_s": end_us / 1e6,
            "window_s": (end_us - start_us) / 1e6,
            "n_samples": float(len(group)),
        }
        for key in feature_keys:
            series = [row[key] for row in group]
            if key == "ca_state":
                token[key] = most_frequent(series)
            elif key == "lost":
                token[key] = difference(series)
            else:
                token[key] = series[-1] if aggregate == "last" else mean(series)
        if "sinr_dl_db" not in feature_keys:
            token["sinr_dl_db"] = hold_last(sinr_dl, end_us)
            token["sinr_ul_db"] = hold_last(sinr_ul, end_us)
        tokens.append(token)
    return tokens


def write_tokens(path, tokens, feature_keys, cfg):
    mode = cfg_get(cfg, "output.tokenMode", "rtt")
    aggregate = cfg_get(cfg, "output.tokenAggregate", "mean")
    extra = [] if "sinr_dl_db" in feature_keys else ["sinr_dl_db", "sinr_ul_db"]
    header = [
        "token",
        "t_start_s",
        "t_end_s",
        "window_s",
        "n_samples",
        *feature_keys,
        *extra,
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(
            f"# One token per window. mode={mode} aggregate={aggregate}. "
            "lost is the increase in cumulative lost over the window. "
            "ca_state is the most frequent value in the window. "
            "SINR on a Sage row is already the latest report at that tick.\n"
        )
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for token in tokens:
            writer.writerow(
                {
                    key: "" if token.get(key) is None else token.get(key, "")
                    for key in header
                }
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="scratch/sat-bs-handset/config/config.json",
        help="config.json (output.token* keys)",
    )
    parser.add_argument("--tcp", required=True, help="5 ms TCP snapshot CSV")
    parser.add_argument("--sinr-dl", default="", help="downlink SINR CSV")
    parser.add_argument("--sinr-ul", default="", help="uplink SINR CSV")
    parser.add_argument("--out", required=True, help="token CSV to write")
    args = parser.parse_args()

    cfg = load_config(args.config)
    tcp_fields, tcp_rows = read_csv(args.tcp)
    feature_keys = [key for key in tcp_fields if key != "time_s"]
    _, sinr_dl = read_csv(args.sinr_dl) if args.sinr_dl else ([], [])
    _, sinr_ul = read_csv(args.sinr_ul) if args.sinr_ul else ([], [])
    tokens = fold(tcp_rows, feature_keys, sinr_dl, sinr_ul, cfg)
    out = Path(args.out)
    write_tokens(out, tokens, feature_keys, cfg)
    print(f"tokens={len(tokens)} out={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
