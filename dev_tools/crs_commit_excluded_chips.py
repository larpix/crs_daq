#!/usr/bin/env python3
"""
Read excluded-chip information directly from tracked CRS config files at a Git commit.

This is a small recovery/inspection tool for commits that do not yet contain the
compact config_history/current/*.csv summaries.

It does NOT check out or roll back the repository.  It reads files directly with:

    git show <commit>:RUN_CONFIG.json
    git show <commit>:configs/iog-*-pacman-tile-*-hydra-network.json

Recommended first query, because it matches the operator/manual excludes:

    python dev_tools/crs_commit_excluded_chips.py 073dd74 --source run-config --iogs 1-8

Hydra mode answers a different question: chips listed in excluded_chips inside
hydra network JSONs, if those fields exist.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Set, Tuple

HYDRA_RE = re.compile(r"configs/iog-(\d+)-pacman-tile-(\d+)-hydra-network\.json$")


class GitError(RuntimeError):
    pass


def run_git(args: Sequence[str], repo: Optional[Path] = None, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo) if repo else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def git_root(start: Path) -> Optional[Path]:
    try:
        root = run_git(["rev-parse", "--show-toplevel"], repo=start).strip()
    except GitError:
        return None
    return Path(root) if root else None


def git_show_text(commit: str, path: str, repo: Optional[Path]) -> str:
    return run_git(["show", f"{commit}:{path}"], repo=repo)


def git_metadata(commit: str, repo: Optional[Path]) -> Dict[str, str]:
    meta = {
        "requested": commit,
        "full_hash": "",
        "short_hash": commit,
        "date": "",
        "subject": "",
        "tags": "",
    }
    for key, fmt in (("full_hash", "%H"), ("date", "%ci"), ("subject", "%s")):
        try:
            meta[key] = run_git(["show", "-s", f"--format={fmt}", commit], repo=repo).strip()
        except GitError:
            pass
    if meta["full_hash"]:
        meta["short_hash"] = meta["full_hash"][:12]
    try:
        tags = run_git(["tag", "--points-at", commit], repo=repo).strip().splitlines()
        meta["tags"] = ";".join(t.strip() for t in tags if t.strip())
    except GitError:
        pass
    return meta


def to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s, 0)
        except ValueError:
            return None
    return None


def sorted_ints(values: Iterable[Any]) -> List[int]:
    out: Set[int] = set()
    for v in values:
        i = to_int(v)
        if i is not None:
            out.add(i)
    return sorted(out)


def values_as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def parse_iog_range(text: str) -> List[int]:
    out: Set[int] = set()
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece:
            a_s, b_s = piece.split("-", 1)
            a = to_int(a_s)
            b = to_int(b_s)
            if a is None or b is None:
                raise argparse.ArgumentTypeError(f"Invalid IO-group range: {piece!r}")
            lo, hi = sorted((a, b))
            out.update(range(lo, hi + 1))
        else:
            i = to_int(piece)
            if i is None:
                raise argparse.ArgumentTypeError(f"Invalid IO group: {piece!r}")
            out.add(i)
    return sorted(out)


def extract_iog_from_key(key: str) -> Optional[int]:
    patterns = [
        r"(?:^|_)io[_-]?group[_-]?(\d+)(?:_|$)",
        r"(?:^|_)iog[_-]?(\d+)(?:_|$)",
        r"(?:^|-)io[_-]?group[-_](\d+)(?:[-_]|$)",
        r"(?:^|-)iog[-_](\d+)(?:[-_]|$)",
    ]
    for pat in patterns:
        m = re.search(pat, key, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))
    # Common CRS pattern: io_group_pacman_tile_6
    m = re.search(r"_(\d+)$", key)
    if m and ("iog" in key.lower() or "io_group" in key.lower()):
        return int(m.group(1))
    return None


def add_active_tile_pairs_from_value(
    value: Any,
    pairs: Set[Tuple[int, int]],
    default_iog: Optional[int] = None,
) -> None:
    """Best-effort parser for io_group_pacman_tile data."""
    if value is None:
        return

    if isinstance(value, dict):
        for k, v in value.items():
            iog = to_int(k)
            if iog is None:
                iog = extract_iog_from_key(str(k)) or default_iog
            add_active_tile_pairs_from_value(v, pairs, default_iog=iog)
        return

    if isinstance(value, list):
        # List of explicit [iog, tile] pairs.
        if all(isinstance(x, (list, tuple)) and len(x) >= 2 for x in value):
            for x in value:
                if default_iog is None:
                    iog = to_int(x[0])
                    tile = to_int(x[1])
                else:
                    iog = default_iog
                    tile = to_int(x[0])
                if iog is not None and tile is not None:
                    pairs.add((iog, tile))
            return

        # List of active tiles for a known IO group.
        if default_iog is not None:
            for x in value:
                tile = to_int(x)
                if tile is not None:
                    pairs.add((default_iog, tile))
            return
        return

    tile = to_int(value)
    if default_iog is not None and tile is not None:
        pairs.add((default_iog, tile))


def find_active_tiles(run_config: Dict[str, Any]) -> Set[Tuple[int, int]]:
    pairs: Set[Tuple[int, int]] = set()
    for key, value in run_config.items():
        k = str(key)
        kl = k.lower()
        if "pacman_tile" in kl and ("io_group" in kl or "iog" in kl):
            iog = extract_iog_from_key(k)
            add_active_tile_pairs_from_value(value, pairs, default_iog=iog)
    return pairs


def find_key_value(data: Dict[str, Any], possible_names: Sequence[str]) -> Any:
    lowered = {str(k).lower(): v for k, v in data.items()}
    for name in possible_names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    # Also allow keys that start with requested stem, e.g. iog_exclude_6.
    combined: Dict[str, Any] = {}
    for name in possible_names:
        stem = name.lower().rstrip("_")
        for k, v in data.items():
            kl = str(k).lower().rstrip("_")
            if kl.startswith(stem):
                combined[str(k)] = v
    return combined or None


def parse_run_excludes(run_config: Dict[str, Any]) -> Dict[Tuple[int, int], Set[int]]:
    """Parse RUN_CONFIG iog_exclude-like structures into {(iog, tile): chips}."""
    raw = find_key_value(run_config, ["iog_exclude", "io_group_exclude", "excluded_chips"])
    out: DefaultDict[Tuple[int, int], Set[int]] = defaultdict(set)
    active_pairs = find_active_tiles(run_config)
    tiles_by_iog: DefaultDict[int, Set[int]] = defaultdict(set)
    for iog, tile in active_pairs:
        tiles_by_iog[iog].add(tile)

    def add(iog: Optional[int], tile: Optional[int], chips: Iterable[Any]) -> None:
        if iog is None:
            return
        chips_i = sorted_ints(chips)
        if not chips_i:
            return
        if tile is None:
            # Ambiguous iog-level excludes are rare.  If active tiles are known,
            # record them on every active tile for that IO group; otherwise tile 0
            # makes the ambiguity visible instead of silently dropping it.
            candidate_tiles = tiles_by_iog.get(iog) or {0}
            for t in candidate_tiles:
                out[(iog, t)].update(chips_i)
        else:
            out[(iog, tile)].update(chips_i)

    if raw is None:
        return dict(out)

    if isinstance(raw, dict):
        for k, v in raw.items():
            iog = to_int(k) or extract_iog_from_key(str(k))
            if isinstance(v, dict):
                for tk, chips in v.items():
                    tile = to_int(tk)
                    tk_l = str(tk).lower()
                    if tile is None and tk_l in {"chips", "chip_ids", "excluded_chips"}:
                        add(iog, None, values_as_list(chips))
                    else:
                        add(iog, tile, values_as_list(chips))
            elif isinstance(v, list) and all(isinstance(x, (list, tuple)) for x in v):
                for x in v:
                    if len(x) >= 3:
                        # Either [iog, tile, chip] if iog was not in the key,
                        # or [tile, chip, ...] if it was.
                        if iog is None:
                            add(to_int(x[0]), to_int(x[1]), [x[2]])
                        else:
                            add(iog, to_int(x[0]), [x[1]])
                    elif len(x) == 2:
                        add(iog, to_int(x[0]), [x[1]])
            else:
                add(iog, None, values_as_list(v))
        return dict(out)

    if isinstance(raw, list):
        for x in raw:
            if isinstance(x, dict):
                iog = to_int(x.get("io_group") or x.get("iog"))
                tile = to_int(x.get("tile") or x.get("pacman_tile"))
                chips = x.get("chips") or x.get("chip_ids") or x.get("excluded_chips") or x.get("chip_id")
                add(iog, tile, values_as_list(chips))
            elif isinstance(x, (list, tuple)) and len(x) >= 3:
                add(to_int(x[0]), to_int(x[1]), [x[2]])
        return dict(out)

    return dict(out)


def collect_key_values(obj: Any, key_name: str) -> List[Any]:
    found: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() == key_name.lower():
                found.append(v)
            found.extend(collect_key_values(v, key_name))
    elif isinstance(obj, list):
        for x in obj:
            found.extend(collect_key_values(x, key_name))
    return found


def flatten_ints(obj: Any) -> Set[int]:
    out: Set[int] = set()
    if isinstance(obj, dict):
        for k in ("chip_id", "chip", "id"):
            if k in obj:
                i = to_int(obj[k])
                if i is not None:
                    out.add(i)
        for v in obj.values():
            out.update(flatten_ints(v))
    elif isinstance(obj, list):
        for x in obj:
            out.update(flatten_ints(x))
    else:
        i = to_int(obj)
        if i is not None:
            out.add(i)
    return out


@dataclass(frozen=True)
class ExcludedChip:
    io_group: int
    tile: int
    chip_id: int
    source: str

    @property
    def legacy_label(self) -> str:
        return f"{self.tile}-{self.chip_id}"


def read_run_config_excluded(commit: str, repo: Optional[Path], run_config_path: str) -> List[ExcludedChip]:
    text = git_show_text(commit, run_config_path, repo)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse {run_config_path} at {commit}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{run_config_path} at {commit} is not a JSON object")

    excludes = parse_run_excludes(data)
    chips: List[ExcludedChip] = []
    for (iog, tile), chip_ids in excludes.items():
        for chip_id in sorted(chip_ids):
            chips.append(ExcludedChip(iog, tile, chip_id, "run-config"))
    chips.sort(key=lambda c: (c.io_group, c.tile, c.chip_id, c.source))
    return chips


def hydra_paths_at_commit(commit: str, repo: Optional[Path], hydra_dir: str) -> List[str]:
    # Use ls-tree and regex filtering rather than shell globs.  This keeps the
    # query independent of the current working tree.
    out = run_git(["ls-tree", "-r", "--name-only", commit, "--", hydra_dir], repo=repo)
    paths = []
    for line in out.splitlines():
        line = line.strip()
        if HYDRA_RE.search(line):
            paths.append(line)
    return sorted(paths)


def read_hydra_excluded(commit: str, repo: Optional[Path], hydra_dir: str) -> List[ExcludedChip]:
    chips: List[ExcludedChip] = []
    paths = hydra_paths_at_commit(commit, repo, hydra_dir)
    for path in paths:
        m = HYDRA_RE.search(path)
        if not m:
            continue
        iog = int(m.group(1))
        tile = int(m.group(2))
        text = git_show_text(commit, path, repo)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            print(f"WARNING: Could not parse {path} at {commit}: {exc}", file=sys.stderr)
            continue
        excluded: Set[int] = set()
        for value in collect_key_values(data, "excluded_chips"):
            excluded.update(flatten_ints(value))
        for chip_id in sorted(excluded):
            chips.append(ExcludedChip(iog, tile, chip_id, "hydra"))
    chips.sort(key=lambda c: (c.io_group, c.tile, c.chip_id, c.source))
    return chips


def filter_iogs(chips: Iterable[ExcludedChip], iogs: Optional[Sequence[int]]) -> List[ExcludedChip]:
    if not iogs:
        return sorted(chips, key=lambda c: (c.io_group, c.tile, c.chip_id, c.source))
    allowed = set(iogs)
    return sorted((c for c in chips if c.io_group in allowed), key=lambda c: (c.io_group, c.tile, c.chip_id, c.source))


def chip_map(chips: Iterable[ExcludedChip]) -> Dict[int, List[ExcludedChip]]:
    by_iog: DefaultDict[int, List[ExcludedChip]] = defaultdict(list)
    seen: Set[Tuple[int, int, int, str]] = set()
    for c in chips:
        key = (c.io_group, c.tile, c.chip_id, c.source)
        if key in seen:
            continue
        seen.add(key)
        by_iog[c.io_group].append(c)
    for iog in by_iog:
        by_iog[iog].sort(key=lambda c: (c.tile, c.chip_id, c.source))
    return dict(by_iog)


def print_legacy(chips: List[ExcludedChip], iogs: Optional[Sequence[int]], source_label: str) -> None:
    by_iog = chip_map(chips)
    if iogs:
        ordered_iogs = list(iogs)
    else:
        ordered_iogs = sorted(by_iog)
    total = len(chips)
    print(f"Total excluded chip entries ({source_label}): {total}")
    print()
    for iog in ordered_iogs:
        entries = by_iog.get(iog, [])
        print(f"Iog {iog} ({len(entries)})")
        if entries:
            print(", ".join(c.legacy_label for c in entries))
        else:
            print("-")


def print_table(chips: List[ExcludedChip]) -> None:
    print("io_group,tile,chip_id,source")
    for c in chips:
        print(f"{c.io_group},{c.tile},{c.chip_id},{c.source}")


def print_json(chips: List[ExcludedChip], meta: Dict[str, str], source_label: str) -> None:
    payload: Dict[str, Any] = {
        "commit": meta,
        "source": source_label,
        "total_excluded_chip_entries": len(chips),
        "excluded_chips": [
            {"io_group": c.io_group, "tile": c.tile, "chip_id": c.chip_id, "source": c.source}
            for c in chips
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def print_header(meta: Dict[str, str]) -> None:
    print(f"Commit: {meta.get('short_hash') or meta.get('requested')}")
    if meta.get("date"):
        print(f"Date: {meta['date']}")
    if meta.get("subject"):
        print(f"Subject: {meta['subject']}")
    if meta.get("tags"):
        print(f"Tags: {meta['tags']}")
    print()


def collect_chips(args: argparse.Namespace, repo: Optional[Path]) -> Tuple[List[ExcludedChip], str]:
    if args.source == "run-config":
        return read_run_config_excluded(args.commit, repo, args.run_config), f"{args.run_config}: iog_exclude"
    if args.source == "hydra":
        return read_hydra_excluded(args.commit, repo, args.hydra_dir), f"{args.hydra_dir}/iog-*-pacman-tile-*-hydra-network.json: excluded_chips"

    # both: keep separate source labels so overlapping entries are visible.
    chips = []
    chips.extend(read_run_config_excluded(args.commit, repo, args.run_config))
    chips.extend(read_hydra_excluded(args.commit, repo, args.hydra_dir))
    chips.sort(key=lambda c: (c.io_group, c.tile, c.chip_id, c.source))
    return chips, f"{args.run_config} + hydra excluded_chips"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read excluded chips from RUN_CONFIG.json or hydra JSONs at a specific Git commit, without checkout.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("commit", help="Git commit, tag, or ref, e.g. 073dd74")
    parser.add_argument(
        "--source",
        choices=["run-config", "hydra", "both"],
        default="run-config",
        help="Which committed files to inspect. run-config matches manual operator excludes.",
    )
    parser.add_argument("--run-config", default="RUN_CONFIG.json", help="Path to RUN_CONFIG.json inside the repo")
    parser.add_argument("--hydra-dir", default="configs", help="Directory containing hydra network JSONs inside the repo")
    parser.add_argument("--iogs", type=parse_iog_range, default=None, help="Comma/range list, e.g. 1-8 or 1,3,6")
    parser.add_argument(
        "--format",
        choices=["legacy", "table", "csv", "json"],
        default="legacy",
        help="Output format. table and csv are the same simple CSV table.",
    )
    parser.add_argument("--no-header", action="store_true", help="Suppress commit metadata header for text formats")
    parser.add_argument("--repo", default=None, help="Path inside the Git repo; default: auto-detect from cwd")

    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve() if args.repo else git_root(Path.cwd())

    try:
        meta = git_metadata(args.commit, repo)
        chips, source_label = collect_chips(args, repo)
        chips = filter_iogs(chips, args.iogs)
    except Exception as exc:  # noqa: BLE001 - command-line tool should give clear error
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print_json(chips, meta, source_label)
        return 0

    if not args.no_header:
        print_header(meta)
        print(f"Source: {source_label}")
        print()

    if args.format in {"table", "csv"}:
        print_table(chips)
    else:
        print_legacy(chips, args.iogs, source_label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
