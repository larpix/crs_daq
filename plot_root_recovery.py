#!/usr/bin/env python3
"""
plot_root_recovery.py

Companion plotter for v2b_root_recovery_monitor.py.

Usage
-----
    python plot_root_recovery.py root_recovery_logs/root_recovery_YYYYMMDD_HHMMSS_TZ/

By default the script writes PNGs to:
    <log-folder>/plots/

Outputs
-------
1. root_recovery_status_iog5.png
2. root_recovery_status_iog6.png
   Hydra/anode-style physical maps using the same geometry YAML as
   analysis/plot_hydra_network_anode.py. Only root chips are color-filled.

3. root_recovery_reliability_by_test.png
   Aligned Cycle x Phase raster. Every root occupies the same x-column for
   a given recovery test, so sequential scan timing cannot masquerade as
   physical behavior. The start timestamp of each cycle is shown above it.

4. root_recovery_reliability_vs_time.png
   The same reliability information on a real wall-clock axis. Phase windows
   and cycle boundaries are drawn explicitly so scan-order timing and the
   between-cycle wait are visible rather than mysterious.

Reliability score
-----------------
For each root and recovery phase:

    score = 0.5 * (
        post_read matched_registers / total_registers
        +
        repeat_read matched_registers / total_registers
    )

Thus:
    1.0 = both full readbacks are perfect
    0.5 = e.g. one full failure and one perfect readback
    0.0 = no matching register replies in either read

pre_read is intentionally NOT included because the hard-reset phases clear
the return-path configuration, so a pre-read failure after reset is expected.

Recovery phases produced by v2b_root_recovery_monitor.py
--------------------------------------------------------
Fast / fast_poke:
    No IOG reset. Short timeout/delay. Asks whether the root can be brought
    up quickly with minimal intervention.

2x Reset / double_reset:
    Two long hard resets of the IOG before its roots are scanned, followed by
    normal bootstrap/configuration/readback.

Reset Burst / reset_burst:
    A burst of short hard resets before the IOG is scanned. This is the
    aggressive repeated-reset recovery attempt.

Slow / slow_poke:
    No IOG reset. Long timeout/delay and repeated verification. Asks whether
    patient communication can recover a marginal root.

The monitor applies IOG resets once per IOG/phase, not once per individual root.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Patch, Rectangle


SCRIPT_VERSION = "2026-08-14-root-recovery-plot-v2.2"
# ARTIFACT_MARKER = "fresh-v2.2-phase-filter-hydra-fix"
DEFAULT_GEOMETRY = "analysis/multi_tile_layout-2.3.16.yaml"

PHASE_ORDER = ["fast_poke", "double_reset", "reset_burst", "slow_poke"]
PHASE_LABEL = {
    "fast_poke": "Fast",
    "double_reset": "2x Reset",
    "reset_burst": "Burst",
    "slow_poke": "Slow",
}
PHASE_SHORT = {
    "fast_poke": "Fast",
    "double_reset": "2xR",
    "reset_burst": "Burst",
    "slow_poke": "Slow",
}

PHASE_CLI_MAP = {
    "all": None,
    "fast": "fast_poke",
    "double": "double_reset",
    "burst": "reset_burst",
    "slow": "slow_poke",
    "fast_poke": "fast_poke",
    "double_reset": "double_reset",
    "reset_burst": "reset_burst",
    "slow_poke": "slow_poke",
}

# Explicit reliability/status palette: red -> yellow -> green.
RELIABILITY_CMAP = LinearSegmentedColormap.from_list(
    "root_recovery_reliability",
    ["#b2182b", "#fdd36a", "#1a9850"],
)
RELIABILITY_CMAP.set_bad("#eeeeee")

STATUS_PASS = "#2ca25f"
STATUS_PARTIAL = "#fdae61"
STATUS_FAIL = "#d73027"
STATUS_NOT_TESTED = "#bdbdbd"
STATUS_SKIPPED = "#e0e0e0"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot LArPix v2b root-recovery monitor logs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    parser.add_argument(
        "log_folder",
        type=Path,
        help="Root-recovery session directory containing trials.csv and session.json.",
    )
    parser.add_argument(
        "--geometry-yaml",
        type=Path,
        default=None,
        help=(
            "Anode geometry YAML. If omitted, the plotter searches the session "
            "repo_root, current directory, and script directory for "
            f"{DEFAULT_GEOMETRY}."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory. Default: <log_folder>/plots/",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=180,
        help="PNG resolution.",
    )
    parser.add_argument(
        "--by-test-phase",
        choices=[
            "all",
            "fast",
            "double",
            "burst",
            "slow",
            "fast_poke",
            "double_reset",
            "reset_burst",
            "slow_poke",
        ],
        default="all",
        help=(
            "Filter only root_recovery_reliability_by_test*.png to one "
            "recovery phase. The real-time plot still shows all phases."
        ),
    )
    return parser.parse_args()


def _as_bool(series):
    if pd.api.types.is_bool_dtype(series):
        return series
    mapped = (
        series.astype(str)
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False})
    )
    return mapped.astype("boolean").fillna(False).astype(bool)


def load_session(log_folder: Path):
    session_path = log_folder / "session.json"
    if not session_path.exists():
        return {}
    with session_path.open() as f:
        return json.load(f)


def load_trials(log_folder: Path):
    path = log_folder / "trials.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")

    df = pd.read_csv(path)
    required = {
        "timestamp_local",
        "elapsed_s",
        "cycle",
        "phase",
        "io_group",
        "tile",
        "io_channel",
        "chip_id",
        "step",
        "any_reply",
        "all_match",
        "total_registers",
        "matched_registers",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"trials.csv is missing required columns: {missing}")

    df["any_reply"] = _as_bool(df["any_reply"])
    df["all_match"] = _as_bool(df["all_match"])
    df["cycle"] = pd.to_numeric(df["cycle"], errors="coerce").astype("Int64")
    for col in ["io_group", "tile", "io_channel", "chip_id"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # Preserve the local offset stored by the monitor. For a single session
    # the offset should be constant, so matplotlib can use these directly.
    df["dt_local"] = pd.to_datetime(df["timestamp_local"], errors="coerce")
    if df["dt_local"].isna().all():
        raise ValueError("Could not parse any timestamp_local entries.")

    return df


def targets_dataframe(session, trials):
    if session.get("targets"):
        targets = pd.DataFrame(session["targets"])
    else:
        targets = trials[
            ["io_group", "tile", "io_channel", "chip_id"]
        ].drop_duplicates()

    for col in ["io_group", "tile", "io_channel", "chip_id"]:
        targets[col] = pd.to_numeric(targets[col], errors="coerce").astype(int)

    targets = (
        targets.drop_duplicates()
        .sort_values(["io_group", "io_channel", "chip_id"])
        .reset_index(drop=True)
    )
    targets["root_key"] = targets.apply(
        lambda r: (int(r.io_group), int(r.io_channel), int(r.chip_id)), axis=1
    )
    targets["label"] = targets.apply(
        lambda r: (
            f"IOG{int(r.io_group)}  "
            f"T{int(r.tile)}  "
            f"IOC{int(r.io_channel):02d}  "
            f"C{int(r.chip_id)}"
        ),
        axis=1,
    )
    return targets


def build_scores(trials):
    reads = trials[trials["step"].isin(["post_read", "repeat_read"])].copy()
    if reads.empty:
        raise ValueError("No post_read/repeat_read rows found in trials.csv.")

    reads["matched_fraction"] = (
        pd.to_numeric(reads["matched_registers"], errors="coerce").fillna(0.0)
        / pd.to_numeric(reads["total_registers"], errors="coerce").replace(0, np.nan)
    ).fillna(0.0)

    # Use the post-read timestamp as the placement time for each root test.
    # post and repeat are adjacent operations, so this is a faithful wall-clock
    # location and, importantly, it preserves the original local UTC offset.
    scores = (
        reads.groupby(
            ["cycle", "phase", "io_group", "tile", "io_channel", "chip_id"],
            as_index=False,
            dropna=False,
        )
        .agg(
            score=("matched_fraction", "mean"),
            elapsed_s=("elapsed_s", "mean"),
            dt_local=("dt_local", "min"),
            n_reads=("step", "count"),
        )
    )

    return scores


def phase_order_index(phase):
    try:
        return PHASE_ORDER.index(phase)
    except ValueError:
        return len(PHASE_ORDER)


def cycle_phase_columns(scores, selected_phase=None):
    observed = set(
        (int(c), str(p))
        for c, p in scores[["cycle", "phase"]].dropna().itertuples(index=False, name=None)
    )

    if selected_phase is not None:
        observed = {(c, p) for c, p in observed if p == selected_phase}

    cycles = sorted({c for c, _ in observed})
    columns = []
    for cycle in cycles:
        known = [(cycle, p) for p in PHASE_ORDER if (cycle, p) in observed]
        unknown = sorted(
            [(c, p) for c, p in observed if c == cycle and p not in PHASE_ORDER],
            key=lambda x: x[1],
        )
        columns.extend(known + unknown)
    return columns


def cycle_start_table(trials):
    starts = (
        trials.dropna(subset=["cycle", "dt_local"])
        .groupby("cycle", as_index=False)
        .agg(dt_local=("dt_local", "min"), elapsed_s=("elapsed_s", "min"))
        .sort_values("cycle")
    )
    return starts


def phase_bounds_table(trials):
    bounds = (
        trials.dropna(subset=["cycle", "phase", "dt_local"])
        .groupby(["cycle", "phase"], as_index=False)
        .agg(
            start=("dt_local", "min"),
            end=("dt_local", "max"),
            elapsed_start=("elapsed_s", "min"),
            elapsed_end=("elapsed_s", "max"),
        )
    )
    bounds["phase_order"] = bounds["phase"].map(
        {p: i for i, p in enumerate(PHASE_ORDER)}
    ).fillna(len(PHASE_ORDER))
    return bounds.sort_values(["cycle", "phase_order", "start"])


def cycle_start_label(ts):
    if pd.isna(ts):
        return ""
    # Offset is more robust than an assumed timezone abbreviation.
    return ts.strftime("%m/%d\n%H:%M:%S")


def plot_reliability_by_test(
    trials, scores, targets, output_path, dpi, selected_phase=None
):
    columns = cycle_phase_columns(scores, selected_phase=selected_phase)
    if not columns:
        raise ValueError("No cycle/phase tests found.")

    root_to_row = {key: i for i, key in enumerate(targets["root_key"])}
    col_to_index = {cp: j for j, cp in enumerate(columns)}
    matrix = np.full((len(targets), len(columns)), np.nan)

    for row in scores.itertuples(index=False):
        key = (int(row.io_group), int(row.io_channel), int(row.chip_id))
        cp = (int(row.cycle), str(row.phase))
        if key in root_to_row and cp in col_to_index:
            matrix[root_to_row[key], col_to_index[cp]] = float(row.score)

    ncols = len(columns)
    width = min(28.0, max(14.0, 8.0 + 0.32 * ncols))
    height = max(12.0, 0.20 * len(targets) + 4.0)

    fig, ax = plt.subplots(figsize=(width, height))
    im = ax.imshow(
        matrix,
        aspect="auto",
        interpolation="nearest",
        cmap=RELIABILITY_CMAP,
        vmin=0,
        vmax=1,
    )

    ax.set_yticks(np.arange(len(targets)))
    ax.set_yticklabels(targets["label"], fontsize=7)
    ax.set_xticks(np.arange(ncols))
    ax.set_xticklabels(
        [PHASE_SHORT.get(phase, phase) for _, phase in columns],
        rotation=90,
        fontsize=8,
    )

    # Tile and IOG separators.
    for idx in range(1, len(targets)):
        prev = targets.iloc[idx - 1]
        cur = targets.iloc[idx]
        if cur.io_group != prev.io_group:
            ax.axhline(idx - 0.5, color="black", linewidth=1.8)
        elif cur.tile != prev.tile:
            ax.axhline(idx - 0.5, color="0.80", linewidth=0.6)

    starts = cycle_start_table(trials).set_index("cycle")

    # Cycle boundaries + centered cycle/timestamp annotation.
    cycles = sorted({c for c, _ in columns})
    for cycle in cycles:
        inds = [j for j, (c, _) in enumerate(columns) if c == cycle]
        if not inds:
            continue
        left, right = inds[0], inds[-1]
        if left > 0:
            ax.axvline(left - 0.5, color="black", linewidth=1.3)

        center = (left + right) / 2.0
        ts = starts.loc[cycle, "dt_local"] if cycle in starts.index else pd.NaT
        ax.text(
            center,
            1.012,
            f"Cycle {cycle}\n{cycle_start_label(ts)}",
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="bottom",
            fontsize=8,
            fontweight="bold",
            clip_on=False,
        )

    if selected_phase is None:
        title_line = "Root recovery reliability by test"
        subtitle_line = "Every root is aligned to the same Cycle x Phase column"
    else:
        phase_name = PHASE_LABEL.get(selected_phase, selected_phase)
        title_line = f"Root recovery reliability by test — {phase_name} only"
        subtitle_line = "One aligned column per recovery cycle"

    ax.set_title(
        f"{title_line}\n{subtitle_line}",
        pad=72,
    )
    ax.set_xlabel("Recovery test")
    ax.set_ylabel("Root")

    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label(
        "Reliability score = mean matched-register fraction of post + repeat reads"
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def _session_time_span(trials):
    valid = trials["dt_local"].dropna()
    return valid.min(), valid.max()


def plot_reliability_vs_time(trials, scores, targets, output_path, dpi):
    root_to_row = {key: i for i, key in enumerate(targets["root_key"])}

    plot_df = scores.copy()
    plot_df["root_row"] = plot_df.apply(
        lambda r: root_to_row.get(
            (int(r.io_group), int(r.io_channel), int(r.chip_id)), np.nan
        ),
        axis=1,
    )
    plot_df = plot_df.dropna(subset=["root_row", "dt_local"])
    plot_df["root_row"] = plot_df["root_row"].astype(int)

    t0, t1 = _session_time_span(trials)
    duration_minutes = max((t1 - t0).total_seconds() / 60.0, 1.0)
    width = min(30.0, max(15.0, 13.0 + 0.12 * duration_minutes))
    height = max(12.0, 0.20 * len(targets) + 4.0)

    fig, ax = plt.subplots(figsize=(width, height))

    # Very light phase windows. Real gaps between cycles remain unshaded.
    bounds = phase_bounds_table(trials)
    phase_gray = {
        "fast_poke": "0.97",
        "double_reset": "0.93",
        "reset_burst": "0.97",
        "slow_poke": "0.93",
    }
    for b in bounds.itertuples(index=False):
        ax.axvspan(
            b.start,
            b.end,
            facecolor=phase_gray.get(str(b.phase), "0.96"),
            edgecolor="none",
            zorder=0,
        )

    sc = ax.scatter(
        plot_df["dt_local"],
        plot_df["root_row"],
        c=plot_df["score"],
        cmap=RELIABILITY_CMAP,
        vmin=0,
        vmax=1,
        marker="s",
        s=27,
        linewidths=0,
        zorder=3,
    )

    ax.set_yticks(np.arange(len(targets)))
    ax.set_yticklabels(targets["label"], fontsize=7)
    ax.invert_yaxis()

    # Tile/IOG separators.
    for idx in range(1, len(targets)):
        prev = targets.iloc[idx - 1]
        cur = targets.iloc[idx]
        if cur.io_group != prev.io_group:
            ax.axhline(idx - 0.5, color="black", linewidth=1.8, zorder=4)
        elif cur.tile != prev.tile:
            ax.axhline(idx - 0.5, color="0.82", linewidth=0.6, zorder=4)

    # Cycle starts are strong vertical lines. Phase starts are thin dashed lines.
    starts = cycle_start_table(trials)
    for s in starts.itertuples(index=False):
        ax.axvline(s.dt_local, color="black", linewidth=1.0, alpha=0.65, zorder=2)

    for b in bounds.itertuples(index=False):
        if str(b.phase) != "fast_poke":
            ax.axvline(
                b.start,
                color="0.45",
                linewidth=0.6,
                linestyle="--",
                alpha=0.6,
                zorder=2,
            )

    # Put compact cycle labels above the upper edge.
    for s in starts.itertuples(index=False):
        ax.text(
            s.dt_local,
            1.018,
            f"C{int(s.cycle)}",
            transform=ax.get_xaxis_transform(),
            ha="left",
            va="bottom",
            fontsize=8,
            fontweight="bold",
            clip_on=False,
        )

    # Phase names along the upper edge. Their horizontal widths are true time.
    for b in bounds.itertuples(index=False):
        midpoint = b.start + (b.end - b.start) / 2
        ax.text(
            midpoint,
            1.003,
            PHASE_SHORT.get(str(b.phase), str(b.phase)),
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="bottom",
            fontsize=6.2,
            color="0.30",
            rotation=90,
            clip_on=False,
        )

    # Explicitly label long gaps between complete cycles. In the monitor these
    # are normally the configured --between-cycles wait (30 s by default).
    cycle_windows = (
        trials.groupby("cycle", as_index=False)
        .agg(start=("dt_local", "min"), end=("dt_local", "max"))
        .sort_values("cycle")
        .reset_index(drop=True)
    )
    for i in range(1, len(cycle_windows)):
        prev_end = cycle_windows.loc[i - 1, "end"]
        next_start = cycle_windows.loc[i, "start"]
        gap_s = (next_start - prev_end).total_seconds()
        if gap_s >= 5.0:
            midpoint = prev_end + (next_start - prev_end) / 2
            ax.text(
                midpoint,
                0.992,
                f"{gap_s:.0f}s wait",
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=6.0,
                color="0.35",
                clip_on=False,
            )

    # Time formatting: actual wall-clock spacing is preserved.
    duration = (t1 - t0).total_seconds()
    plot_tz = t0.tzinfo
    if duration <= 6 * 3600:
        locator = mdates.AutoDateLocator(minticks=6, maxticks=14, tz=plot_tz)
        formatter = mdates.DateFormatter("%H:%M:%S", tz=plot_tz)
    elif duration <= 2 * 86400:
        locator = mdates.AutoDateLocator(minticks=6, maxticks=14, tz=plot_tz)
        formatter = mdates.DateFormatter("%m-%d %H:%M", tz=plot_tz)
    else:
        locator = mdates.AutoDateLocator(minticks=6, maxticks=14, tz=plot_tz)
        formatter = mdates.ConciseDateFormatter(locator, tz=plot_tz)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)

    offset_text = t0.strftime("%z")
    date_text = t0.strftime("%Y-%m-%d")
    ax.set_title(
        "Root recovery reliability versus real wall-clock time\n"
        "Horizontal spacing is physical time; within-phase diagonals are sequential root scan order; "
        "blank gaps are idle/between-cycle time",
        pad=50,
    )
    ax.set_xlabel(f"Local time on {date_text} (UTC{offset_text[:3]}:{offset_text[3:]})")
    ax.set_ylabel("Root")

    cbar = fig.colorbar(sc, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label(
        "Reliability score = mean matched-register fraction of post + repeat reads"
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def _lookup(mapping, key):
    if key in mapping:
        return mapping[key]
    skey = str(key)
    if skey in mapping:
        return mapping[skey]
    try:
        ikey = int(key)
        if ikey in mapping:
            return mapping[ikey]
    except Exception:
        pass
    raise KeyError(key)


def _rotate_pixel(pixel_pos, tile_orientation):
    # Same convention used by analysis/plot_hydra_network_anode.py.
    return (
        pixel_pos[0] * tile_orientation[2],
        pixel_pos[1] * tile_orientation[1],
    )


def load_hydra_chip_geometry(geometry_yaml: Path):
    """
    Reproduce the physical ASIC rectangles used by plot_hydra_network_anode.py.

    Returns
    -------
    chip_rects : dict[(tile, chip)] -> dict(minX, maxX, minY, maxY, avgX, avgY)
    tile_rects : dict[tile] -> dict(minX, maxX, minY, maxY)
    """
    with geometry_yaml.open() as f:
        geo = yaml.full_load(f)

    required = [
        "pixel_pitch",
        "chip_channel_to_position",
        "tile_orientations",
        "tile_positions",
        "tpc_centers",
        "tile_indeces",
        "tile_chip_to_io",
    ]
    missing = [k for k in required if k not in geo]
    if missing:
        raise ValueError(
            f"Geometry YAML is missing keys required by Hydra plot geometry: {missing}"
        )

    pixel_pitch = float(geo["pixel_pitch"])
    chip_channel_to_position = geo["chip_channel_to_position"]
    tile_orientations = geo["tile_orientations"]
    tile_positions = geo["tile_positions"]
    tpc_centers = geo["tpc_centers"]
    tile_indeces = geo["tile_indeces"]
    tile_chip_to_io = geo["tile_chip_to_io"]

    base_positions = np.array(
        [list(v) for v in chip_channel_to_position.values()], dtype=float
    )
    xs = base_positions[:, 0] * pixel_pitch
    ys = base_positions[:, 1] * pixel_pitch
    x_size = max(xs) - min(xs) + pixel_pitch
    y_size = max(ys) - min(ys) + pixel_pitch

    nonrouted_v2a_channels = {
        6, 7, 8, 9, 22, 23, 24, 25, 38, 39, 40, 54, 55, 56, 57
    }
    routed_channels = {i for i in range(64) if i not in nonrouted_v2a_channels}

    chip_points = {}

    # The CRS anode plot uses physical tile geometries 1..8 for one IOG.
    # Some geometry YAMLs also contain tiles 9..16 at overlapping projected
    # positions. Drawing both sets here causes duplicate tile labels and the
    # later white ASIC rectangles to cover the colored root rectangles.
    for tile_key, chip_map in tile_chip_to_io.items():
        tile = int(tile_key)
        if tile < 1 or tile > 8:
            continue

        tile_orientation = _lookup(tile_orientations, tile_key)
        tile_position = _lookup(tile_positions, tile_key)
        tile_index = _lookup(tile_indeces, tile_key)
        tpc_center = _lookup(tpc_centers, tile_index[0])

        chips_present = {int(chip) for chip in chip_map.keys()}

        for chip_channel_key, pos in chip_channel_to_position.items():
            chip_channel = int(chip_channel_key)
            chip = chip_channel // 1000
            channel = chip_channel % 1000

            if chip not in chips_present or channel not in routed_channels:
                continue

            x = float(pos[0]) * pixel_pitch + pixel_pitch / 2.0 - x_size / 2.0
            y = float(pos[1]) * pixel_pitch + pixel_pitch / 2.0 - y_size / 2.0

            x, y = _rotate_pixel((x, y), tile_orientation)
            x += float(tile_position[2]) + float(tpc_center[0])
            y += float(tile_position[1]) + float(tpc_center[1])

            chip_points.setdefault((tile, chip), [[], []])
            chip_points[(tile, chip)][0].append(x)
            chip_points[(tile, chip)][1].append(y)

    chip_rects = {}
    for key, (x, y) in chip_points.items():
        if not x or not y:
            continue
        chip_rects[key] = {
            "minX": min(x),
            "maxX": max(x),
            "avgX": (max(x) + min(x)) / 2.0,
            "minY": min(y),
            "maxY": max(y),
            "avgY": (max(y) + min(y)) / 2.0,
        }

    if not chip_rects:
        raise ValueError("No chip rectangles could be built from geometry YAML.")

    tile_rects = {}
    for tile in sorted({tile for tile, _ in chip_rects}):
        rects = [r for (t, _), r in chip_rects.items() if t == tile]
        tile_rects[tile] = {
            "minX": min(r["minX"] for r in rects),
            "maxX": max(r["maxX"] for r in rects),
            "minY": min(r["minY"] for r in rects),
            "maxY": max(r["maxY"] for r in rects),
        }

    return chip_rects, tile_rects


def expected_target_count(session, targets):
    if session.get("targets"):
        return len(session["targets"])
    return len(targets)


def latest_complete_snapshot(trials, expected_targets):
    """
    Choose the latest cycle/phase with a repeat_read for every monitored target.

    If no complete batch exists, fall back to the latest repeat_read per root.
    """
    repeat = trials[trials["step"] == "repeat_read"].copy()
    if repeat.empty:
        raise ValueError("No repeat_read rows available for status snapshot.")

    repeat["root_key"] = repeat.apply(
        lambda r: (int(r.io_group), int(r.io_channel), int(r.chip_id)), axis=1
    )

    grouped = (
        repeat.groupby(["cycle", "phase"], as_index=False)
        .agg(
            n_roots=("root_key", "nunique"),
            end_elapsed=("elapsed_s", "max"),
            end_time=("dt_local", "max"),
        )
        .sort_values("end_elapsed")
    )

    complete = grouped[grouped["n_roots"] >= expected_targets]
    if not complete.empty:
        chosen = complete.iloc[-1]
        cycle = int(chosen["cycle"])
        phase = str(chosen["phase"])
        snap = repeat[
            (repeat["cycle"] == cycle) & (repeat["phase"] == phase)
        ].copy()
        snap = (
            snap.sort_values("elapsed_s")
            .groupby(["io_group", "io_channel", "chip_id"], as_index=False)
            .tail(1)
        )
        return snap, {
            "mode": "complete_batch",
            "cycle": cycle,
            "phase": phase,
            "end_time": chosen["end_time"],
        }

    # Mid-first-cycle fallback.
    snap = (
        repeat.sort_values("elapsed_s")
        .groupby(["io_group", "io_channel", "chip_id"], as_index=False)
        .tail(1)
    )
    return snap, {
        "mode": "latest_per_root",
        "cycle": None,
        "phase": None,
        "end_time": snap["dt_local"].max(),
    }


def status_from_row(row):
    if row is None:
        return STATUS_NOT_TESTED, "Not tested"

    total = float(row["total_registers"]) if pd.notna(row["total_registers"]) else 256.0
    matched = float(row["matched_registers"]) if pd.notna(row["matched_registers"]) else 0.0
    frac = matched / total if total else 0.0

    if bool(row["all_match"]):
        return STATUS_PASS, "256/256 pass"
    if bool(row["any_reply"]):
        return STATUS_PARTIAL, f"Partial ({matched:.0f}/{total:.0f})"
    return STATUS_FAIL, "No reply"


def plot_status_hydra_style(
    trials,
    session,
    targets,
    chip_rects,
    tile_rects,
    io_group,
    output_path,
    dpi,
):
    expected = expected_target_count(session, targets)
    snapshot, meta = latest_complete_snapshot(trials, expected)
    snap_iog = snapshot[snapshot["io_group"] == io_group].copy()

    lookup = {}
    for row in snap_iog.to_dict("records"):
        lookup[(int(row["tile"]), int(row["chip_id"]))] = row

    root_ids = sorted(
        targets[targets["io_group"] == io_group]["chip_id"].unique().tolist()
    )

    all_x = [v for r in chip_rects.values() for v in (r["minX"], r["maxX"])]
    all_y = [v for r in chip_rects.values() for v in (r["minY"], r["maxY"])]
    xmin, xmax = min(all_x), max(all_x)
    ymin, ymax = min(all_y), max(all_y)
    dx = xmax - xmin
    dy = ymax - ymin

    fig, ax = plt.subplots(figsize=(12, 20))
    ax.set_xlabel("X Position [mm]")
    ax.set_ylabel("Y Position [mm]")
    ax.set_xlim(xmin - 0.04 * dx, xmax + 0.04 * dx)
    ax.set_ylim(ymin - 0.04 * dy, ymax + 0.04 * dy)
    ax.set_aspect("equal")

    # Draw familiar anode chip grid and chip IDs.
    for (tile, chip), r in sorted(chip_rects.items()):
        width = r["maxX"] - r["minX"]
        height = r["maxY"] - r["minY"]

        face = "white"
        edge = "0.72"
        lw = 0.35
        alpha = 1.0

        is_root = chip in root_ids
        target_exists = not targets[
            (targets["io_group"] == io_group)
            & (targets["tile"] == tile)
            & (targets["chip_id"] == chip)
        ].empty

        if io_group == 6 and tile == 5 and not target_exists:
            # Known intentionally excluded IOG6 tile5.
            if is_root:
                face = STATUS_SKIPPED
                edge = "0.55"
                lw = 0.8
        elif is_root and target_exists:
            row = lookup.get((tile, chip))
            face, _ = status_from_row(row)
            edge = "0.25"
            lw = 0.9

        ax.add_patch(
            Rectangle(
                (r["minX"], r["minY"]),
                width,
                height,
                facecolor=face,
                edgecolor=edge,
                linewidth=lw,
                alpha=alpha,
            )
        )
        ax.text(
            r["avgX"],
            r["avgY"],
            str(chip),
            ha="center",
            va="center",
            fontsize=5.3 if not is_root else 7.0,
            fontweight="bold" if is_root else "normal",
            color="black",
        )

    # Tile borders and labels.
    for tile, r in sorted(tile_rects.items()):
        width = r["maxX"] - r["minX"]
        height = r["maxY"] - r["minY"]
        ax.add_patch(
            Rectangle(
                (r["minX"], r["minY"]),
                width,
                height,
                fill=False,
                edgecolor="black",
                linewidth=1.4,
            )
        )
        ax.text(
            (r["minX"] + r["maxX"]) / 2.0,
            r["maxY"] + 0.012 * dy,
            f"Tile {tile}",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )

        if io_group == 6 and tile == 5:
            any_tile5_target = not targets[
                (targets["io_group"] == 6) & (targets["tile"] == 5)
            ].empty
            if not any_tile5_target:
                ax.text(
                    (r["minX"] + r["maxX"]) / 2.0,
                    (r["minY"] + r["maxY"]) / 2.0,
                    "SKIPPED",
                    ha="center",
                    va="center",
                    fontsize=13,
                    fontweight="bold",
                    color="0.35",
                    bbox=dict(facecolor="white", edgecolor="0.5", alpha=0.8),
                )

    if meta["mode"] == "complete_batch":
        phase = PHASE_LABEL.get(meta["phase"], meta["phase"])
        subtitle = f"Cycle {meta['cycle']} — {phase}"
    else:
        subtitle = "Latest available repeat_read per root (no complete batch yet)"

    if pd.notna(meta["end_time"]):
        subtitle += f" — through {meta['end_time'].strftime('%Y-%m-%d %H:%M:%S %z')}"

    ax.set_title(
        f"Root recovery status — IO Group {io_group}\n{subtitle}",
        fontsize=15,
    )

    legend = [
        Patch(facecolor=STATUS_PASS, edgecolor="0.25", label="Perfect 256/256 repeat read"),
        Patch(facecolor=STATUS_PARTIAL, edgecolor="0.25", label="Partial reply"),
        Patch(facecolor=STATUS_FAIL, edgecolor="0.25", label="No reply"),
        Patch(facecolor=STATUS_SKIPPED, edgecolor="0.55", label="Intentionally skipped"),
    ]
    ax.legend(
        handles=legend,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.035),
        ncol=2,
        frameon=False,
        fontsize=9,
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)

    return meta


def resolve_geometry(args_geometry, session, log_folder):
    if args_geometry is not None:
        p = args_geometry.expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Geometry YAML not found: {p}")
        return p

    candidates = []

    repo_root = session.get("repo_root")
    if repo_root:
        candidates.append(Path(repo_root) / DEFAULT_GEOMETRY)

    # A normal log path is:
    # <repo>/root_recovery_logs/<session>/
    if len(log_folder.parents) >= 2:
        candidates.append(log_folder.parents[1] / DEFAULT_GEOMETRY)

    candidates.append(Path.cwd() / DEFAULT_GEOMETRY)
    candidates.append(Path(__file__).resolve().parent / DEFAULT_GEOMETRY)

    seen = set()
    for c in candidates:
        c = c.expanduser().resolve()
        if c in seen:
            continue
        seen.add(c)
        if c.exists():
            return c

    attempted = "\n  ".join(str(c) for c in seen)
    raise FileNotFoundError(
        "Could not locate the Hydra/anode geometry YAML.\n"
        f"Searched:\n  {attempted}\n"
        "Pass it explicitly with --geometry-yaml."
    )


def summarize_run(trials, targets):
    full = trials[
        (trials["step"].isin(["post_read", "repeat_read"]))
        & trials["all_match"]
    ].copy()
    full_keys = set(
        (int(r.io_group), int(r.io_channel), int(r.chip_id))
        for r in full.itertuples(index=False)
    )
    return len(full_keys), len(targets)


def main():
    args = parse_args()
    log_folder = args.log_folder.expanduser().resolve()
    if not log_folder.is_dir():
        raise NotADirectoryError(f"Log folder does not exist: {log_folder}")

    session = load_session(log_folder)
    trials = load_trials(log_folder)
    targets = targets_dataframe(session, trials)
    scores = build_scores(trials)

    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else log_folder / "plots"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    geometry_yaml = resolve_geometry(args.geometry_yaml, session, log_folder)
    chip_rects, tile_rects = load_hydra_chip_geometry(geometry_yaml)

    selected_phase = PHASE_CLI_MAP[args.by_test_phase]

    if selected_phase is None:
        aligned_name = "root_recovery_reliability_by_test.png"
    else:
        aligned_name = (
            "root_recovery_reliability_by_test_"
            f"{selected_phase}.png"
        )

    outputs = {
        "aligned": output_dir / aligned_name,
        "time": output_dir / "root_recovery_reliability_vs_time.png",
        "iog5": output_dir / "root_recovery_status_iog5.png",
        "iog6": output_dir / "root_recovery_status_iog6.png",
    }

    plot_reliability_by_test(
        trials,
        scores,
        targets,
        outputs["aligned"],
        args.dpi,
        selected_phase=selected_phase,
    )
    plot_reliability_vs_time(
        trials, scores, targets, outputs["time"], args.dpi
    )

    meta5 = plot_status_hydra_style(
        trials,
        session,
        targets,
        chip_rects,
        tile_rects,
        5,
        outputs["iog5"],
        args.dpi,
    )
    meta6 = plot_status_hydra_style(
        trials,
        session,
        targets,
        chip_rects,
        tile_rects,
        6,
        outputs["iog6"],
        args.dpi,
    )

    ever_good, total = summarize_run(trials, targets)
    cycles = sorted(
        int(x) for x in trials["cycle"].dropna().unique()
    )

    print(f"plot_root_recovery.py {SCRIPT_VERSION}")
    print(f"Log folder: {log_folder}")
    print(f"Geometry:   {geometry_yaml}")
    print(f"Targets:    {total}")
    print(f"Cycles:     {cycles[0]}..{cycles[-1]}" if cycles else "Cycles: none")
    if selected_phase is None:
        print("By-test plot phase: all")
    else:
        print(
            "By-test plot phase: "
            f"{PHASE_LABEL.get(selected_phase, selected_phase)} ({selected_phase})"
        )
    print(
        f"Ever produced a perfect post/repeat full read: "
        f"{ever_good}/{total} ({100.0 * ever_good / total:.1f}%)"
        if total
        else "No targets found."
    )
    if meta5["mode"] == "complete_batch":
        print(
            "Status snapshot batch: "
            f"cycle {meta5['cycle']} / {meta5['phase']}"
        )
    else:
        print("Status snapshot batch: latest available per root")

    print("\nSaved:")
    for path in outputs.values():
        print(f"  {path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
