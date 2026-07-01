#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt


def git(*args):
    return subprocess.check_output(["git", *args], text=True)


def git_show(commit, path):
    try:
        return subprocess.check_output(
            ["git", "show", f"{commit}:{path}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None


def parse_iog_tile(path):
    m = re.search(r"iog-(\d+)-pacman-tile-(\d+)-hydra-network\.json$", path)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def summarize_hydra_json(text):
    data = json.loads(text)

    network = data.get("network", {})
    excluded = data.get("excluded_chips", []) or []

    root_chips = []
    connected_chips = set()
    edges = []
    branch_count = 0
    max_depth_guess = 0

    for key, maybe_branch_group in network.items():
        if key in ("miso_us_uart_map", "miso_ds_uart_map", "mosi_uart_map"):
            continue

        if not isinstance(maybe_branch_group, dict):
            continue

        for branch_id, branch in maybe_branch_group.items():
            nodes = branch.get("nodes", [])
            if not nodes:
                continue

            branch_count += 1

            by_chip = {str(n.get("chip_id")): n for n in nodes}

            for n in nodes:
                chip = n.get("chip_id")
                if chip != "ext":
                    connected_chips.add(str(chip))

                for target in n.get("miso_us", []) or []:
                    if target is not None:
                        edges.append((str(chip), str(target)))

            ext = by_chip.get("ext")
            if ext:
                for target in ext.get("miso_us", []) or []:
                    if target is not None:
                        root_chips.append(str(target))

            # Simple depth estimate: follow directed links from ext, count reachable chips.
            # This is enough for tree-ish hydra chains.
            children = {}
            for src, dst in edges:
                children.setdefault(src, []).append(dst)

            stack = [("ext", 0)]
            seen = set()
            while stack:
                chip, depth = stack.pop()
                if chip in seen:
                    continue
                seen.add(chip)
                max_depth_guess = max(max_depth_guess, depth)
                for child in children.get(chip, []):
                    stack.append((child, depth + 1))

    return {
        "n_connected_chips": len(connected_chips),
        "n_excluded_chips": len(excluded),
        "root_chips": ",".join(root_chips),
        "n_root_chips": len(root_chips),
        "n_edges": len(edges),
        "n_branches": branch_count,
        "max_depth_guess": max_depth_guess,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Path to hydra-network JSON inside the repo")
    parser.add_argument("--out-prefix", default="hydra_history")
    args = parser.parse_args()

    path = args.path
    iog, tile = parse_iog_tile(path)

    commits = git("log", "--follow", "--format=%H%x09%ad%x09%s", "--date=short", "--", path)
    rows = []

    for line in reversed(commits.strip().splitlines()):
        if not line.strip():
            continue

        commit, date, subject = line.split("\t", 2)
        text = git_show(commit, path)

        if text is None:
            continue

        try:
            summary = summarize_hydra_json(text)
        except Exception as exc:
            print(f"Skipping {commit[:8]}: could not parse {path}: {exc}")
            continue

        rows.append({
            "commit": commit[:8],
            "date": date,
            "subject": subject,
            "path": path,
            "io_group": iog,
            "tile": tile,
            **summary,
        })

    if not rows:
        raise SystemExit(f"No history found for {path}")

    df = pd.DataFrame(rows)
    df["iteration"] = range(len(df))

    csv_path = f"{args.out_prefix}.csv"
    df.to_csv(csv_path, index=False)
    print(f"Wrote {csv_path}")

    print()
    print(df[[
        "iteration",
        "commit",
        "date",
        "n_connected_chips",
        "n_excluded_chips",
        "root_chips",
        "max_depth_guess",
        "subject",
    ]].to_string(index=False))

    plt.figure()
    plt.plot(df["iteration"], df["n_connected_chips"], marker="o")
    plt.xlabel("Commit iteration")
    plt.ylabel("Connected chips")
    plt.title(f"Connected chips over Git history: {path}")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f"{args.out_prefix}_connected_chips.png", dpi=150)

    plt.figure()
    plt.plot(df["iteration"], df["n_excluded_chips"], marker="o")
    plt.xlabel("Commit iteration")
    plt.ylabel("Excluded chips")
    plt.title(f"Excluded chips over Git history: {path}")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f"{args.out_prefix}_excluded_chips.png", dpi=150)

    plt.figure()
    plt.plot(df["iteration"], df["n_root_chips"], marker="o")
    plt.xlabel("Commit iteration")
    plt.ylabel("Root chips")
    plt.title(f"Root-chip count over Git history: {path}")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f"{args.out_prefix}_root_chip_count.png", dpi=150)

    print()
    print("Wrote plots:")
    print(f"  {args.out_prefix}_connected_chips.png")
    print(f"  {args.out_prefix}_excluded_chips.png")
    print(f"  {args.out_prefix}_root_chip_count.png")


if __name__ == "__main__":
    main()
