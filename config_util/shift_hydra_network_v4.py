#!/usr/bin/env python3

import argparse
import copy
import json
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime


# The original shift_hydra_network.py considers all six of these
# part of the Hydra routing.
HYDRA_KEYS = {
    "enable_piso_upstream",
    "enable_piso_downstream",
    "enable_posi",
    "enable_miso_upstream",
    "enable_miso_downstream",
    "enable_mosi",
}

# Identity/location comes from the CURRENT known-working config.
IDENTITY_KEYS = {
    "CHIP_KEY",
    "ASIC_ID",
    "ASIC_VERSION",
}

PROTECTED_TOPLEVEL = HYDRA_KEYS | IDENTITY_KEYS


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=4)
        f.write("\n")


def get_value(config, key):
    """
    Get identity information from either:
        config["meta"][key]
    or old-style:
        config[key]
    """
    if isinstance(config.get("meta"), dict):
        if key in config["meta"]:
            return config["meta"][key]

    return config.get(key)


def get_chip_key(config):
    return str(get_value(config, "CHIP_KEY"))


def get_asic_id(config):
    value = get_value(config, "ASIC_ID")
    return None if value is None else str(value)


def parse_chip_key(chip_key):
    """
    '6-10-31' -> (6, 10, 31)
    """
    try:
        iog, io_channel, chip_id = str(chip_key).split("-")
        return int(iog), int(io_channel), int(chip_id)
    except Exception:
        return None


def tuning_score(candidate, current):
    """
    Estimate how much actual tuning information a candidate contains
    compared with the current/default config.

    Useful when a contaminated directory contains both:
        config_6-9-31.json   (old tuned)
        config_6-10-31.json  (new default copy)

    The genuinely tuned config should normally differ more from the
    default config.
    """
    score = 0

    for key, value in candidate.items():

        if key == "meta":
            continue

        if key in PROTECTED_TOPLEVEL:
            continue

        if key not in current or current[key] != value:
            score += 1

    return score


def choose_tuned_candidate(candidates, current_config):
    """
    Choose the most likely tuned version when multiple files have
    the same ASIC_ID.
    """
    current_chip_key = get_chip_key(current_config)

    ranked = []

    for path, cfg in candidates:
        score = tuning_score(cfg, current_config)

        # In a Hydra-shift situation, the old tuned config often has
        # a different CHIP_KEY than the current config.
        moved = get_chip_key(cfg) != current_chip_key

        ranked.append(
            (
                score,
                int(moved),
                path,
                cfg,
            )
        )

    ranked.sort(
        key=lambda x: (x[0], x[1]),
        reverse=True,
    )

    return ranked[0], ranked


def merge_tuning(current_config, tuned_config):
    """
    Start from CURRENT known-working configuration.

    Copy all tunable top-level fields from the old tuned config while
    leaving Hydra routing and chip identity untouched.
    """

    merged = copy.deepcopy(current_config)

    for key, value in tuned_config.items():

        if key == "meta":
            # Keep current metadata completely. In particular:
            # CHIP_KEY / ASIC_ID / ASIC_VERSION.
            continue

        if key in PROTECTED_TOPLEVEL:
            continue

        merged[key] = copy.deepcopy(value)

    return merged


