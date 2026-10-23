#!/usr/bin/env python3
"""Fold 5 ms metric snapshots into tokens.

The ns-3 run keeps writing one TCP row every 5 ms. This script reads those
rows (and optional SINR traces) and writes one token per window. The window
comes from output.token* in config.json.

Min-RTT aggregation (default):
  1. Size each window from the **current** min RTT (``rtt_rate * srtt``) at
     the first sample of the buffer (not locked for the whole flow).
  2. Accumulate fine-grained samples until wall time >= that window RTT.
  3. Collapse the buffer with a **time-weighted mean**
     ``sum(feature * dt) / sum(dt)`` (``lost`` = delta, ``ca_state`` = mode).
  4. Emit one token; derived ``base_rtt`` is ``rtt_rate * srtt`` on the token.

Set ``output.tokenRttLock`` true to freeze min RTT from the first sample
(UNUM CC runtime). Set ``tokenRttColumn`` to ``srtt`` to size windows on
smoothed RTT. ``tokenAggregate`` may be ``weighted`` (default), ``mean``, or
``last``.
"""

import argparse
import csv

from metric_io import read_commented_csv
import json
import sys
from pathlib import Path


MINRTT_ALIASES = ("minrtt", "min_rtt", "base_rtt")


def load_config(path):
    """Load the experiment JSON using the standard decoder; return its nested configuration
    dictionary.
    """
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def cfg_get(root, dotted, default=None):
    """Follow a dotted key through nested dictionaries, returning the supplied default if any
    component is absent.
    """
    node = root
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def read_csv(path):
    """Decode numeric metric rows with the shared comment-aware reader, then sort them by
    simulation time.
    """
    fields, rows = read_commented_csv(Path(path))
    rows.sort(key=lambda row: row["time_s"])
    return fields, rows


def hold_last(samples, end_us):
    """Scan time-ordered PHY samples up to the requested microsecond boundary and return the last
    observed SINR, or None before the first report.
    """
    found = None
    for sample in samples:
        if microseconds(sample["time_s"]) <= end_us:
            found = sample["sinr_db"]
        else:
            break
    return found


def microseconds(seconds):
    """Round a seconds value to integer microseconds so token boundary comparisons avoid
    floating-point drift.
    """
    return int(round(seconds * 1e6))


def mean(values):
    """Average the observed values, excluding None entries; return None when the window contains
    no observations.
    """
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def weighted_mean(values, weights):
    """Average observed values using positive time weights; if their total is zero, use the
    arithmetic mean of the observed values.
    """
    total_w = 0.0
    total = 0.0
    present = 0
    fallback = []
    for value, weight in zip(values, weights):
        if value is None:
            continue
        fallback.append(value)
        present += 1
        if weight > 0:
            total += float(value) * weight
            total_w += weight
    if present == 0:
        return None
    if total_w < 1e-15:
        return sum(fallback) / len(fallback)
    return total / total_w


def most_frequent(values):
    """Count the observed values and choose the most frequent; resolve equal counts in favor of
    the value appearing latest in the window.
    """
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
    """Subtract the first observed value from the last, returning zero for one sample and None
    when all values are missing.
    """
    present = [value for value in values if value is not None]
    if not present:
        return None
    if len(present) == 1:
        return 0.0
    return present[-1] - present[0]


def window_rtt_units(row, rtt_column):
    """RTT used to size tokens, in the same Sage units as ``srtt``.

    ``minrtt`` / ``min_rtt`` / ``base_rtt`` resolve to ``rtt_rate * srtt``
    (minimum RTT). Any other name is read as a plain column on the row.
    """
    if rtt_column in MINRTT_ALIASES:
        srtt = row.get("srtt")
        rtt_rate = row.get("rtt_rate")
        if srtt is None or rtt_rate is None:
            return 0.0
        return float(rtt_rate) * float(srtt)
    value = row.get(rtt_column)
    return 0.0 if value is None else float(value)


