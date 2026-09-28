#!/usr/bin/env python3
"""
Merge a hot-pixel list into a combined JSON mask.

Input hot-pixel format, one pixel per line:
    io_group-io_channel-chip_id-channel_id

The combined JSON uses:
    "io_group-tile_id-chip_id": [channel_id, ...]

PACMAN io_channel -> tile_id:
    1-4   -> 1
    5-8   -> 2
    ...
    29-32 -> 8
"""

import argparse
import json
from pathlib import Path


def io_channel_to_tile(io_channel: int) -> int:
    if not 1 <= io_channel <= 32:
        raise ValueError(f"io_channel must be 1..32, got {io_channel}")
    return (io_channel - 1) // 4 + 1


def parse_hot_pixels(path: Path, skip_invalid: bool = False):
    """
    Return a list of (key, channel_id, source_line_number).

    key is the JSON key: "io_group-tile_id-chip_id".
    Duplicate hot pixels are removed.
    """
    pixels = []
    seen = set()

    with path.open() as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()

            # Allow blank lines and comments.
            if not line or line.startswith("#"):
                continue

            try:
                fields = line.split("-")
                if len(fields) != 4:
                    raise ValueError(
                        "expected io_group-io_channel-chip_id-channel_id"
                    )

                io_group, io_channel, chip_id, channel_id = map(int, fields)

                if not 1 <= io_group <= 8:
                    raise ValueError(f"io_group must be 1..8, got {io_group}")
                if not 0 <= chip_id <= 255:
                    raise ValueError(f"chip_id must be 0..255, got {chip_id}")
                if not 0 <= channel_id <= 63:
                    raise ValueError(f"channel_id must be 0..63, got {channel_id}")

                tile_id = io_channel_to_tile(io_channel)
                key = f"{io_group}-{tile_id}-{chip_id}"

                pixel = (key, channel_id)
                if pixel not in seen:
                    seen.add(pixel)
                    pixels.append((key, channel_id, lineno))

            except ValueError as exc:
                msg = f"{path}:{lineno}: {line!r}: {exc}"
                if skip_invalid:
                    print(f"WARNING: skipping {msg}")
                    continue
                raise ValueError(msg) from exc

    return pixels


def merge_hot_pixels(mask, pixels):
    """
    Merge pixels into the JSON object in place.

    Existing entries are never overwritten.
    Existing channel order is preserved; newly added channels are appended.
    """
    added = 0
    already_present = 0
    new_keys = 0

    for key, channel_id, _lineno in pixels:
        if key not in mask:
            mask[key] = []
            new_keys += 1

        if not isinstance(mask[key], list):
            raise TypeError(
                f"JSON entry {key!r} is not a channel list: {mask[key]!r}"
            )

        if channel_id in mask[key]:
            already_present += 1
        else:
            mask[key].append(channel_id)
            added += 1

    return added, already_present, new_keys


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Merge io_group-io_channel-chip-channel hot pixels "
            "into a combined JSON mask."
        )
    )
    parser.add_argument("json_file", type=Path, help="Existing combined JSON mask")
    parser.add_argument("hot_pixels", type=Path, help="Hot-pixel text file")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="Output JSON file (default: <input>_with_hot_pixels.json)"
    )
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Warn and skip malformed/out-of-range hot-pixel lines instead of stopping"
    )
    args = parser.parse_args()

    output = args.output
    if output is None:
        output = args.json_file.with_name(
            f"{args.json_file.stem}_with_hot_pixels{args.json_file.suffix}"
        )

    with args.json_file.open() as f:
        mask = json.load(f)

    if not isinstance(mask, dict):
        raise TypeError("Top-level JSON value must be an object/dictionary.")

    pixels = parse_hot_pixels(args.hot_pixels, skip_invalid=args.skip_invalid)
    added, already_present, new_keys = merge_hot_pixels(mask, pixels)

    with output.open("w") as f:
        json.dump(mask, f, indent=4)
        f.write("\n")

    print(f"Parsed unique hot pixels : {len(pixels)}")
    print(f"Added to JSON            : {added}")
    print(f"Already present          : {already_present}")
    print(f"New JSON chip keys       : {new_keys}")
    print(f"Wrote                    : {output}")


if __name__ == "__main__":
    main()

