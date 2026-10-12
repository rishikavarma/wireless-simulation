#!/usr/bin/env python3
"""Bar charts of the time-average of each collected metric, compared across applications.

Reads results/exp-<workload>/exp-<workload>-tcp.csv. Each bar is the mean of
that column over the record window. Each panel is one metric, so a large
application does not set the scale of a different metric.
"""

import argparse
import csv
import sys
import textwrap
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

WORKLOADS = ["probe", "file", "rpc", "stream", "iot"]
LABELS = {
    "probe": "Probe",
    "file": "File",
    "rpc": "RPC",
    "stream": "Stream",
    "iot": "IoT",
}
COLORS = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7"]

INSTANTANEOUS = ["srtt", "rttvar", "thr", "ca_state", "lost"]
DERIVED = [
    "time_delta",
    "rtt_rate",
    "loss_db",
    "acked_rate",
    "dr_ratio",
    "bdp_cwnd",
    "dr",
    "cwnd_unacked_rate",
    "dr_max",
    "dr_max_ratio",
    "pre_act",
]
SINR = ["sinr_dl_db", "sinr_ul_db"]

NOTES = {
    "instantaneous": (
        "Bar height is the mean of the column over the record window. "
        "srtt is µs/1e5 (1 = 100 ms). rttvar is µs/1e3 (1 = 1 ms). "
        "thr is a fraction of 100 Mbps. lost is cumulative lost packets/100. "
        "ca_state: 0 Open, 1 Disorder, 2 Cwr, 3 Recovery, 4 Loss."
    ),
    "derived": (
        "Bar height is the mean of the column over the record window. "
        "loss_db, dr, and dr_max are a fraction of 100 Mbps. "
        "pre_act is log2 of the congestion-window change."
    ),
    "sinr": "Bar height is the mean SINR on the TCP rows over the record window, in dB.",
}


def load_trace(path):
    with path.open(newline="", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip() and not line.startswith("#")]
    reader = csv.DictReader(lines)
    columns = reader.fieldnames or []
    data = {column: [] for column in columns}
    for row in reader:
        for column in columns:
            text = (row[column] or "").strip()
            data[column].append(np.nan if text == "" else float(text))
    return {column: np.asarray(values, dtype=float) for column, values in data.items()}


def load_workloads(results):
    traces = {}
    for workload in WORKLOADS:
        path = results / f"exp-{workload}" / f"exp-{workload}-tcp.csv"
        if not path.is_file():
            raise SystemExit(f"missing trace: {path}")
        traces[workload] = load_trace(path)
    return traces


def time_mean(traces, column):
    means = []
    for workload in WORKLOADS:
        values = traces[workload][column]
        values = values[np.isfinite(values)]
        means.append(float(np.mean(values)) if values.size else np.nan)
    return means


def label_of(value):
    if not np.isfinite(value):
        return ""
    return f"{value:.3g}"


def plot_bars(traces, columns, title, note, out_path, ncols):
    nrows = int(np.ceil(len(columns) / ncols))
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(4.2 * ncols, 3.1 * nrows + 0.7),
        squeeze=False,
    )
    names = [LABELS[workload] for workload in WORKLOADS]
    x = np.arange(len(WORKLOADS))
    flat = axes.ravel()
    for index, column in enumerate(columns):
        ax = flat[index]
        means = time_mean(traces, column)
        bars = ax.bar(x, means, color=COLORS, width=0.72)
        ax.set_xticks(x)
        ax.set_xticklabels(names)
        ax.set_title(column)
        finite = [value for value in means if np.isfinite(value)]
        lo = min(0.0, min(finite, default=0.0))
        hi = max(0.0, max(finite, default=0.0))
        span = hi - lo
        if span == 0:
            span = 1.0 if hi == 0 else abs(hi)
        ax.set_ylim(lo - 0.08 * span, hi + 0.22 * span)
        for bar, value in zip(bars, means):
            if not np.isfinite(value):
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value,
                label_of(value),
                ha="center",
                va="bottom" if value >= 0 else "top",
                fontsize=8,
            )
        ax.axhline(0, color="#888888", lw=0.6)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    for axis in flat[len(columns) :]:
        axis.set_visible(False)

    width = max(80, int(fig.get_size_inches()[0] * 12))
    wrapped = "\n".join(textwrap.wrap(note, width=width))
    note_lines = wrapped.count("\n") + 1
    height_in = fig.get_size_inches()[1]
    header_in = 0.28 + 0.16 * note_lines
    top = 1 - header_in / height_in
    fig.suptitle(title, fontsize=14, y=1 - 0.08 / height_in, va="top")
    fig.text(
        0.5,
        1 - 0.32 / height_in,
        wrapped,
        ha="center",
        va="top",
        fontsize=8,
        color="#444444",
    )
    fig.tight_layout(rect=(0, 0.01, 1, max(top, 0.5)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    print(out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default="results", help="directory that contains exp-<workload>/")
    parser.add_argument(
        "--out",
        default="results/workload-experiment/figures",
        help="directory for the PNG files",
    )
    args = parser.parse_args()
    traces = load_workloads(Path(args.results))
    out = Path(args.out)
    plot_bars(traces, INSTANTANEOUS, "Instantaneous", NOTES["instantaneous"], out / "instantaneous.png", 3)
    plot_bars(traces, DERIVED, "Derived", NOTES["derived"], out / "derived.png", 4)
    plot_bars(traces, SINR, "SINR", NOTES["sinr"], out / "sinr.png", 2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