def sample_dts_s(times_us):
    """Compute seconds since each preceding timestamp, using the next gap for the first sample or
    5 ms for a singleton and flooring gaps at one microsecond.
    """
    if not times_us:
        return []
    dts = []
    for i, t_us in enumerate(times_us):
        if i == 0:
            if len(times_us) > 1:
                dts.append(max(1e-6, (times_us[1] - t_us) / 1e6))
            else:
                dts.append(0.005)
        else:
            dts.append(max(1e-6, (t_us - times_us[i - 1]) / 1e6))
    return dts


def collapse_group(group, dts, feature_keys, aggregate, locked_base_rtt_units):
    """Build one token from a sample group: record its time span, use mode for congestion state
    and first-to-last change for cumulative loss, and aggregate other fields by the selected
    mean/last rule.
    """
    start_us = microseconds(group[0]["time_s"])
    end_us = microseconds(group[-1]["time_s"]) + microseconds(dts[-1])
    token = {
        "token": None,  # filled by caller
        "t_start_s": start_us / 1e6,
        "t_end_s": end_us / 1e6,
        "window_s": (end_us - start_us) / 1e6,
        "n_samples": float(len(group)),
    }
    if locked_base_rtt_units is not None:
        token["base_rtt"] = locked_base_rtt_units
    for key in feature_keys:
        series = [row[key] for row in group]
        if key == "ca_state":
            token[key] = most_frequent(series)
        elif key == "lost":
            token[key] = difference(series)
        elif aggregate == "last":
            token[key] = series[-1]
        elif aggregate == "mean":
            token[key] = mean(series)
        else:
            token[key] = weighted_mean(series, dts)
    return token


def fold(tcp_rows, feature_keys, sinr_dl, sinr_ul, cfg):
    """Partition time-sorted TCP rows into fixed or RTT-sized windows, aggregate each signal with
    its configured rule, and attach available SINR samples.
    """
    mode = cfg_get(cfg, "output.tokenMode", "rtt")
    fixed_s = float(cfg_get(cfg, "output.tokenFixedS", 0.1))
    min_s = float(cfg_get(cfg, "output.tokenMinS", 0.005))
    aggregate = cfg_get(cfg, "output.tokenAggregate", "weighted")
    rtt_column = cfg_get(cfg, "output.tokenRttColumn", "minrtt")
    seconds_per_unit = float(cfg_get(cfg, "output.tokenRttSecondsPerUnit", 0.1))
    lock_rtt = bool(cfg_get(cfg, "output.tokenRttLock", False))
    if mode not in ("rtt", "fixed"):
        raise SystemExit(f"output.tokenMode must be rtt or fixed, got {mode}")
    if mode == 'fixed' and fixed_s <= 0:
        raise ValueError('fixed token interval must be positive')
    if aggregate not in ("weighted", "mean", "last"):
        raise SystemExit(
            f"output.tokenAggregate must be weighted, mean, or last, got {aggregate}"
        )

    times_us = [microseconds(row["time_s"]) for row in tcp_rows]
    dts = sample_dts_s(times_us)
    tokens = []
    count = len(tcp_rows)

    if mode == "fixed":
        # A shared wall-clock grid retains failed connections and reconnect gaps.
        # Empty windows contain missing measurements, never invented zeros.
        start_s = cfg_get(cfg, 'time.warmUp', 0) + cfg_get(cfg, 'time.measureStart', 0)
        end_s = (cfg_get(cfg, 'time.simTime', 0) - cfg_get(cfg, 'time.appDrain', 0)
                 - cfg_get(cfg, 'time.measureEnd', 0))
        if end_s <= start_s:
            raise ValueError('fixed tokens require a positive configured measurement window')
        index = 0
        start_us = microseconds(start_s)
        stop_us = microseconds(end_s)
        while start_us + microseconds(fixed_s) <= stop_us:
            end_us = start_us + microseconds(fixed_s)
            while index < count and times_us[index] < start_us:
                index += 1
            group = []
            group_dts = []
            while index < count and times_us[index] < end_us:
                group.append(tcp_rows[index])
                group_dts.append(dts[index])
                index += 1
            if group:
                token = collapse_group(group, group_dts, feature_keys, aggregate, None)
            else:
                token = {key: None for key in feature_keys}
                token['n_samples'] = 0
            token['t_start_s'] = start_us / 1e6
            token["token"] = float(len(tokens))
            token["t_end_s"] = end_us / 1e6
            token["window_s"] = (end_us - start_us) / 1e6
            if "sinr_dl_db" not in feature_keys:
                token["sinr_dl_db"] = hold_last(sinr_dl, end_us)
                token["sinr_ul_db"] = hold_last(sinr_ul, end_us)
            tokens.append(token)
            start_us = end_us
        return tokens

    # RTT mode — UNUM: accumulate until locked (or per-window) base RTT.
    locked_units = None
    locked_window_s = None
    buffer = []
    buffer_dts = []
    accumulated_s = 0.0
    index = 0
    while index < count:
        row = tcp_rows[index]
        dt = dts[index]
        if locked_units is None or not lock_rtt:
            units = window_rtt_units(row, rtt_column)
            window_s = max(min_s, units * seconds_per_unit) if units > 0 else min_s
            if lock_rtt:
                locked_units = units if units > 0 else (min_s / seconds_per_unit)
                locked_window_s = window_s
            else:
                # Re-size every token from the first sample of the new buffer.
                if not buffer:
                    locked_units = units if units > 0 else (min_s / seconds_per_unit)
                    locked_window_s = window_s
        target_s = locked_window_s if locked_window_s is not None else min_s

        buffer.append(row)
        buffer_dts.append(dt)
        accumulated_s += dt
        index += 1

        if accumulated_s + 1e-15 >= target_s:
            end_us = microseconds(buffer[-1]["time_s"])
            # When unlocked, still record this window's minrtt as base_rtt.
            token_base = locked_units if locked_units is not None else None
            token = collapse_group(
                buffer, buffer_dts, feature_keys, aggregate, token_base
            )
            token["token"] = float(len(tokens))
            # Report the target window length UNUM aimed for (not just span of samples).
            token["window_s"] = target_s
            token["t_end_s"] = token["t_start_s"] + target_s
            if "sinr_dl_db" not in feature_keys:
                token["sinr_dl_db"] = hold_last(sinr_dl, end_us)
                token["sinr_ul_db"] = hold_last(sinr_ul, end_us)
            tokens.append(token)
            buffer = []
            buffer_dts = []
            accumulated_s = 0.0
            if not lock_rtt:
                locked_units = None
                locked_window_s = None

    # Drop a trailing partial window (UNUM only emits when the base-RTT budget fills).
    return tokens


