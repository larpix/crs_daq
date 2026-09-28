#!/usr/bin/env python3

import argparse
import copy
import json
import os
import shutil
from datetime import datetime


# These values belong to the CURRENT working Hydra and must never
# be overwritten by the old/tuned configuration.
PROTECTED_KEYS = {
    "enable_piso_upstream",
    "enable_piso_downstream",
    "enable_posi",

    # Protect identity information for configs that store it top-level
    "CHIP_KEY",
    "ASIC_ID",
    "ASIC_VERSION",
}

# Same thing for configs using the newer "meta" structure.
PROTECTED_META_KEYS = {
    "CHIP_KEY",
    "ASIC_ID",
    "ASIC_VERSION",
}


def overlay_config(current_config, tuned_config):
    """
    Start with the current known-working config and copy values from
    tuned_config, excluding Hydra routing and chip identity fields.
    """

    merged = copy.deepcopy(current_config)

    for key, value in tuned_config.items():

        # Never overwrite Hydra routing / identity
        if key in PROTECTED_KEYS:
            continue

        # Merge metadata carefully, preserving current chip identity
        if key == "meta" and isinstance(value, dict):
            if "meta" not in merged or not isinstance(merged["meta"], dict):
                merged["meta"] = {}

            for meta_key, meta_value in value.items():
                if meta_key in PROTECTED_META_KEYS:
                    continue

                merged["meta"][meta_key] = copy.deepcopy(meta_value)

            continue

        # Everything else comes from the tuned configuration
        merged[key] = copy.deepcopy(value)

    return merged


def main(iog, source_folder, defaults_file=".default_asic_configs_.json"):

    iog = str(iog)

    # ------------------------------------------------------------
    # Find the CURRENT working configuration directory
    # ------------------------------------------------------------

    with open(defaults_file, "r") as f:
        defaults = json.load(f)

    if iog not in defaults:
        raise RuntimeError(
            f"IO group {iog} is not present in {defaults_file}\n"
            f"Available IO groups: {list(defaults.keys())}"
        )

    current_folder = defaults[iog]

    if not os.path.isdir(current_folder):
        raise RuntimeError(
            f"Current config folder does not exist: {current_folder}"
        )

    if not os.path.isdir(source_folder):
        raise RuntimeError(
            f"Source/tuned config folder does not exist: {source_folder}"
        )

    current_folder = os.path.abspath(current_folder)
    source_folder = os.path.abspath(source_folder)

    if current_folder == source_folder:
        raise RuntimeError(
            "Source folder and current default folder are the same. "
            "Refusing to continue."
        )

    print()
    print("============================================================")
    print("Overlay tuned settings onto current working Hydra")
    print("============================================================")
    print(f"IO group:       {iog}")
    print(f"Current Hydra:  {current_folder}")
    print(f"Tuned configs:  {source_folder}")
    print()

    # ------------------------------------------------------------
    # Back up the known-working configs before touching anything
    # ------------------------------------------------------------

    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")

    backup_folder = (
        current_folder.rstrip("/")
        + f"_before_threshold_overlay_{timestamp}"
    )

    print(f"Creating backup:\n  {backup_folder}")

    shutil.copytree(current_folder, backup_folder)

    print()

    # ------------------------------------------------------------
    # Iterate ONLY over files in the CURRENT working Hydra
    # ------------------------------------------------------------

    current_files = sorted(
        filename
        for filename in os.listdir(current_folder)
        if filename.endswith(".json")
        and os.path.isfile(os.path.join(current_folder, filename))
    )

    updated = []
    missing = []
    failed = []

    for filename in current_files:

        current_path = os.path.join(current_folder, filename)
        tuned_path = os.path.join(source_folder, filename)

        # --------------------------------------------------------
        # No tuned config?
        #
        # Fine. Leave the current known-working config untouched.
        # --------------------------------------------------------

        if not os.path.isfile(tuned_path):
            print(f"NO TUNED CONFIG: {filename}")
            missing.append(filename)
            continue

        # --------------------------------------------------------
        # Load both
        # --------------------------------------------------------

        try:
            with open(current_path, "r") as f:
                current_config = json.load(f)

            with open(tuned_path, "r") as f:
                tuned_config = json.load(f)

        except Exception as exc:
            print(f"ERROR: {filename}: {exc}")
            failed.append(filename)
            continue

        # --------------------------------------------------------
        # Overlay tuned values onto CURRENT Hydra config
        # --------------------------------------------------------

        merged_config = overlay_config(
            current_config,
            tuned_config,
        )

        # --------------------------------------------------------
        # Write back to the current/default config
        # --------------------------------------------------------

        with open(current_path, "w") as f:
            json.dump(merged_config, f, indent=4)
            f.write("\n")

        print(f"UPDATED: {filename}")
        updated.append(filename)

    # ------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------

    print()
    print("============================================================")
    print("Done")
    print("============================================================")
    print(f"Current Hydra configs: {len(current_files)}")
    print(f"Updated from tuned:    {len(updated)}")
    print(f"No tuned config:       {len(missing)}")
    print(f"Errors:                {len(failed)}")
    print()
    print(f"Backup: {backup_folder}")

    if missing:
        print()
        print("Configs kept at their current/default values:")
        for filename in missing:
            print(f"  {filename}")

    if failed:
        print()
        print("Configs that could not be processed:")
        for filename in failed:
            print(f"  {filename}")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description=(
            "Overlay settings from a tuned ASIC configuration directory "
            "onto the current working Hydra configurations."
        )
    )

    parser.add_argument(
        "iog",
        type=int,
        help="IO group to process, e.g. 6",
    )

    parser.add_argument(
        "source_folder",
        help=(
            "Folder containing the tuned configs, e.g. "
            "/data/CRS/asic_configs/SelfTrigger_v3/m2"
        ),
    )

    parser.add_argument(
        "--defaults",
        default=".default_asic_configs_.json",
        help="Path to .default_asic_configs_.json",
    )

    args = parser.parse_args()

    main(
        args.iog,
        args.source_folder,
        defaults_file=args.defaults,
    )
