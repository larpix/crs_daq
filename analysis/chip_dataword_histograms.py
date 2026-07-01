#!/usr/bin/env python3
"""
Plot raw ADC/dataword histograms for one selected LArPix chip, optionally one
selected pixel/channel.

This is meant as a lightweight companion to plot_metric_anode.py. Instead of
making an anode map of channel-level mean/std/rate, this script looks at the
raw dataword distribution itself.

Typical use, whole chip:

    python chip_dataword_histograms.py \
        --filename packets.h5 \
        --io_group 1 --io_channel 2 --chip_id 11 \
        --output_dir chip_dataword_hists

Typical use, one pixel/channel only:

    python chip_dataword_histograms.py \
        --filename packets.h5 \
        --io_group 1 --io_channel 2 --chip_id 11 --channel_id 37 \
        --output_dir chip_dataword_hists

Outputs:
  * CSV summary with one row per channel.
  * If --channel_id is given: one raw dataword histogram for that channel.
  * If --channel_id is not given: a combined chip histogram and an 8x8 grid of
    per-channel histograms, where subplot position is just channel_id order,
    not the physical pixel XY geometry.

Notes:
  * Selection follows the same packet convention as the original plotting code:
    packet_type == 0 and valid_parity == 1 are used for ADC/dataword packets.
  * YAML geometry is not needed unless you later want the per-channel plots
    placed at physical pixel positions.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Optional

import h5py
import matplotlib.pyplot as plt
import numpy as np


DEFAULT_CHUNK_SIZE = 2_000_000
N_CHANNELS = 64


def require_fields(dtype: np.dtype, fields: list[str]) -> None:
    missing = [field for field in fields if dtype.names is None or field not in dtype.names]
    if missing:
        raise KeyError("packets dataset is missing required field(s): " + ", ".join(missing))


def update_minmax(old_min: Optional[float], old_max: Optional[float], values: np.ndarray) -> tuple[Optional[float], Optional[float]]:
    if values.size == 0:
        return old_min, old_max
    vmin = float(np.min(values))
    vmax = float(np.max(values))
    if old_min is None or vmin < old_min:
        old_min = vmin
    if old_max is None or vmax > old_max:
        old_max = vmax
    return old_min, old_max


def read_selected_datawords(
    filename: str | Path,
    io_group: int,
    io_channel: int,
    chip_id: int,
    channel_id: Optional[int] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_selected_packets: Optional[int] = None,
) -> tuple[dict[int, np.ndarray], Optional[float]]:
    """
    Return raw dataword arrays grouped by channel_id for one chip.

    max_selected_packets is applied after the chip/channel selection, not to the
    entire file. This makes quick tests less dependent on file ordering.
    """
    filename = Path(filename)

    if channel_id is not None and not (0 <= channel_id < N_CHANNELS):
        raise ValueError("channel_id must be between 0 and 63")

    pieces: dict[int, list[np.ndarray]] = {ch: [] for ch in range(N_CHANNELS)}
    selected_total = 0

    # The original code estimates livetime from packet_type == 4 timestamps.
    ts_min: Optional[float] = None
    ts_max: Optional[float] = None

    with h5py.File(filename, "r") as h5:
        if "packets" not in h5:
            raise KeyError(f"No 'packets' dataset found in {filename}")

        packets = h5["packets"]
        require_fields(
            packets.dtype,
            [
                "packet_type",
                "valid_parity",
                "io_group",
                "io_channel",
                "chip_id",
                "channel_id",
                "dataword",
                "timestamp",
            ],
        )

        n_packets = packets.shape[0]
        for start in range(0, n_packets, chunk_size):
            stop = min(start + chunk_size, n_packets)
            p = packets[start:stop]

            ts_mask = p["packet_type"] == 4
            if np.any(ts_mask):
                ts = p["timestamp"][ts_mask].astype(np.float64)
                ts_min, ts_max = update_minmax(ts_min, ts_max, ts)

            mask = (
                (p["packet_type"] == 0)
                & (p["valid_parity"] == 1)
                & (p["io_group"] == io_group)
                & (p["io_channel"] == io_channel)
                & (p["chip_id"] == chip_id)
            )
            if channel_id is not None:
                mask &= p["channel_id"] == channel_id

            if not np.any(mask):
                continue

            channels = p["channel_id"][mask].astype(np.int64)
            adcs = p["dataword"][mask].astype(np.float64)

            good = (channels >= 0) & (channels < N_CHANNELS)
            channels = channels[good]
            adcs = adcs[good]

            if adcs.size == 0:
                continue

            if max_selected_packets is not None and max_selected_packets >= 0:
                remaining = max_selected_packets - selected_total
                if remaining <= 0:
                    break
                channels = channels[:remaining]
                adcs = adcs[:remaining]

            for ch in np.unique(channels):
                ch_int = int(ch)
                pieces[ch_int].append(adcs[channels == ch])

            selected_total += int(adcs.size)
            if max_selected_packets is not None and max_selected_packets >= 0 and selected_total >= max_selected_packets:
                break

    data_by_channel: dict[int, np.ndarray] = {}
    for ch, parts in pieces.items():
        if parts:
            data_by_channel[ch] = np.concatenate(parts)
        else:
            data_by_channel[ch] = np.array([], dtype=np.float64)

    livetime = None
    if ts_min is not None and ts_max is not None and ts_max > ts_min:
        livetime = ts_max - ts_min

    return data_by_channel, livetime


def all_selected_values(data_by_channel: dict[int, np.ndarray]) -> np.ndarray:
    parts = [v for v in data_by_channel.values() if v.size]
    if not parts:
        return np.array([], dtype=np.float64)
    return np.concatenate(parts)


def make_integerish_bins(
    values: np.ndarray,
    adc_min: Optional[float],
    adc_max: Optional[float],
    bin_width: float,
) -> np.ndarray:
    if values.size == 0:
        raise ValueError("No selected packets found; cannot make histogram bins.")
    if bin_width <= 0:
        raise ValueError("bin_width must be positive")

    lo = math.floor(float(np.min(values))) if adc_min is None else float(adc_min)
    hi = math.ceil(float(np.max(values))) if adc_max is None else float(adc_max)
    if hi < lo:
        raise ValueError("adc_max must be >= adc_min")
    if hi == lo:
        lo -= bin_width
        hi += bin_width

    # Center integer ADC values in bins when bin_width=1.
    return np.arange(lo - 0.5 * bin_width, hi + 1.5 * bin_width, bin_width)


def channel_summary_rows(data_by_channel: dict[int, np.ndarray], livetime: Optional[float]) -> list[dict[str, float | int | str]]:
    rows = []
    for ch in range(N_CHANNELS):
        values = data_by_channel[ch]
        n = int(values.size)
        if n:
            row = {
                "channel_id": ch,
                "n_packets": n,
                "adc_mean": float(np.mean(values)),
                "adc_std": float(np.std(values)),
                "adc_min": float(np.min(values)),
                "adc_p01": float(np.percentile(values, 1)),
                "adc_p05": float(np.percentile(values, 5)),
                "adc_median": float(np.percentile(values, 50)),
                "adc_p95": float(np.percentile(values, 95)),
                "adc_p99": float(np.percentile(values, 99)),
                "adc_max": float(np.max(values)),
                "rate_like_original": "" if livetime is None else float(n / (livetime + 1e-9)),
            }
        else:
            row = {
                "channel_id": ch,
                "n_packets": 0,
                "adc_mean": "",
                "adc_std": "",
                "adc_min": "",
                "adc_p01": "",
                "adc_p05": "",
                "adc_median": "",
                "adc_p95": "",
                "adc_p99": "",
                "adc_max": "",
                "rate_like_original": "",
            }
        rows.append(row)
    return rows


def save_summary_csv(rows: list[dict[str, float | int | str]], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "channel_id",
        "n_packets",
        "adc_mean",
        "adc_std",
        "adc_min",
        "adc_p01",
        "adc_p05",
        "adc_median",
        "adc_p95",
        "adc_p99",
        "adc_max",
        "rate_like_original",
    ]
    with output_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def plot_single_channel_hist(
    values: np.ndarray,
    bins: np.ndarray,
    output_png: Path,
    title: str,
    log_y: bool,
) -> None:
    if values.size == 0:
        raise ValueError("No packets found for the selected channel.")

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.hist(values, bins=bins)
    ax.set_xlabel("ADC dataword")
    ax.set_ylabel("Packet count")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    if log_y:
        ax.set_yscale("log")

    text = (
        f"N = {values.size}\n"
        f"mean = {np.mean(values):.3f}\n"
        f"std = {np.std(values):.3f}\n"
        f"median = {np.median(values):.3f}"
    )
    ax.text(0.98, 0.98, text, transform=ax.transAxes, ha="right", va="top", bbox=dict(boxstyle="round", alpha=0.15))
    fig.tight_layout()
    fig.savefig(output_png, dpi=160)
    plt.close(fig)


def plot_combined_chip_hist(
    data_by_channel: dict[int, np.ndarray],
    bins: np.ndarray,
    output_png: Path,
    title: str,
    log_y: bool,
    overlay_channels: bool,
) -> None:
    values = all_selected_values(data_by_channel)
    if values.size == 0:
        raise ValueError("No selected chip packets found.")

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 6))

    ax.hist(values, bins=bins, histtype="stepfilled", alpha=0.35, label="All selected chip packets")

    if overlay_channels:
        for ch in range(N_CHANNELS):
            ch_values = data_by_channel[ch]
            if ch_values.size:
                ax.hist(ch_values, bins=bins, histtype="step", linewidth=0.7, alpha=0.35)

    ax.set_xlabel("ADC dataword")
    ax.set_ylabel("Packet count")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    if log_y:
        ax.set_yscale("log")
    ax.legend(loc="best")

    active_channels = sum(1 for v in data_by_channel.values() if v.size)
    text = (
        f"N = {values.size}\n"
        f"active channels = {active_channels}\n"
        f"mean = {np.mean(values):.3f}\n"
        f"std = {np.std(values):.3f}"
    )
    ax.text(0.98, 0.98, text, transform=ax.transAxes, ha="right", va="top", bbox=dict(boxstyle="round", alpha=0.15))
    fig.tight_layout()
    fig.savefig(output_png, dpi=160)
    plt.close(fig)


def plot_channel_grid(
    data_by_channel: dict[int, np.ndarray],
    bins: np.ndarray,
    output_png: Path,
    title: str,
    log_y: bool,
    max_title_channels: int = N_CHANNELS,
) -> None:
    values = all_selected_values(data_by_channel)
    if values.size == 0:
        raise ValueError("No selected chip packets found.")

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(8, 8, figsize=(22, 18), sharex=True, sharey=False)

    for ch in range(N_CHANNELS):
        ax = axes[ch // 8, ch % 8]
        ch_values = data_by_channel[ch]
        if ch_values.size:
            ax.hist(ch_values, bins=bins)
            if log_y:
                ax.set_yscale("log")
            mean = np.mean(ch_values)
            std = np.std(ch_values)
            ax.set_title(f"ch {ch}\nN={ch_values.size}, μ={mean:.1f}, σ={std:.1f}", fontsize=8)
        else:
            ax.set_title(f"ch {ch}\nempty", fontsize=8)
            ax.text(0.5, 0.5, "empty", transform=ax.transAxes, ha="center", va="center", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.2)

    fig.suptitle(title + "\nSubplot positions are channel_id order, not physical XY geometry.", fontsize=16)
    fig.supxlabel("ADC dataword")
    fig.supylabel("Packet count")
    fig.tight_layout(rect=[0, 0.02, 1, 0.96])
    fig.savefig(output_png, dpi=160)
    plt.close(fig)


def plot_channel_summary(rows: list[dict[str, float | int | str]], output_png: Path, title: str) -> None:
    output_png.parent.mkdir(parents=True, exist_ok=True)
    ch = np.array([int(r["channel_id"]) for r in rows])
    n = np.array([int(r["n_packets"]) for r in rows])

    def numeric(field: str) -> np.ndarray:
        out = []
        for r in rows:
            value = r[field]
            out.append(np.nan if value == "" else float(value))
        return np.array(out, dtype=float)

    mean = numeric("adc_mean")
    std = numeric("adc_std")
    median = numeric("adc_median")

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    axes[0].plot(ch, n, marker=".", linestyle="none")
    axes[0].set_ylabel("Packets")
    axes[0].set_yscale("log" if np.nanmax(n) > 0 else "linear")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(ch, mean, marker=".", linestyle="none", label="mean")
    axes[1].plot(ch, median, marker="x", linestyle="none", label="median")
    axes[1].set_ylabel("ADC")
    axes[1].legend(loc="best")
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(ch, std, marker=".", linestyle="none")
    axes[2].set_xlabel("channel_id")
    axes[2].set_ylabel("ADC std")
    axes[2].grid(True, alpha=0.3)

    fig.suptitle(title)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(output_png, dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot raw dataword histograms for one selected LArPix chip or one channel."
    )
    parser.add_argument("--filename", required=True, help="Input HDF5 file containing a packets dataset")
    parser.add_argument("--io_group", required=True, type=int)
    parser.add_argument("--io_channel", required=True, type=int)
    parser.add_argument("--chip_id", required=True, type=int)
    parser.add_argument("--channel_id", type=int, default=None, help="Optional channel/pixel id, 0-63")
    parser.add_argument("--output_dir", default="chip_dataword_hists")
    parser.add_argument("--chunk_size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument(
        "--max_selected_packets",
        type=int,
        default=-1,
        help="Optional cap after selecting the requested chip/channel; -1 means no cap",
    )
    parser.add_argument("--adc_min", type=float, default=None, help="Lower ADC value for histogram range")
    parser.add_argument("--adc_max", type=float, default=None, help="Upper ADC value for histogram range")
    parser.add_argument("--bin_width", type=float, default=1.0, help="Histogram bin width in ADC units")
    parser.add_argument("--log_y", action="store_true", help="Use log scale for histogram y axes")
    parser.add_argument(
        "--overlay_channels",
        action="store_true",
        help="On the combined chip histogram, also draw thin per-channel overlays. This can be busy.",
    )
    parser.add_argument("--no_grid", action="store_true", help="Do not make the 8x8 per-channel histogram grid")
    parser.add_argument("--no_summary_plot", action="store_true", help="Do not make the channel summary plot")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    tag = f"iog{args.io_group}_ioc{args.io_channel}_chip{args.chip_id}"
    if args.channel_id is not None:
        tag += f"_ch{args.channel_id}"

    max_selected = None if args.max_selected_packets is None or args.max_selected_packets < 0 else args.max_selected_packets

    print("Reading selected packets...")
    data_by_channel, livetime = read_selected_datawords(
        filename=args.filename,
        io_group=args.io_group,
        io_channel=args.io_channel,
        chip_id=args.chip_id,
        channel_id=args.channel_id,
        chunk_size=args.chunk_size,
        max_selected_packets=max_selected,
    )

    selected_values = all_selected_values(data_by_channel)
    if selected_values.size == 0:
        raise SystemExit(
            "No valid data packets found for selection: "
            f"io_group={args.io_group}, io_channel={args.io_channel}, "
            f"chip_id={args.chip_id}, channel_id={args.channel_id}"
        )

    bins = make_integerish_bins(selected_values, args.adc_min, args.adc_max, args.bin_width)

    rows = channel_summary_rows(data_by_channel, livetime)
    csv_path = output_dir / f"{tag}_dataword_summary.csv"
    save_summary_csv(rows, csv_path)
    print(f"Saved {csv_path}")

    base_title = f"io_group={args.io_group}, io_channel={args.io_channel}, chip_id={args.chip_id}"

    if args.channel_id is not None:
        channel_values = data_by_channel[args.channel_id]
        out = output_dir / f"{tag}_dataword_hist.png"
        plot_single_channel_hist(
            channel_values,
            bins,
            out,
            title=base_title + f", channel_id={args.channel_id}",
            log_y=args.log_y,
        )
        print(f"Saved {out}")
    else:
        out = output_dir / f"{tag}_dataword_hist_all_packets.png"
        plot_combined_chip_hist(
            data_by_channel,
            bins,
            out,
            title=base_title + ", all chip packets",
            log_y=args.log_y,
            overlay_channels=args.overlay_channels,
        )
        print(f"Saved {out}")

        if not args.no_grid:
            out = output_dir / f"{tag}_dataword_hist_by_channel_grid.png"
            plot_channel_grid(
                data_by_channel,
                bins,
                out,
                title=base_title + ", dataword histogram by channel",
                log_y=args.log_y,
            )
            print(f"Saved {out}")

    if not args.no_summary_plot:
        out = output_dir / f"{tag}_dataword_channel_summary.png"
        plot_channel_summary(rows, out, title=base_title + ", channel summary")
        print(f"Saved {out}")

    active_channels = sum(1 for v in data_by_channel.values() if v.size)
    print("Done.")
    print(f"Selected packets: {selected_values.size}")
    print(f"Active channels: {active_channels}/64")
    if livetime is not None:
        print(f"Livetime-like timestamp span from packet_type==4: {livetime}")


if __name__ == "__main__":
    main()
