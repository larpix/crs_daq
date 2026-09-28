

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


def main(*files, ignore_csa=False, **kwargs):

    trims_by_iogroup = {}
    chips_by_iogroup = {}

    low_trim_chips = []
    high_trim_chips = []

    missing_trim = 0
    missing_iogroup = 0
    bad_trim = 0
    skipped_no_active_channels = 0

    # ============================================================
    # Read configurations
    # ============================================================

    for file in files:

        try:
            with open(file, "r") as f:
                config = json.load(f)
        except Exception as e:
            print(f"Could not read {file}: {e}")
            continue

        # --------------------------------------------------------
        # IO group
        # --------------------------------------------------------

        io_group = get_io_group(config)

        if io_group is None:
            missing_iogroup += 1
            print(f"Could not determine io_group: {file}")
            continue

        # --------------------------------------------------------
        # Pixel trim DAC
        # --------------------------------------------------------

        try:
            ptd = np.array(
                config["pixel_trim_dac"],
                dtype=int,
            )
        except (KeyError, TypeError, ValueError):
            missing_trim += 1
            print(f"Missing pixel_trim_dac: {file}")
            continue

        if len(ptd) != 64:
            print(
                f"Unexpected pixel_trim_dac length "
                f"({len(ptd)}): {file}"
            )
            continue

        if np.any(ptd < 0) or np.any(ptd > 31):
            bad_trim += 1
            print(
                f"Pixel trim outside [0,31]: "
                f"{file}\n{ptd}"
            )

        # --------------------------------------------------------
        # Active channels
        #
        # Active = csa_enable AND NOT channel_mask
        # --------------------------------------------------------

        if ignore_csa:
            active = np.ones(64, dtype=bool)

        else:
            try:
                csa = np.array(
                    config["csa_enable"],
                    dtype=bool,
                )

                channel_mask = np.array(
                    config["channel_mask"],
                    dtype=bool,
                )

                active = np.logical_and(
                    csa,
                    np.logical_not(channel_mask),
                )

            except (KeyError, TypeError, ValueError):
                # Match the spirit of the old script:
                # if CSA information isn't available, assume
                # all channels are active.
                active = np.ones(64, dtype=bool)

        if not np.any(active):
            skipped_no_active_channels += 1
            continue

        active_trims = ptd[active]

        # --------------------------------------------------------
        # Store values by IO group
        # --------------------------------------------------------

        trims_by_iogroup.setdefault(
            io_group, []
        ).extend(active_trims.tolist())

        chips_by_iogroup[io_group] = (
            chips_by_iogroup.get(io_group, 0) + 1
        )

        # --------------------------------------------------------
        # Existing low/high-trim diagnostics
        # --------------------------------------------------------

        try:
            chip_key = config["meta"]["CHIP_KEY"]
        except KeyError:
            chip_key = file

        threshold_global = config.get(
            "threshold_global",
            "unknown",
        )

        if np.percentile(active_trims, 80) < 4:

            print(
                "Low Trims!!",
                chip_key,
                active_trims,
                "tdac =",
                threshold_global,
            )

            low_trim_chips.append(chip_key)

        if np.percentile(active_trims, 40) > 20:

            print(
                "High Trims!!",
                chip_key,
                active_trims,
                "tdac =",
                threshold_global,
            )

            high_trim_chips.append(chip_key)

    # ============================================================
    # Sanity check
    # ============================================================

    if not trims_by_iogroup:
        print("No valid pixel trim values found.")
        return

    io_groups = sorted(
        trims_by_iogroup.keys()
    )

    # Pixel trim DAC is intrinsically 0--31
    bins = np.arange(-0.5, 32.5, 1)

    # ============================================================
    # Text summary
    # ============================================================

    print()
    print("==========================================")
    print("Pixel trim summary")
    print("==========================================")
    print()

    total_channels = sum(
        len(v)
        for v in trims_by_iogroup.values()
    )

    total_chips = sum(
        chips_by_iogroup.values()
    )

    print(f"IO groups:                  {io_groups}")
    print(f"Valid chips:                {total_chips}")
    print(f"Active channels:            {total_channels}")
    print(f"Missing pixel_trim_dac:     {missing_trim}")
    print(f"Missing io_group:           {missing_iogroup}")
    print(f"Configs with bad trim DAC:  {bad_trim}")
    print(
        f"Skipped chips (no active):  "
        f"{skipped_no_active_channels}"
    )
    print()

    for io_group in io_groups:

        values = np.array(
            trims_by_iogroup[io_group]
        )

        print(
            f"IO group {io_group}: "
            f"{chips_by_iogroup[io_group]} chips, "
            f"{len(values)} channels, "
            f"mean={np.mean(values):.2f}, "
            f"median={np.median(values):.1f}, "
            f"std={np.std(values):.2f}"
        )

    print()

    # ------------------------------------------------------------
    # Global trim distribution, preserving old output
    # ------------------------------------------------------------

    all_trims = np.concatenate(
        [
            np.array(v)
            for v in trims_by_iogroup.values()
        ]
    )

    vals, _ = np.histogram(
        all_trims,
        bins=bins,
    )

    print("Global pixel trim distribution:")

    for trim_dac, count in enumerate(vals):
        print(f"{trim_dac}: {count}")

    print()
    print(
        ",".join(
            str(v)
            for v in vals
        )
    )

    print()
    print(
        "Number of low trim chips:",
        len(low_trim_chips),
    )

    print(
        "Number of high trim chips:",
        len(high_trim_chips),
    )

    # Preserve the original output file
    with open("low_trim_chips.json", "w") as f:
        json.dump(
            low_trim_chips,
            f,
            indent=4,
        )

    # ============================================================
    # Figure 1:
    # Individual IO-group panels
    # ============================================================

    ncols = 4
    nrows = math.ceil(
        len(io_groups) / ncols
    )

    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(16, 4.5 * nrows),
        sharex=True,
        sharey=True,
    )

    axes = np.atleast_1d(
        axes
    ).flatten()

    # ------------------------------------------------------------
    # Find common Y maximum
    # ------------------------------------------------------------

    max_bin_count = 0

    for io_group in io_groups:

        values = np.array(
            trims_by_iogroup[io_group]
        )

        counts, _ = np.histogram(
            values,
            bins=bins,
        )

        max_bin_count = max(
            max_bin_count,
            np.max(counts),
        )

    # ------------------------------------------------------------
    # Draw panels
    # ------------------------------------------------------------

    for ax, io_group in zip(
        axes,
        io_groups,
    ):

        values = np.array(
            trims_by_iogroup[io_group]
        )

        ax.hist(
            values,
            bins=bins,
            edgecolor="black",
            linewidth=0.7,
        )

        mean = np.mean(values)
        median = np.median(values)
        std = np.std(values)

        ax.axvline(
            mean,
            linestyle="--",
            linewidth=1.5,
        )

        ax.set_title(
            f"IO Group {io_group} — "
            f"{chips_by_iogroup[io_group]} chips"
        )

        ax.text(
            0.97,
            0.95,
            f"{len(values)} channels\n"
            f"mean = {mean:.2f}\n"
            f"median = {median:.1f}\n"
            f"std = {std:.2f}",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=10,
        )

        ax.set_xlim(
            -0.5,
            31.5,
        )

        ax.set_ylim(
            0,
            max_bin_count * 1.12,
        )

        ax.set_xticks(
            np.arange(0, 32, 4)
        )

        ax.grid(
            alpha=0.25
        )

        ax.set_xlabel(
            "Pixel trim DAC"
        )

        ax.set_ylabel(
            "Channel count"
        )

    # Hide unused panels
    for ax in axes[len(io_groups):]:
        ax.set_visible(False)

    fig.suptitle(
        "2x2 Run 3 — Pixel Trim Distribution by IO Group",
        fontsize=18,
    )

    fig.tight_layout(
        rect=[0, 0, 1, 0.95]
    )

    fig.savefig(
        "pixel_trim_by_iogroup.png",
        dpi=180,
        bbox_inches="tight",
    )

    print()
    print(
        "Saved plot to: "
        "pixel_trim_by_iogroup.png"
    )

    # ============================================================
    # Figure 2:
    # Overlay all IO groups
    # ============================================================

    fig_overlay, ax = plt.subplots(
        figsize=(9, 7)
    )

    for io_group in io_groups:

        values = np.array(
            trims_by_iogroup[io_group]
        )

        ax.hist(
            values,
            bins=bins,
            histtype="step",
            linewidth=1.8,
            label=f"io_group = {io_group}",
        )

    ax.set_yscale("log")

    ax.set_xlim(
        -0.5,
        31.5,
    )

    ax.set_xticks(
        np.arange(0, 32, 4)
    )

    ax.set_xlabel(
        "pixel_trim_dac",
        fontsize=14,
    )

    ax.set_ylabel(
        "channel count",
        fontsize=14,
    )

    ax.grid(
        alpha=0.25,
        which="both",
    )

    ax.legend(
        fontsize=11,
        loc="upper right",
    )

    fig_overlay.tight_layout()

    fig_overlay.savefig(
        "pixel_trim_overlay.png",
        dpi=180,
        bbox_inches="tight",
    )

    print(
        "Saved plot to: "
        "pixel_trim_overlay.png"
    )


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description=(
            "Plot pixel trim DAC distributions "
            "by IO group."
        )
    )

    parser.add_argument(
        "input_files",
        nargs="+",
        help="ASIC configuration JSON files",
    )

    parser.add_argument(
        "--ignore-csa",
        action="store_true",
        help=(
            "Include all 64 channels regardless "
            "of csa_enable/channel_mask."
        ),
    )

    args = parser.parse_args()

    main(
        *args.input_files,
        ignore_csa=args.ignore_csa,
    )
