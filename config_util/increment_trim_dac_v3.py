#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path


TRIM_MIN = 0
TRIM_MAX = 31
N_CHANNELS = 64


def collect_files(inputs):
    """
    Accept individual JSON files and/or directories.
    Directories contribute all *.json files directly inside them.
    """
    files = []

    for item in inputs:
        path = Path(item)

        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))

        elif path.is_file():
            files.append(path)

        else:
            raise FileNotFoundError(f"Input does not exist: {path}")

    # Remove duplicates while preserving order
    seen = set()
    unique = []

    for path in files:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(path)
            seen.add(resolved)

    return unique


def validate_config(config, path):
    if "pixel_trim_dac" not in config:
        raise ValueError(f"{path}: missing 'pixel_trim_dac'")

    trims = config["pixel_trim_dac"]

    if len(trims) != N_CHANNELS:
        raise ValueError(
            f"{path}: expected {N_CHANNELS} trim values, "
            f"found {len(trims)}"
        )

    for chan, value in enumerate(trims):
        if not isinstance(value, int):
            raise ValueError(
                f"{path}: channel {chan} trim is not an integer: {value!r}"
            )

        if not TRIM_MIN <= value <= TRIM_MAX:
            raise ValueError(
                f"{path}: channel {chan} has invalid trim {value}"
            )


def main(files, inc, dry_run=False):
    total_files = 0
    total_channels = 0

    changed = 0
    unchanged = 0

    saturated_high = 0
    saturated_low = 0

    # ---------------------------------------------------------
    # Preflight
    #
    # Check every file before modifying any of them.
    # ---------------------------------------------------------

    configs = []

    print(f"Preflighting {len(files)} files...")

    for path in files:
        with open(path, "r") as f:
            config = json.load(f)

        validate_config(config, path)
        configs.append((path, config))

    print("Preflight OK.")
    print()

    # ---------------------------------------------------------
    # Apply transformation
    # ---------------------------------------------------------

    for path, config in configs:
        trims = config["pixel_trim_dac"]

        for chan in range(N_CHANNELS):
            old = trims[chan]

            raw_new = old + inc
            new = max(TRIM_MIN, min(raw_new, TRIM_MAX))

            if raw_new > TRIM_MAX:
                saturated_high += 1

            if raw_new < TRIM_MIN:
                saturated_low += 1

            if new != old:
                changed += 1
            else:
                unchanged += 1

            trims[chan] = new

            total_channels += 1

        total_files += 1

        if not dry_run:
            with open(path, "w") as f:
                json.dump(config, f, indent=4)
                f.write("\n")

    # ---------------------------------------------------------
    # Summary
    # ---------------------------------------------------------

    print("============================================================")
    print(" PIXEL TRIM UPDATE SUMMARY")
    print("============================================================")
    print(f"Files                   : {total_files}")
    print(f"Channels                : {total_channels}")
    print(f"Requested delta         : {inc:+d}")
    print()
    print(f"Actually changed        : {changed}")
    print(f"Unchanged               : {unchanged}")

    if inc > 0:
        print(f"Saturated at {TRIM_MAX:<2}       : {saturated_high}")

    if inc < 0:
        print(f"Saturated at {TRIM_MIN:<2}        : {saturated_low}")

    print()

    if dry_run:
        print("DRY RUN — no files were modified.")
    else:
        print("Update complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Increment/decrement LArPix pixel trim DACs."
    )

    parser.add_argument(
        "inputs",
        nargs="+",
        help="JSON files and/or directories containing JSON files",
    )

    parser.add_argument(
        "--inc",
        type=int,
        required=True,
        help="Amount to change pixel_trim_dac by, e.g. +1 or -1",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate changes without writing anything",
    )

    args = parser.parse_args()

    try:
        files = collect_files(args.inputs)

        if not files:
            raise RuntimeError("No JSON files found.")

        main(
            files,
            inc=args.inc,
            dry_run=args.dry_run,
        )

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
