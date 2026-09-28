import argparse
import json
import math
import numpy as np
from matplotlib import pyplot as plt


def get_io_group(config):
    """
    Extract io_group from meta.ASIC_ID.

    Example:
        ASIC_ID = "6-8-38"
        -> io_group = 6
    """
    try:
        return int(config["meta"]["ASIC_ID"].split("-")[0])
    except (KeyError, ValueError, IndexError, AttributeError):
        return None


def main(*files, output="threshold_global_by_iogroup.png", **kwargs):

    thresholds_by_iogroup = {}
    missing_threshold = 0
    missing_iogroup = 0
    total = 0

    for file in files:
        try:
            with open(file, "r") as f:
                config = json.load(f)
        except Exception as e:
            print(f"Could not read {file}: {e}")
            continue

        io_group = get_io_group(config)

        if io_group is None:
            missing_iogroup += 1
            print(f"Could not determine io_group: {file}")
            continue

        try:
            threshold = int(config["threshold_global"])
        except (KeyError, TypeError, ValueError):
            missing_threshold += 1
            continue

        thresholds_by_iogroup.setdefault(io_group, []).append(threshold)
        total += 1

    if not thresholds_by_iogroup:
        print("No valid threshold_global values found.")
        return

    # ------------------------------------------------------------
    # Print summary
    # ------------------------------------------------------------

    print()
    print(f"Valid chips:              {total}")
    print(f"Missing threshold_global: {missing_threshold}")
    print(f"Missing io_group:         {missing_iogroup}")
    print()

    for io_group in sorted(thresholds_by_iogroup):
        values = np.array(thresholds_by_iogroup[io_group])

        print(
            f"IO group {io_group}: "
            f"N={len(values)}, "
            f"mean={np.mean(values):.2f}, "
            f"median={np.median(values):.1f}, "
            f"min={np.min(values)}, "
            f"max={np.max(values)}"
        )

        for value in sorted(set(values)):
            print(f"    {value}: {np.sum(values == value)}")

    # ------------------------------------------------------------
    # Common histogram bins
    # ------------------------------------------------------------

    all_values = np.concatenate(
        [np.array(v) for v in thresholds_by_iogroup.values()]
    )

    min_threshold = int(np.min(all_values))
    max_threshold = int(np.max(all_values))

    # Integer-centered bins:
    # threshold 32 occupies [31.5, 32.5)
    bins = np.arange(
        min_threshold - 0.5,
        max_threshold + 1.5,
        1
    )

    io_groups = sorted(thresholds_by_iogroup.keys())

    # Usually 8 IO groups -> 2 x 4
    ncols = 4
    nrows = math.ceil(len(io_groups) / ncols)

    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(16, 4.5 * nrows),
        sharex=True,
        sharey=True,
    )

    axes = np.atleast_1d(axes).flatten()

    # Determine common Y scale
    max_bin_count = 0
    histograms = {}

    for io_group in io_groups:
        values = np.array(thresholds_by_iogroup[io_group])
        counts, _ = np.histogram(values, bins=bins)
        histograms[io_group] = counts
        max_bin_count = max(max_bin_count, np.max(counts))

    # ------------------------------------------------------------
    # Plot each IO group
    # ------------------------------------------------------------

    for ax, io_group in zip(axes, io_groups):

        values = np.array(thresholds_by_iogroup[io_group])

        ax.hist(
            values,
            bins=bins,
            edgecolor="black",
            linewidth=0.7,
        )

        mean = np.mean(values)
        median = np.median(values)

        ax.axvline(
            mean,
            linestyle="--",
            linewidth=1.5,
            label=f"mean = {mean:.1f}",
        )

        ax.set_title(f"IO Group {io_group}  —  {len(values)} chips")

        ax.grid(alpha=0.25)

        ax.text(
            0.97,
            0.95,
            f"mean = {mean:.2f}\n"
            f"median = {median:.1f}\n"
            f"range = {np.min(values)}–{np.max(values)}",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=10,
        )

        ax.set_ylim(0, max_bin_count * 1.12)

    # Hide unused panels
    for ax in axes[len(io_groups):]:
        ax.set_visible(False)

    # Labels only on outside panels
    for ax in axes:
        if ax.get_visible():
            ax.set_xlabel("Global threshold DAC")
            ax.set_ylabel("Chip count")

    # Use integer-ish tick spacing
    tick_start = 5 * (min_threshold // 5)
    tick_end = 5 * math.ceil(max_threshold / 5)

    for ax in axes:
        if ax.get_visible():
            ax.set_xticks(
                np.arange(tick_start, tick_end + 1, 5)
            )

    fig.suptitle(
        "2x2 Run 3 — Global Threshold Distribution by IO Group",
        fontsize=18,
    )

    fig.tight_layout(rect=[0, 0, 1, 0.95])

    fig.savefig(
        output,
        dpi=180,
        bbox_inches="tight",
    )

    print()
    print(f"Saved plot to: {output}")

    # ------------------------------------------------------------
    # Overlay plot: all IO groups
    # ------------------------------------------------------------

    fig_overlay, ax = plt.subplots(figsize=(9, 7))

    for io_group in io_groups:
        values = np.array(thresholds_by_iogroup[io_group])

        ax.hist(
            values,
            bins=bins,
            histtype="step",
            linewidth=1.8,
            label=f"io_group = {io_group}",
        )

    ax.set_yscale("log")

    ax.set_xlabel("threshold_global", fontsize=14)
    ax.set_ylabel("chip count", fontsize=14)

    ax.grid(alpha=0.25)

    ax.legend(
        fontsize=11,
        loc="upper right",
    )

    fig_overlay.tight_layout()

    overlay_output = "threshold_global_overlay.png"

    fig_overlay.savefig(
        overlay_output,
        dpi=180,
        bbox_inches="tight",
    )

    print(f"Saved overlay plot to: {overlay_output}")

if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Plot threshold_global distributions by IO group."
    )

    parser.add_argument(
        "input_files",
        nargs="+",
        help="ASIC configuration JSON files",
    )

    parser.add_argument(
        "-o",
        "--output",
        default="threshold_global_by_iogroup.png",
        help="Output PNG filename",
    )

    args = parser.parse_args()

    main(
        *args.input_files,
        output=args.output,
    )
