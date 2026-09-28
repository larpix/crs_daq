"""
Compare pixel trim DAC distributions by IO group for two sets of
ASIC configuration files.

Example:
    python plot_pixel_trim_dist_v3.py \
        --set-a configs_run3a/*.json --label-a "Run 3A" \
        --set-b configs_run3b/*.json --label-b "Run 3B"
"""

import argparse
import json
import math
import re
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch


# Pixel trim DAC is intrinsically 0--31
BINS = np.arange(-0.5, 32.5, 1)

# Visual style for the two configuration sets
SET_STYLES = [
    dict(color="tab:blue", linestyle="-", hatch=None, alpha=0.45),
    dict(color="tab:red", linestyle="--", hatch=None, alpha=1.0),
]


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


def safe_name(label):
    """Turn a legend label into something usable in a filename."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "set"


# ================================================================
# Reading one set of configuration files
# ================================================================

def load_config_set(files, label, ignore_csa=False):
    """
    Read a list of configuration files and return a dict with the
    trims and chip counts per IO group plus diagnostic counters.
    """

    result = dict(
        label=label,
        trims_by_iogroup={},
        chips_by_iogroup={},
        low_trim_chips=[],
        high_trim_chips=[],
        missing_trim=0,
        missing_iogroup=0,
        bad_trim=0,
        skipped_no_active_channels=0,
    )

    trims_by_iogroup = result["trims_by_iogroup"]
    chips_by_iogroup = result["chips_by_iogroup"]

    for file in files:

        try:
            with open(file, "r") as f:
                config = json.load(f)
        except Exception as e:
            print(f"[{label}] Could not read {file}: {e}")
            continue

        # --------------------------------------------------------
        # IO group
        # --------------------------------------------------------

        io_group = get_io_group(config)

        if io_group is None:
            result["missing_iogroup"] += 1
            print(f"[{label}] Could not determine io_group: {file}")
            continue

        # --------------------------------------------------------
        # Pixel trim DAC
        # --------------------------------------------------------

        try:
            ptd = np.array(config["pixel_trim_dac"], dtype=int)
        except (KeyError, TypeError, ValueError):
            result["missing_trim"] += 1
            print(f"[{label}] Missing pixel_trim_dac: {file}")
            continue

        if len(ptd) != 64:
            print(
                f"[{label}] Unexpected pixel_trim_dac length "
                f"({len(ptd)}): {file}"
            )
            continue

        if np.any(ptd < 0) or np.any(ptd > 31):
            result["bad_trim"] += 1
            print(f"[{label}] Pixel trim outside [0,31]: {file}\n{ptd}")

        # --------------------------------------------------------
        # Active channels: csa_enable AND NOT channel_mask
        # --------------------------------------------------------

        if ignore_csa:
            active = np.ones(64, dtype=bool)
        else:
            try:
                csa = np.array(config["csa_enable"], dtype=bool)
                channel_mask = np.array(config["channel_mask"], dtype=bool)
                active = np.logical_and(csa, np.logical_not(channel_mask))
            except (KeyError, TypeError, ValueError):
                # If CSA information isn't available, assume all
                # channels are active.
                active = np.ones(64, dtype=bool)

        if not np.any(active):
            result["skipped_no_active_channels"] += 1
            continue

        active_trims = ptd[active]

        # --------------------------------------------------------
        # Store values by IO group
        # --------------------------------------------------------

        trims_by_iogroup.setdefault(io_group, []).extend(
            active_trims.tolist()
        )
        chips_by_iogroup[io_group] = chips_by_iogroup.get(io_group, 0) + 1

        # --------------------------------------------------------
        # Low/high-trim diagnostics
        # --------------------------------------------------------

        try:
            chip_key = config["meta"]["CHIP_KEY"]
        except KeyError:
            chip_key = file

        threshold_global = config.get("threshold_global", "unknown")

        if np.percentile(active_trims, 80) < 4:
            print(
                f"[{label}] Low Trims!!", chip_key, active_trims,
                "tdac =", threshold_global,
            )
            result["low_trim_chips"].append(chip_key)

        if np.percentile(active_trims, 40) > 20:
            print(
                f"[{label}] High Trims!!", chip_key, active_trims,
                "tdac =", threshold_global,
            )
            result["high_trim_chips"].append(chip_key)

    # Convert lists to arrays once, for convenience later
    for io_group in trims_by_iogroup:
        trims_by_iogroup[io_group] = np.array(trims_by_iogroup[io_group])

    return result


# ================================================================
# Text summary for one set
# ================================================================

def print_summary(result):

    label = result["label"]
    trims_by_iogroup = result["trims_by_iogroup"]
    chips_by_iogroup = result["chips_by_iogroup"]
    io_groups = sorted(trims_by_iogroup.keys())

    print()
    print("==========================================")
    print(f"Pixel trim summary — {label}")
    print("==========================================")
    print()

    total_channels = sum(len(v) for v in trims_by_iogroup.values())
    total_chips = sum(chips_by_iogroup.values())

    print(f"IO groups:                  {io_groups}")
    print(f"Valid chips:                {total_chips}")
    print(f"Active channels:            {total_channels}")
    print(f"Missing pixel_trim_dac:     {result['missing_trim']}")
    print(f"Missing io_group:           {result['missing_iogroup']}")
    print(f"Configs with bad trim DAC:  {result['bad_trim']}")
    print(
        f"Skipped chips (no active):  "
        f"{result['skipped_no_active_channels']}"
    )
    print()

    for io_group in io_groups:
        values = trims_by_iogroup[io_group]
        print(
            f"IO group {io_group}: "
            f"{chips_by_iogroup[io_group]} chips, "
            f"{len(values)} channels, "
            f"mean={np.mean(values):.2f}, "
            f"median={np.median(values):.1f}, "
            f"std={np.std(values):.2f}"
        )

    print()

    if trims_by_iogroup:
        all_trims = np.concatenate(list(trims_by_iogroup.values()))
        vals, _ = np.histogram(all_trims, bins=BINS)

        print(f"Global pixel trim distribution ({label}):")
        for trim_dac, count in enumerate(vals):
            print(f"{trim_dac}: {count}")
        print()
        print(",".join(str(v) for v in vals))
        print()

    print("Number of low trim chips:", len(result["low_trim_chips"]))
    print("Number of high trim chips:", len(result["high_trim_chips"]))

    out_name = f"low_trim_chips_{safe_name(label)}.json"
    with open(out_name, "w") as f:
        json.dump(result["low_trim_chips"], f, indent=4)
    print(f"Saved low-trim chip list to: {out_name}")


# ================================================================
# Figure 1: one panel per IO group, both sets overlaid
# ================================================================

def plot_by_iogroup(results, title, outfile, normalize=False):

    io_groups = sorted(
        set().union(*(r["trims_by_iogroup"].keys() for r in results))
    )

    ncols = 4
    nrows = math.ceil(len(io_groups) / ncols)

    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(16, 4.8 * nrows),
        sharex=True, sharey=True,
    )
    axes = np.atleast_1d(axes).flatten()

    # Common Y maximum across all panels and both sets
    max_bin_count = 0
    for r in results:
        for values in r["trims_by_iogroup"].values():
            counts, _ = np.histogram(values, bins=BINS, density=normalize)
            max_bin_count = max(max_bin_count, np.max(counts))

    for ax, io_group in zip(axes, io_groups):

        stat_lines = []

        for i, r in enumerate(results):
            style = SET_STYLES[i]
            values = r["trims_by_iogroup"].get(io_group)

            if values is None or len(values) == 0:
                stat_lines.append(f"{r['label']}: no data")
                continue

            if i == 0:
                # First set: filled histogram
                ax.hist(
                    values, bins=BINS, density=normalize,
                    color=style["color"], alpha=style["alpha"],
                    edgecolor="black", linewidth=0.5,
                )
            else:
                # Second set: outline on top so both remain visible
                ax.hist(
                    values, bins=BINS, density=normalize,
                    histtype="step", color=style["color"],
                    linestyle=style["linestyle"], linewidth=1.8,
                )

            mean = np.mean(values)
            ax.axvline(
                mean, color=style["color"],
                linestyle=":", linewidth=1.5,
            )

            n_chips = r["chips_by_iogroup"].get(io_group, 0)
            stat_lines.append(
                f"{r['label']}: {n_chips} chips, {len(values)} ch\n"
                f"  mean={mean:.2f}, med={np.median(values):.1f}, "
                f"std={np.std(values):.2f}"
            )

        ax.set_title(f"IO Group {io_group}")

        ax.text(
            0.97, 0.95, "\n".join(stat_lines),
            transform=ax.transAxes, ha="right", va="top",
            fontsize=8.5, zorder=10,
            bbox=dict(facecolor="white", alpha=0.8, edgecolor="none"),
        )

        ax.set_xlim(-0.5, 31.5)
        ax.set_ylim(0, max_bin_count * 1.35)
        ax.set_xticks(np.arange(0, 32, 4))
        ax.grid(alpha=0.25)
        ax.set_xlabel("Pixel trim DAC")
        ax.set_ylabel("Fraction of channels" if normalize else "Channel count")

    for ax in axes[len(io_groups):]:
        ax.set_visible(False)

    # Figure-level legend identifying the two configuration sets
    handles = [
        Patch(
            facecolor=SET_STYLES[0]["color"], alpha=SET_STYLES[0]["alpha"],
            edgecolor="black", label=results[0]["label"],
        ),
        Line2D(
            [0], [0], color=SET_STYLES[1]["color"],
            linestyle=SET_STYLES[1]["linestyle"], linewidth=1.8,
            label=results[1]["label"],
        ),
        Line2D(
            [0], [0], color="gray", linestyle=":", linewidth=1.5,
            label="mean (set colour)",
        ),
    ]
    fig.legend(
        handles=handles, loc="upper right",
        bbox_to_anchor=(0.99, 0.995), fontsize=11, ncol=3,
    )

    fig.suptitle(title, fontsize=18, x=0.02, ha="left")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(outfile, dpi=180, bbox_inches="tight")

    print(f"Saved plot to: {outfile}")


# ================================================================
# Figure 2: all IO groups and both sets on one axis
# ================================================================

def plot_overlay(results, outfile, normalize=False):

    io_groups = sorted(
        set().union(*(r["trims_by_iogroup"].keys() for r in results))
    )

    # One colour per IO group, one line style per configuration set
    cmap = plt.get_cmap("tab10" if len(io_groups) <= 10 else "tab20")
    group_colors = {g: cmap(i % cmap.N) for i, g in enumerate(io_groups)}
    set_linestyles = ["-", "--"]

    fig, ax = plt.subplots(figsize=(11, 7.5))

    for io_group in io_groups:
        for i, r in enumerate(results):
            values = r["trims_by_iogroup"].get(io_group)
            if values is None or len(values) == 0:
                continue

            ax.hist(
                values, bins=BINS, density=normalize,
                histtype="step",
                color=group_colors[io_group],
                linestyle=set_linestyles[i],
                linewidth=1.8,
                label=f"io_group = {io_group} [{r['label']}]",
            )

    ax.set_yscale("log")
    ax.set_xlim(-0.5, 31.5)
    ax.set_xticks(np.arange(0, 32, 4))
    ax.set_xlabel("pixel_trim_dac", fontsize=14)
    ax.set_ylabel(
        "fraction of channels" if normalize else "channel count",
        fontsize=14,
    )
    ax.grid(alpha=0.25, which="both")

    # Compact key for the line styles, inside the plot
    style_key = [
        Line2D([0], [0], color="black", linestyle=set_linestyles[i],
               linewidth=1.8, label=r["label"])
        for i, r in enumerate(results)
    ]
    key = ax.legend(handles=style_key, loc="upper right", fontsize=11,
                    title="Configuration set")
    ax.add_artist(key)

    # Full legend (every io_group / set combination), outside the axes
    ax.legend(
        fontsize=9, loc="upper left",
        bbox_to_anchor=(1.02, 1.0), ncol=1,
        title="colour = io_group\nline style = config set",
        title_fontsize=9,
    )

    ax.set_title(
        f"Pixel trim overlay — {results[0]['label']} vs {results[1]['label']}",
        fontsize=14,
    )

    fig.tight_layout()
    fig.savefig(outfile, dpi=180, bbox_inches="tight")

    print(f"Saved plot to: {outfile}")


# ================================================================
# Main
# ================================================================

def main(files_a, files_b, label_a="Config A", label_b="Config B",
         ignore_csa=False, normalize=False, title=None, prefix=""):

    if label_a == label_b:
        label_b = label_b + " (2)"

    results = [
        load_config_set(files_a, label_a, ignore_csa=ignore_csa),
        load_config_set(files_b, label_b, ignore_csa=ignore_csa),
    ]

    for r in results:
        print_summary(r)

    if not any(r["trims_by_iogroup"] for r in results):
        print("No valid pixel trim values found in either set.")
        return

    for r in results:
        if not r["trims_by_iogroup"]:
            print(f"Warning: no valid pixel trim values in '{r['label']}'.")

    if title is None:
        title = (
            f"Pixel Trim Distribution by IO Group — "
            f"{label_a} vs {label_b}"
        )

    print()
    plot_by_iogroup(
        results, title,
        f"{prefix}pixel_trim_by_iogroup_comparison.png",
        normalize=normalize,
    )
    plot_overlay(
        results,
        f"{prefix}pixel_trim_overlay_comparison.png",
        normalize=normalize,
    )


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description=(
            "Compare pixel trim DAC distributions by IO group "
            "for two sets of configuration files."
        )
    )

    parser.add_argument(
        "-a", "--set-a", nargs="+", required=True, metavar="FILE",
        help="First set of ASIC configuration JSON files",
    )
    parser.add_argument(
        "-b", "--set-b", nargs="+", required=True, metavar="FILE",
        help="Second set of ASIC configuration JSON files",
    )
    parser.add_argument(
        "--label-a", default="Config A",
        help="Legend label for the first set (default: 'Config A')",
    )
    parser.add_argument(
        "--label-b", default="Config B",
        help="Legend label for the second set (default: 'Config B')",
    )
    parser.add_argument(
        "--ignore-csa", action="store_true",
        help="Include all 64 channels regardless of csa_enable/channel_mask.",
    )
    parser.add_argument(
        "--normalize", action="store_true",
        help=(
            "Plot fractions instead of raw counts (useful when the two "
            "sets have different numbers of chips)."
        ),
    )
    parser.add_argument(
        "--title", default=None,
        help="Custom suptitle for the per-IO-group figure.",
    )
    parser.add_argument(
        "--prefix", default="",
        help="Prefix for output plot filenames.",
    )

    args = parser.parse_args()

    main(
        args.set_a,
        args.set_b,
        label_a=args.label_a,
        label_b=args.label_b,
        ignore_csa=args.ignore_csa,
        normalize=args.normalize,
        title=args.title,
        prefix=args.prefix,
    )
