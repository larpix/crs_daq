#!/usr/bin/env python3
"""
Query committed CRS cockpit-state history without requiring DAQ-local paths.

This companion tool intentionally reads only files tracked in the Git repo,
using `git show <commit>:<path>`. It should run on any computer after `git pull`.
It does NOT inspect /data/CRS/asic_configs or other DAQ-local raw config paths.

Initial supported queries:

  # Show manually excluded chips from RUN_CONFIG summary at a commit
  python dev_tools/crs_cockpit_history.py excluded-chips 073dd74

  # Same, in a CSV-like table
  python dev_tools/crs_cockpit_history.py excluded-chips 073dd74 --format table

  # Show hydra connected/excluded/root counts from committed hydra summary
  python dev_tools/crs_cockpit_history.py hydra-counts 073dd74

Expected committed inputs, produced by crs_cockpit_snapshot.py:

  config_history/current/run_config_summary.csv
  config_history/current/hydra_summary.csv
  config_history/current/asic_config_summary.csv
  config_history/current/cockpit_summary.json
  config_history/current/warnings.txt
  config_history/current/changes_since_previous.txt
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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

CSV_NONE = ""
DEFAULT_HISTORY_DIR = "config_history/current"
RUN_CONFIG_SUMMARY = f"{DEFAULT_HISTORY_DIR}/run_config_summary.csv"
HYDRA_SUMMARY = f"{DEFAULT_HISTORY_DIR}/hydra_summary.csv"
COCKPIT_SUMMARY = f"{DEFAULT_HISTORY_DIR}/cockpit_summary.json"


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------


class GitReadError(RuntimeError):
    """Raised when a tracked file cannot be read at the requested commit."""


def run_git(args: Sequence[str], cwd: Optional[Path] = None, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        cmd = "git " + " ".join(args)
        raise GitReadError(f"{cmd} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def git_root(start: Path) -> Optional[Path]:
    try:
        out = run_git(["rev-parse", "--show-toplevel"], cwd=start).strip()
    except GitReadError:
        return None
    return Path(out) if out else None


def git_show_text(commit: str, path: str, repo: Optional[Path]) -> str:
    try:
        return run_git(["show", f"{commit}:{path}"], cwd=repo)
    except GitReadError as exc:
        raise GitReadError(
            f"Could not read {path!r} at commit {commit!r}.\n"
            "This history tool only reads committed compact summaries. "
            "Check that the commit includes config_history/current outputs, or use a later commit.\n"
            f"Original error:\n{exc}"
        ) from exc


def git_metadata(commit: str, repo: Optional[Path]) -> Dict[str, str]:
    meta: Dict[str, str] = {
        "requested": commit,
        "full_hash": "",
        "short_hash": commit,
        "date": "",
        "subject": "",
        "tags": "",
    }
    try:
        meta["full_hash"] = run_git(["rev-parse", commit], cwd=repo).strip()
        if meta["full_hash"]:
            meta["short_hash"] = meta["full_hash"][:12]
    except GitReadError:
        pass
    try:
        meta["date"] = run_git(["show", "-s", "--format=%ci", commit], cwd=repo).strip()
    except GitReadError:
        pass
    try:
        meta["subject"] = run_git(["show", "-s", "--format=%s", commit], cwd=repo).strip()
    except GitReadError:
        pass
    try:
        tags = run_git(["tag", "--points-at", commit], cwd=repo).strip().splitlines()
        meta["tags"] = ";".join(t for t in tags if t.strip())
    except GitReadError:
        pass
    return meta


def read_csv_at_commit(commit: str, path: str, repo: Optional[Path]) -> List[Dict[str, str]]:
    text = git_show_text(commit, path, repo)
    return list(csv.DictReader(io.StringIO(text)))


def read_json_at_commit(commit: str, path: str, repo: Optional[Path]) -> Optional[Any]:
    try:
        text = git_show_text(commit, path, repo)
    except GitReadError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


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


def parse_int_set(text: Any) -> Set[int]:
    if text is None:
        return set()
    if isinstance(text, (list, tuple, set)):
        out: Set[int] = set()
        for item in text:
            i = to_int(item)
            if i is not None:
                out.add(i)
        return out
    s = str(text).strip()
    if not s:
        return set()
    out: Set[int] = set()
    for part in re.split(r"[;,\s]+", s):
        if not part:
            continue
        i = to_int(part)
        if i is not None:
            out.add(i)
    return out


def truthy(text: Any) -> bool:
    return str(text).strip().lower() in {"1", "true", "yes", "y", "active"}


def natural_iog_list(rows: Sequence[Dict[str, str]], forced_iogs: Optional[Sequence[int]] = None) -> List[int]:
    if forced_iogs:
        return sorted(set(forced_iogs))
    iogs: Set[int] = set()
    for row in rows:
        iog = to_int(row.get("io_group"))
        if iog is not None:
            iogs.add(iog)
    return sorted(iogs)


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
                raise argparse.ArgumentTypeError(f"Invalid IO group range: {piece!r}")
            lo, hi = sorted((a, b))
            out.update(range(lo, hi + 1))
        else:
            i = to_int(piece)
            if i is None:
                raise argparse.ArgumentTypeError(f"Invalid IO group: {piece!r}")
            out.add(i)
    return sorted(out)


@dataclass(frozen=True)
class ExcludedChip:
    io_group: int
    tile: int
    chip_id: int
    source_active: bool

    @property
    def legacy_label(self) -> str:
        return f"{self.tile}-{self.chip_id}"


# -----------------------------------------------------------------------------
# Excluded chip query
# -----------------------------------------------------------------------------


def collect_run_config_excludes(
    rows: Sequence[Dict[str, str]],
    include_inactive: bool = False,
) -> List[ExcludedChip]:
    chips: List[ExcludedChip] = []
    for row in rows:
        iog = to_int(row.get("io_group"))
        tile = to_int(row.get("tile"))
        if iog is None or tile is None:
            continue
        active = truthy(row.get("active"))
        if not active and not include_inactive:
            continue
        for chip in sorted(parse_int_set(row.get("excluded_chips"))):
            chips.append(ExcludedChip(iog, tile, chip, active))
    chips.sort(key=lambda c: (c.io_group, c.tile, c.chip_id))
    return chips


def print_excluded_legacy(
    chips: Sequence[ExcludedChip],
    iogs: Sequence[int],
    meta: Dict[str, str],
    source_path: str,
    include_header: bool = True,
) -> None:
    by_iog: Dict[int, List[ExcludedChip]] = defaultdict(list)
    for chip in chips:
        by_iog[chip.io_group].append(chip)

    if include_header:
        print(f"Commit: {meta.get('short_hash') or meta.get('requested')}")
        if meta.get("date"):
            print(f"Date: {meta['date']}")
        if meta.get("subject"):
            print(f"Subject: {meta['subject']}")
        if meta.get("tags"):
            print(f"Tags: {meta['tags']}")
        print(f"Source: {source_path}")
        print(f"Total manually excluded chip entries: {len(chips)}")
        print("")

    for iog in iogs:
        entries = by_iog.get(iog, [])
        print(f"Iog {iog} ({len(entries)})")
        if entries:
            print(", ".join(chip.legacy_label for chip in entries))
        else:
            print("-")


def print_excluded_table(chips: Sequence[ExcludedChip], meta: Dict[str, str], source_path: str) -> None:
    print(f"# commit={meta.get('short_hash') or meta.get('requested')} source={source_path} total={len(chips)}")
    print("io_group,tile,chip_id,active")
    for chip in chips:
        print(f"{chip.io_group},{chip.tile},{chip.chip_id},{str(chip.source_active).lower()}")


def print_excluded_markdown(chips: Sequence[ExcludedChip], iogs: Sequence[int], meta: Dict[str, str], source_path: str) -> None:
    print(f"**Commit:** `{meta.get('short_hash') or meta.get('requested')}`  ")
    if meta.get("subject"):
        print(f"**Subject:** {meta['subject']}  ")
    print(f"**Source:** `{source_path}`  ")
    print(f"**Total manually excluded chip entries:** {len(chips)}")
    print("")
    print("| IO group | Count | Tile-chip entries |")
    print("|---:|---:|---|")
    by_iog: Dict[int, List[ExcludedChip]] = defaultdict(list)
    for chip in chips:
        by_iog[chip.io_group].append(chip)
    for iog in iogs:
        entries = by_iog.get(iog, [])
        labels = ", ".join(chip.legacy_label for chip in entries) if entries else "-"
        print(f"| {iog} | {len(entries)} | {labels} |")


def cmd_excluded_chips(args: argparse.Namespace) -> int:
    repo = git_root(Path.cwd())
    rows = read_csv_at_commit(args.commit, args.run_config_summary, repo)
    meta = git_metadata(args.commit, repo)
    chips = collect_run_config_excludes(rows, include_inactive=args.include_inactive)
    iogs = natural_iog_list(rows, args.iogs)

    if args.format == "legacy":
        print_excluded_legacy(
            chips,
            iogs,
            meta,
            args.run_config_summary,
            include_header=not args.no_header,
        )
    elif args.format == "table":
        print_excluded_table(chips, meta, args.run_config_summary)
    elif args.format == "markdown":
        print_excluded_markdown(chips, iogs, meta, args.run_config_summary)
    else:
        raise ValueError(f"Unknown format: {args.format}")

    return 0


# -----------------------------------------------------------------------------
# Hydra counts query
# -----------------------------------------------------------------------------


def hydra_row_sort_key(row: Dict[str, str]) -> Tuple[int, int, str]:
    iog = to_int(row.get("io_group"))
    tile = to_int(row.get("tile"))
    return (iog if iog is not None else 9999, tile if tile is not None else 9999, row.get("path", ""))


def cmd_hydra_counts(args: argparse.Namespace) -> int:
    repo = git_root(Path.cwd())
    rows = read_csv_at_commit(args.commit, args.hydra_summary, repo)
    rows = sorted(rows, key=hydra_row_sort_key)
    meta = git_metadata(args.commit, repo)

    print(f"Commit: {meta.get('short_hash') or meta.get('requested')}")
    if meta.get("date"):
        print(f"Date: {meta['date']}")
    if meta.get("subject"):
        print(f"Subject: {meta['subject']}")
    if meta.get("tags"):
        print(f"Tags: {meta['tags']}")
    print(f"Source: {args.hydra_summary}")
    print("")

    print("io_group,tile,connected_chip_count,n_excluded_chips,n_root_chips,root_chips,max_chain_depth,path")
    for row in rows:
        print(
            ",".join(
                [
                    row.get("io_group", CSV_NONE),
                    row.get("tile", CSV_NONE),
                    row.get("connected_chip_count", CSV_NONE),
                    row.get("n_excluded_chips", CSV_NONE),
                    row.get("n_root_chips", CSV_NONE),
                    row.get("root_chips", CSV_NONE),
                    row.get("max_chain_depth", CSV_NONE),
                    row.get("path", CSV_NONE),
                ]
            )
        )
    return 0


# -----------------------------------------------------------------------------
# Cockpit summary query
# -----------------------------------------------------------------------------


def cmd_summary(args: argparse.Namespace) -> int:
    repo = git_root(Path.cwd())
    data = read_json_at_commit(args.commit, args.cockpit_summary, repo)
    meta = git_metadata(args.commit, repo)
    if not isinstance(data, dict):
        raise GitReadError(f"Could not read a JSON cockpit summary at {args.commit}:{args.cockpit_summary}")

    print(f"Commit: {meta.get('short_hash') or meta.get('requested')}")
    if meta.get("date"):
        print(f"Date: {meta['date']}")
    if meta.get("subject"):
        print(f"Subject: {meta['subject']}")
    if meta.get("tags"):
        print(f"Tags: {meta['tags']}")
    print(f"Source: {args.cockpit_summary}")
    print("")

    counts = data.get("counts", {})
    if isinstance(counts, dict):
        for key in sorted(counts):
            print(f"{key}: {counts[key]}")
    else:
        print(json.dumps(data, indent=2, sort_keys=True))
    return 0


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query committed CRS cockpit-state summaries across Git history.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_excl = sub.add_parser(
        "excluded-chips",
        help="Show manually excluded chips from committed run_config_summary.csv at a commit.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_excl.add_argument("commit", help="Commit-ish to query, e.g. 073dd74, HEAD, HEAD~3, tag name")
    p_excl.add_argument(
        "--run-config-summary",
        default=RUN_CONFIG_SUMMARY,
        help="Tracked CSV summary to read via git show <commit>:<path>.",
    )
    p_excl.add_argument(
        "--format",
        choices=["legacy", "table", "markdown"],
        default="legacy",
        help="Output format.",
    )
    p_excl.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include rows marked inactive in run_config_summary.csv.",
    )
    p_excl.add_argument(
        "--iogs",
        type=parse_iog_range,
        default=None,
        help="Force IO groups to print, e.g. '1-8' or '1,2,5'. Default: IO groups present in the CSV.",
    )
    p_excl.add_argument("--no-header", action="store_true", help="Suppress header in legacy format.")
    p_excl.set_defaults(func=cmd_excluded_chips)

    p_hydra = sub.add_parser(
        "hydra-counts",
        help="Show connected/excluded/root counts from committed hydra_summary.csv at a commit.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_hydra.add_argument("commit", help="Commit-ish to query, e.g. 073dd74, HEAD, HEAD~3, tag name")
    p_hydra.add_argument(
        "--hydra-summary",
        default=HYDRA_SUMMARY,
        help="Tracked CSV summary to read via git show <commit>:<path>.",
    )
    p_hydra.set_defaults(func=cmd_hydra_counts)

    p_summary = sub.add_parser(
        "summary",
        help="Show high-level cockpit_summary.json counts at a commit.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_summary.add_argument("commit", help="Commit-ish to query, e.g. 073dd74, HEAD, HEAD~3, tag name")
    p_summary.add_argument(
        "--cockpit-summary",
        default=COCKPIT_SUMMARY,
        help="Tracked JSON summary to read via git show <commit>:<path>.",
    )
    p_summary.set_defaults(func=cmd_summary)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except GitReadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 141


if __name__ == "__main__":
    raise SystemExit(main())