def write_tokens(path, tokens, feature_keys, cfg):
    """Write token timing and aggregated metrics to CSV with comments recording the tokenization
    configuration.
    """
    mode = cfg_get(cfg, "output.tokenMode", "rtt")
    aggregate = cfg_get(cfg, "output.tokenAggregate", "weighted")
    rtt_column = cfg_get(cfg, "output.tokenRttColumn", "minrtt")
    lock_rtt = bool(cfg_get(cfg, "output.tokenRttLock", False))
    extra = []
    if tokens and "base_rtt" in tokens[0] and "base_rtt" not in feature_keys:
        extra.append("base_rtt")
    if "sinr_dl_db" not in feature_keys:
        extra.extend(["sinr_dl_db", "sinr_ul_db"])
    # Stable header: meta, optional locked base_rtt, sage features, sinr.
    header = ["token", "t_start_s", "t_end_s", "window_s", "n_samples"]
    if "base_rtt" in extra:
        header.append("base_rtt")
    header.extend(feature_keys)
    header.extend([k for k in extra if k != "base_rtt"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(
            f"# One token per window. mode={mode} aggregate={aggregate} "
            f"rtt_column={rtt_column} lock_rtt={bool(lock_rtt)}. "
            "minrtt windows use current rtt_rate*srtt unless lock_rtt; "
            "time-weighted mean (lost=delta, ca_state=mode). "
            "SINR is hold-last at the last sample in the window.\n"
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
    """Parse raw TCP/SINR paths and token settings, fold the traces into windows, and write the
    resulting token CSV.
    """
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