def main(
    iog,
    tuned_folder,
    io_channels,
    defaults_file=".default_asic_configs_.json",
):

    iog = int(iog)
    io_channels = set(int(x) for x in io_channels)

    # ------------------------------------------------------------
    # Resolve CURRENT authoritative config
    # ------------------------------------------------------------

    defaults = load_json(defaults_file)

    key = str(iog)

    if key not in defaults:
        raise RuntimeError(
            f"IOG {iog} is not present in {defaults_file}"
        )

    current_folder = os.path.abspath(defaults[key])
    tuned_folder = os.path.abspath(tuned_folder)

    if current_folder == tuned_folder:
        raise RuntimeError(
            "Current/default directory and tuned directory are identical."
        )

    print()
    print("=" * 70)
    print("Rebuild tuned configs onto current Hydra")
    print("=" * 70)
    print(f"IO group:             {iog}")
    print(f"IO channels:          {sorted(io_channels)}")
    print(f"Current authoritative:{current_folder}")
    print(f"Tuned directory:      {tuned_folder}")
    print()

    # ------------------------------------------------------------
    # Load ALL tuned configs BEFORE touching the directory.
    #
    # ASIC_ID is the join key, NOT CHIP_KEY / filename.
    # ------------------------------------------------------------

    tuned_by_asic = defaultdict(list)
    tuned_files = []

    for filename in sorted(os.listdir(tuned_folder)):

        if not filename.endswith(".json"):
            continue

        path = os.path.join(tuned_folder, filename)

        if not os.path.isfile(path):
            continue

        try:
            cfg = load_json(path)
        except Exception as exc:
            print(f"WARNING: cannot read {path}: {exc}")
            continue

        asic_id = get_asic_id(cfg)

        if asic_id is None:
            print(f"WARNING: no ASIC_ID in {path}")
            continue

        tuned_by_asic[asic_id].append((path, cfg))
        tuned_files.append((path, cfg))

    # ------------------------------------------------------------
    # Inventory CURRENT configs for requested channels.
    # ------------------------------------------------------------

    current_entries = []

    for filename in sorted(os.listdir(current_folder)):

        if not filename.endswith(".json"):
            continue

        path = os.path.join(current_folder, filename)

        if not os.path.isfile(path):
            continue

        cfg = load_json(path)

        chip_key = get_chip_key(cfg)
        parsed = parse_chip_key(chip_key)

        if parsed is None:
            continue

        cfg_iog, io_channel, chip_id = parsed

        if cfg_iog != iog:
            continue

        if io_channel not in io_channels:
            continue

        asic_id = get_asic_id(cfg)

        if asic_id is None:
            raise RuntimeError(
                f"Current config has no ASIC_ID: {path}"
            )

        current_entries.append(
            {
                "path": path,
                "filename": filename,
                "config": cfg,
                "chip_key": chip_key,
                "asic_id": asic_id,
                "io_channel": io_channel,
                "chip_id": chip_id,
            }
        )

    if not current_entries:
        raise RuntimeError(
            "No current configs found for requested IOG/channels."
        )

    authoritative_asic_ids = {
        entry["asic_id"]
        for entry in current_entries
    }

    print(
        f"Current Hydra contains {len(current_entries)} configs "
        f"in selected channels."
    )

    # ------------------------------------------------------------
    # Back up ENTIRE tuned directory.
    # ------------------------------------------------------------

    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")

    backup = (
        tuned_folder.rstrip("/")
        + "_before_hydra_rebuild_"
        + timestamp
    )

    print()
    print("Backing up tuned directory:")
    print(f"  {backup}")

    shutil.copytree(tuned_folder, backup)

    # ------------------------------------------------------------
    # Remove stale files.
    #
    # Delete:
    #   1. anything whose CHIP_KEY is in selected channels
    #   2. anything with an ASIC_ID represented by the current Hydra
    #
    # #2 is what catches e.g. old 6-9-31 when ASIC X is now 6-10-31.
    # ------------------------------------------------------------

    print()
    print("Removing stale configs from rebuilt region...")

    removed = []

    for path, cfg in tuned_files:

        parsed = parse_chip_key(get_chip_key(cfg))
        asic_id = get_asic_id(cfg)

        selected_by_location = False

        if parsed is not None:
            cfg_iog, io_channel, chip_id = parsed

            selected_by_location = (
                cfg_iog == iog
                and io_channel in io_channels
            )

        selected_by_identity = (
            asic_id in authoritative_asic_ids
        )

        if selected_by_location or selected_by_identity:

            if os.path.exists(path):
                print(
                    f"  REMOVE {os.path.basename(path)}"
                    f"  ASIC_ID={asic_id}"
                )
                os.remove(path)
                removed.append(path)

    # ------------------------------------------------------------
    # Rebuild from CURRENT configs.
    # ------------------------------------------------------------

    print()
    print("Rebuilding configs...")

    rebuilt = 0
    rescued = 0
    default_only = 0

    for entry in current_entries:

        current_cfg = entry["config"]
        current_key = entry["chip_key"]
        asic_id = entry["asic_id"]

        candidates = tuned_by_asic.get(asic_id, [])

        output_name = f"config_{current_key}.json"
        output_path = os.path.join(
            tuned_folder,
            output_name,
        )

        if candidates:

            winner, ranking = choose_tuned_candidate(
                candidates,
                current_cfg,
            )

            score, moved, source_path, source_cfg = winner

            if len(candidates) > 1:
                print()
                print(
                    f"  MULTIPLE candidates for ASIC_ID {asic_id}:"
                )

                for s, m, p, c in ranking:
                    print(
                        f"    score={s:3d} "
                        f"CHIP_KEY={get_chip_key(c):>10} "
                        f"{os.path.basename(p)}"
                    )

                print(
                    "    -> choosing "
                    f"{os.path.basename(source_path)}"
                )

            merged = merge_tuning(
                current_cfg,
                source_cfg,
            )

            write_json(
                output_path,
                merged,
            )

            old_key = get_chip_key(source_cfg)

            if old_key != current_key:
                print(
                    f"  RESCUE ASIC_ID {asic_id}: "
                    f"{old_key} -> {current_key}"
                )
            else:
                print(
                    f"  UPDATE {current_key}"
                )

            rescued += 1

        else:

            # ASIC exists in current working Hydra but had no old
            # tuned config. Keep the known-working default config.
            write_json(
                output_path,
                current_cfg,
            )

            print(
                f"  DEFAULT {current_key}: "
                f"no tuned config for ASIC_ID {asic_id}"
            )

            default_only += 1

        rebuilt += 1

    # ------------------------------------------------------------
    # Verify there are no duplicate ASIC IDs or old locations left.
    # ------------------------------------------------------------

    print()
    print("Verifying rebuilt directory...")

    seen_asic = {}
    errors = []

    for filename in sorted(os.listdir(tuned_folder)):

        if not filename.endswith(".json"):
            continue

        path = os.path.join(tuned_folder, filename)

        if not os.path.isfile(path):
            continue

        cfg = load_json(path)

        asic_id = get_asic_id(cfg)
        chip_key = get_chip_key(cfg)

        if asic_id in seen_asic:
            errors.append(
                f"Duplicate ASIC_ID {asic_id}: "
                f"{seen_asic[asic_id]} and {filename}"
            )
        else:
            seen_asic[asic_id] = filename

        # Filename should agree with CHIP_KEY.
        expected = f"config_{chip_key}.json"

        if filename != expected:
            errors.append(
                f"Filename/CHIP_KEY mismatch: "
                f"{filename} != {expected}"
            )

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Removed stale configs : {len(removed)}")
    print(f"Rebuilt configs       : {rebuilt}")
    print(f"Recovered tuning      : {rescued}")
    print(f"Current/default only  : {default_only}")
    print(f"Verification errors   : {len(errors)}")
    print(f"Backup                : {backup}")

    if errors:
        print()
        print("ERRORS:")
        for error in errors:
            print(f"  {error}")

        raise RuntimeError(
            "Verification failed. See errors above."
        )

    print()
    print("Rebuild completed successfully.")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description=(
            "Rebuild tuned ASIC configs on top of the current "
            "known-working Hydra, matching chips by ASIC_ID."
        )
    )

    parser.add_argument(
        "iog",
        type=int,
        help="IO group, e.g. 6",
    )

    parser.add_argument(
        "tuned_folder",
        help="Existing tuned ASIC config directory (the mX directory)",
    )

    parser.add_argument(
        "--io-channels",
        type=int,
        nargs="+",
        required=True,
        help=(
            "IO channels to rebuild, e.g. "
            "--io-channels 9 10 11 12"
        ),
    )

    parser.add_argument(
        "--defaults",
        default=".default_asic_configs_.json",
        help="Current default ASIC config mapping",
    )

    args = parser.parse_args()

    main(
        args.iog,
        args.tuned_folder,
        args.io_channels,
        defaults_file=args.defaults,
    )
