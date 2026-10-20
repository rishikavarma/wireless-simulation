#!/usr/bin/env python3
"""Render the mobile NR-NTN dataset setup and sampled ranges as one figure."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Arc, Circle, FancyArrowPatch, FancyBboxPatch, Rectangle


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "local" / "figures"

INK = "#18283b"
MUTED = "#56697b"
LINE = "#d9e4ec"
BG = "#f3f7fa"
WHITE = "#ffffff"
TEAL = "#087c91"
TEAL_PALE = "#e7f5f7"
ORANGE = "#d67b26"
ORANGE_PALE = "#fff3e5"
PURPLE = "#67559a"
PURPLE_PALE = "#f0ecf8"
GRAY = "#9caab7"


def text(ax, x, y, value, *, size=11, color=INK, weight="normal", **kwargs):
    """Place a styled label using normalized axes coordinates so the diagram layout is
    independent of data limits.
    """
    return ax.text(
        x, y, value, transform=ax.transAxes, ha="left", va="center",
        fontsize=size, color=color, weight=weight, **kwargs
    )


def card(ax, x, y, w, h, *, tint=WHITE):
    """Add a rounded, tinted panel in normalized axes coordinates to group related setup
    information.
    """
    patch = FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.012",
        linewidth=1, edgecolor=LINE, facecolor=tint, transform=ax.transAxes
    )
    ax.add_patch(patch)


def link(ax, a, b, *, color, width=2.8, dashed=False, z=3):
    """Draw a bidirectional relay path between normalized coordinates, optionally dashed for an
    inactive candidate.
    """
    ax.add_patch(FancyArrowPatch(
        a, b, arrowstyle="<->", mutation_scale=11, linewidth=width,
        linestyle="--" if dashed else "-", color=color,
        transform=ax.transAxes, zorder=z, shrinkA=12, shrinkB=12
    ))


def satellite(ax, x, y, color):
    """Draw a schematic satellite body and two solar panels at the supplied axes position."""
    ax.add_patch(Rectangle((x - .021, y - .009), .015, .018,
                           facecolor=color, edgecolor=WHITE, lw=.7,
                           transform=ax.transAxes, zorder=6))
    ax.add_patch(Rectangle((x + .006, y - .009), .015, .018,
                           facecolor=color, edgecolor=WHITE, lw=.7,
                           transform=ax.transAxes, zorder=6))
    ax.add_patch(FancyBboxPatch((x - .006, y - .011), .012, .022,
                                boxstyle="round,pad=0.001,rounding_size=0.002",
                                facecolor=WHITE, edgecolor=color, lw=1.5,
                                transform=ax.transAxes, zorder=7))


def draw_setup(ax):
    """Compose the UE, two relays, feeder/service paths, gateway/server, and fixed-reference
    panel using schematic drawing helpers.
    """
    card(ax, .032, .669, .621, .244)
    card(ax, .669, .669, .299, .244, tint="#f7f9fc")
    text(ax, .049, .890, "TRANSPARENT NR BENT PIPE", size=15, weight="bold")
    text(ax, .049, .869, "One UE  ·  one ground gNB/cell  ·  two moving LEO relays", size=10.8,
         color=MUTED)

    # Orbit and Earth are schematic. The dashed relay is an available alternative.
    ax.add_patch(Arc((.343, .758), .43, .23, theta1=12, theta2=167,
                     color="#d1dce6", lw=1.5, transform=ax.transAxes, zorder=1))
    ax.add_patch(Arc((.346, .581), .77, .28, theta1=19, theta2=161,
                     color="#dce9ee", lw=2.5, transform=ax.transAxes, zorder=1))
    ax.add_patch(FancyArrowPatch((.431, .852), (.319, .839),
                                 arrowstyle="->", mutation_scale=13,
                                 color=MUTED, lw=1.3, transform=ax.transAxes, zorder=2))
    text(ax, .328, .859, "orbital motion", size=9.6, color=MUTED)

    # Draw standby path first so the selected path stays visually dominant.
    link(ax, (.137, .736), (.459, .817), color=GRAY, width=1.7, dashed=True, z=2)
    link(ax, (.459, .817), (.554, .736), color=GRAY, width=1.7, dashed=True, z=2)
    link(ax, (.137, .736), (.283, .829), color=TEAL)
    link(ax, (.283, .829), (.554, .736), color=ORANGE)

    satellite(ax, .283, .829, PURPLE)
    satellite(ax, .459, .817, GRAY)
    text(ax, .259, .850, "relay 0: active", size=10.6, weight="bold", color=PURPLE)
    text(ax, .436, .836, "relay 1: candidate", size=10.3, color=MUTED)

    # A handset, a ground gateway tower, and the colocated application server.
    ax.add_patch(FancyBboxPatch((.124, .718), .017, .034,
                                boxstyle="round,pad=0.001,rounding_size=0.003",
                                facecolor=WHITE, edgecolor=TEAL, lw=2,
                                transform=ax.transAxes, zorder=6))
    ax.add_patch(Circle((.1325, .724), .0017, color=TEAL,
                        transform=ax.transAxes, zorder=7))
    ax.add_patch(Rectangle((.550, .716), .016, .038,
                           facecolor=WHITE, edgecolor=ORANGE, lw=1.8,
                           transform=ax.transAxes, zorder=6))
    ax.add_patch(Rectangle((.574, .721), .027, .029,
                           facecolor=WHITE, edgecolor=INK, lw=1.4,
                           transform=ax.transAxes, zorder=6))
    for y in (.727, .735, .743):
        ax.add_patch(Circle((.579, y), .0017, color=TEAL,
                            transform=ax.transAxes, zorder=7))
    ax.plot([.566, .574], [.735, .735], color=INK, lw=1.5,
            transform=ax.transAxes, zorder=5)
    text(ax, .094, .695, "NR UE", size=11, weight="bold")
    text(ax, .511, .695, "ground gNB + app server", size=10.4, weight="bold")
    text(ax, .145, .784, "service: UL 1995 / DL 2185 MHz", size=10.0, color=TEAL,
         weight="bold")
    text(ax, .395, .770, "feeder: UL 30 / DL 20 GHz", size=10.0, color=ORANGE,
         weight="bold")

    text(ax, .687, .891, "FIXED REFERENCE", size=15, weight="bold")
    ax.plot([.686, .952], [.874, .874], color=LINE, lw=1, transform=ax.transAxes)
    fixed = [
        "Handset: 0 dBi, 7 dB NF; 23 dBm ceiling",
        "Gateway: 40 dBm, 50 dBi, 5 dB NF",
        "Satellite service: 34 dBW/MHz EIRP; 30 dBi RX",
        "Satellite feeder: 4 dBW/MHz EIRP; 38.5 dBi RX",
        "UE 1.5 m; gNB 25 m; rural LOS",
        "NR OFDMA + RLC AM; TCP NewReno",
        "One active relay; same NR cell/IP/TCP socket",
    ]
    for i, value in enumerate(fixed):
        text(ax, .688, .853 - i * .026, "• " + value, size=10.35)


def detail_card(ax, x, title, rows, *, accent, tint):
    """Draw one titled factor panel and lay out its text rows at fixed vertical spacing."""
    card(ax, x, .397, .299, .253, tint=tint)
    ax.add_patch(Rectangle((x + .010, .615), .006, .021,
                           facecolor=accent, edgecolor="none",
                           transform=ax.transAxes))
    text(ax, x + .025, .625, title, size=14.0, weight="bold")
    ax.plot([x + .011, x + .288], [.610, .610], color=LINE,
            lw=1, transform=ax.transAxes)
    for i, row in enumerate(rows):
        text(ax, x + .014, .592 - i * .0195, row, size=10.75)


def draw_knobs(ax):
    """Render the orbit/mobility, relay/timing, and radio/attenuation panels with the simple
    campaign factor levels.
    """
    detail_card(ax, .032, "ORBIT + UE MOBILITY", [
        "Altitude: 800 km; inclination: 53°",
        "Relay 1 phase: 8° behind relay 0",
        "Orbit period: about 101 min (gravity-derived)",
        "gNB east offset: uniform −20…+20 km",
        "UE offset / movement disk: 500 m",
        "UE stationary / mobile: 50% / 50%",
        "Mobile UE: outdoor walk at 1.4 m/s",
        "Both masks 30°; admitted paths ≥35°",
    ], accent=TEAL, tint="#f5fbfc")
    detail_card(ax, .350, "RELAY HANDOVER + TIME", [
        "Switch enabled / control: 50% / 50%",
        "Select shortest path visible at both ends",
        "Check every 100 ms; hysteresis 5 km",
        "Time to trigger: 1 s",
        "Requested outage: 100 ms",
        "Actual outage ≥ horizon RTT + 10 ms",
        "Both groups: 130 s total duration",
        "Warm-up 15 s; record from 20 to 119 s",
        "Admit exactly 1 switch in record window",
    ], accent=PURPLE, tint="#f9f7fc")
    detail_card(ax, .669, "RADIO + LINK CONDITIONS", [
        "n256 centers fixed: UL 1995 / DL 2185 MHz",
        "Width: 10 MHz; UE power: 23 dBm",
        "Service attenuation: 0 / 1.5 / 3 dB",
        "Feeder fade: clear 0 dB",
        "Feeder fade: moderate 12 dB",
        "Feeder fade: degraded 24 dB",
        "Loss factors rotate independently",
        "Antenna gains, NF and rural LOS fixed",
        "Static weather proxy; no rain time series",
    ], accent=ORANGE, tint="#fffaf4")


def draw_workloads(ax):
    """Render a five-row application table describing the fixed traffic shapes and probe/file
    pacing choices.
    """
    card(ax, .032, .103, .936, .278)
    text(ax, .050, .363, "FIVE EXISTING TCP APPLICATIONS", size=15, weight="bold")
    text(ax, .510, .364, "server think: fixed 0 ms", size=10.8,
         color=MUTED)
    text(ax, .050, .343, "Application", size=10.7, color=MUTED, weight="bold")
    text(ax, .179, .343, "Traffic and sizes", size=10.7, color=MUTED, weight="bold")
    text(ax, .561, .343, "Timing and other sampled parameters", size=10.7,
         color=MUTED, weight="bold")
    rows = [
        ("Probe", "CBR; 512 B packets", "5 or 10 kb/s source pacing"),
        ("File", "Lognormal median 16 kB; sigma 0.3\n128 kB cap; 512 B chunks", "5 or 10 kb/s chunk pacing; 3 s reading mean"),
        ("RPC", "128 B request; Pareto object scale 128 B\nShape 2; object cap 1400 B", "3 objects/page on average; 0.2 s object gap\n1.5 s reading mean"),
        ("Stream", "5 frames/s × 2 slices; 40 B scale\nPareto shape 2; cap 200 B", "2.5 ms slice-gap scale; 10 ms cap"),
        ("IoT", "Uniform payload 64–128 B", "Mean report interval 1.5 s"),
    ]
    y_top = .334
    row_h = .0452
    for i, (name, left, right) in enumerate(rows):
        y = y_top - (i + 1) * row_h
        ax.add_patch(Rectangle((.043, y), .914, row_h,
                               facecolor="#f7fafc" if i % 2 == 0 else WHITE,
                               edgecolor="none", transform=ax.transAxes))
        center = y + row_h / 2
        text(ax, .050, center, name, size=11.2, weight="bold", color=PURPLE)
        text(ax, .179, center, left, size=10.3, linespacing=1.25)
        text(ax, .561, center, right, size=10.3, linespacing=1.25)


def draw_admission(ax):
    """Draw the sampling, visibility admission, and run-retention rules in the figure footer."""
    card(ax, .032, .017, .936, .069, tint="#eaf1f6")
    text(ax, .049, .073,
         "SAMPLING  ·  5 apps × 2 switch states × 2 UE states = 20 balanced cases/cycle; service/feeder loss rotate",
         size=11.2, weight="bold")
    text(ax, .049, .050,
         "ADMISSION  ·  0.5 s whole-run geometry check; both active hops ≥35°; estimated bootstrap UL C/N ≥2 dB",
         size=10.6)
    text(ax, .049, .029,
         "RETENTION  ·  keep completed runs with application exchange and matching switch count; original metrics/feature heads",
         size=10.6)


def main():
    """Assemble the setup and factor panels on a fixed-size canvas and export matching SVG, PDF,
    and PNG figures.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "none"})
    fig = plt.figure(figsize=(20, 15), facecolor=BG)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    text(ax, .032, .972, "Mobile NR-NTN dataset: setup and sampled knobs",
         size=25, weight="bold")
    text(ax, .033, .937,
         "Transparent two-satellite LEO relay  ·  controlled orbital switching  ·  one handheld UE and ground gNB",
         size=12.6, color=MUTED)
    draw_setup(ax)
    draw_knobs(ax)
    draw_workloads(ax)
    draw_admission(ax)
    for ext, kwargs in (("svg", {}), ("pdf", {}), ("png", {"dpi": 170})):
        path = OUT / f"mobile-dataset-design.{ext}"
        fig.savefig(path, facecolor=BG, bbox_inches=None, **kwargs)
        print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
