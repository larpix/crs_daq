#!/usr/bin/env python3
"""
Create a compact, commit-friendly snapshot of the CRS cockpit state.

This tool is intended to be run on a DAQ machine where live detector-control
state and raw ASIC config directories are available.  It writes compact CSV/JSON
summaries that can be safely committed to the crs_daq repository.

Typical use:

    python dev_tools/crs_cockpit_snapshot.py \
      --run-config RUN_CONFIG.json \
      --hydra-glob 'configs/iog-*-pacman-tile-*-hydra-network.json' \
      --asic-config-dir /data/CRS/asic_configs/ParameterScan/NominalTest \
      --out-dir config_history/current

The companion history/plotting tool should only use the committed outputs from
config_history/current across Git commits. It should not depend on DAQ-local raw
ASIC config directories.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import glob
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


CSV_NONE = ""
PROVENANCE_KEYS = {
    "meta",
    "created",
    "last_update",
    "description",
    "toggle_lists",
    "toggle_list",
    "toggle_data",
    "toggle_file",
    "toggle_files",
    "toggle_path",
    "toggle_paths",
    "source_file",
    "source_path",
}

RUN_CONFIG_FIELDS = [
    "io_group",
    "tile",
    "active",
    "pacman_version",
    "asic_version",
    "VDDD_DAC",
    "VDDA_DAC",
    "excluded_chips",
    "n_excluded_chips",
]

HYDRA_FIELDS = [
    "io_group",
    "tile",
    "path",
    "connected_chip_count",
    "root_chips",
    "n_root_chips",
    "branch_count",
    "max_chain_depth",
    "excluded_chips",
    "n_excluded_chips",
    "connected_chips",
]

ASIC_FIELDS = [
    "module",
    "io_group",
    "io_channel",
    "chip_id",
    "path",
    "threshold_global",
    "vref_dac",
    "vcm_dac",
    "adc_hold_delay",
    "periodic_reset_cycles",
    "enable_periodic_reset",
    "enable_rolling_periodic_reset",
    "enable_miso_upstream",
    "enable_miso_downstream",
    "enable_miso_differential",
    "enable_mosi",
    "n_disabled_channels",
    "disabled_channels",
    "n_csa_disabled_channels",
    "csa_disabled_channels",
    "pixel_trim_min",
    "pixel_trim_max",
    "pixel_trim_mean",
    "pixel_trim_hash",
    "state_hash_without_meta",
]


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------


def warn(msg: str, warnings: List[str]) -> None:
    warnings.append(msg)


def load_json(path: Path, warnings: Optional[List[str]] = None) -> Optional[Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001 - report and continue in operations
        if warnings is not None:
            warn(f"Could not read JSON {path}: {exc}", warnings)
        return None


def canonical_json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json_dumps(obj).encode("utf-8")).hexdigest()


def strip_provenance(obj: Any) -> Any:
    """Remove metadata/provenance-only fields before hashing ASIC state."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            key = str(k)
            key_l = key.lower()
            if key_l in PROVENANCE_KEYS:
                continue
            # Be conservative but remove obvious path/toggle provenance fields.
            if "toggle" in key_l:
                continue
            if key_l.endswith("_path") or key_l.endswith("_paths"):
                continue
            out[key] = strip_provenance(v)
        return out
    if isinstance(obj, list):
        return [strip_provenance(x) for x in obj]
    return obj


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


def natural_sort_key(value: Any) -> Tuple[int, Any]:
    i = to_int(value)
    if i is not None:
        return (0, i)
    return (1, str(value))


def sorted_ints(values: Iterable[Any]) -> List[int]:
    out: Set[int] = set()
    for v in values:
        i = to_int(v)
        if i is not None:
            out.add(i)
    return sorted(out)


def semicolon_list(values: Iterable[Any]) -> str:
    vals = sorted_ints(values)
    return ";".join(str(v) for v in vals)


def parse_semicolon_ints(text: Any) -> Set[int]:
    if text is None:
        return set()
    if isinstance(text, (list, tuple, set)):
        return set(sorted_ints(text))
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


def stringify(value: Any) -> str:
    if value is None:
        return CSV_NONE
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    return canonical_json_dumps(value)


def relpath_if_possible(path: Path, base: Optional[Path]) -> str:
    try:
        if base is not None:
            return str(path.resolve().relative_to(base.resolve()))
    except Exception:  # noqa: BLE001
        pass
    return str(path)


def run_git(args: Sequence[str], cwd: Optional[Path] = None) -> Optional[str]:
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return p.stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def git_root(start: Path) -> Optional[Path]:
    root = run_git(["rev-parse", "--show-toplevel"], cwd=start)
    if not root:
        return None
    return Path(root)


def git_head_info(repo: Optional[Path]) -> Tuple[str, str]:
    if repo is None:
        return (CSV_NONE, CSV_NONE)
    commit = run_git(["rev-parse", "--short=12", "HEAD"], cwd=repo) or CSV_NONE
    branch = run_git(["branch", "--show-current"], cwd=repo) or CSV_NONE
    return commit, branch


def git_show_text(repo: Optional[Path], rel_path: str) -> Optional[str]:
    if repo is None:
        return None
    return run_git(["show", f"HEAD:{rel_path}"], cwd=repo)


def read_text_if_exists(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception:
        return None


# -----------------------------------------------------------------------------
# RUN_CONFIG parsing
# -----------------------------------------------------------------------------


def dict_get_case_insensitive(d: Dict[str, Any], *names: str) -> Any:
    lowered = {str(k).lower(): v for k, v in d.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def values_as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def extract_iog_from_key(key: str) -> Optional[int]:
    patterns = [
        r"(?:^|_)io[_-]?group[_-]?(\d+)(?:_|$)",
        r"(?:^|_)iog[_-]?(\d+)(?:_|$)",
        r"(?:^|_)io[_-]?group[_-].*?[_-](\d+)$",
        r"(?:^|_)iog[_-].*?[_-](\d+)$",
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
        # List of [iog, tile] pairs.
        if all(isinstance(x, (list, tuple)) and len(x) >= 2 for x in value):
            for x in value:
                iog = to_int(x[0]) if default_iog is None else default_iog
                tile = to_int(x[1]) if default_iog is None else to_int(x[0])
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

        # Ambiguous list without IO group. Ignore rather than inventing.
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


def lookup_iog(mapping: Any, iog: int) -> Any:
    if mapping is None:
        return None
    if not isinstance(mapping, dict):
        return mapping
    for key in (iog, str(iog), f"iog{iog}", f"iog_{iog}", f"io_group_{iog}"):
        if key in mapping:
            return mapping[key]
    for k, v in mapping.items():
        if to_int(k) == iog:
            return v
    return None


def lookup_iog_tile(mapping: Any, iog: int, tile: int) -> Any:
    val = lookup_iog(mapping, iog)
    if isinstance(val, dict):
        for key in (tile, str(tile), f"tile{tile}", f"tile_{tile}", f"pacman_tile_{tile}"):
            if key in val:
                return val[key]
        for k, v in val.items():
            if to_int(k) == tile:
                return v
        return None
    if isinstance(val, list):
        # If it is a list of [tile, value] pairs, use those.
        for x in val:
            if isinstance(x, (list, tuple)) and len(x) >= 2 and to_int(x[0]) == tile:
                return x[1]
        # Otherwise use the same value for all tiles in this IO group.
        return val
    return val


def find_key_value(data: Dict[str, Any], possible_names: Sequence[str]) -> Any:
    lowered = {str(k).lower(): v for k, v in data.items()}
    for name in possible_names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    # Also allow keys that start with the requested stem, e.g. iog_pacman_version_6.
    combined: Dict[str, Any] = {}
    for name in possible_names:
        stem = name.lower().rstrip("_")
        for k, v in data.items():
            kl = str(k).lower().rstrip("_")
            if kl.startswith(stem):
                combined[str(k)] = v
    return combined or None


def parse_run_excludes(run_config: Dict[str, Any]) -> Dict[Tuple[int, int], Set[int]]:
    """Parse iog_exclude into {(iog, tile): {chip_ids}} as best as possible."""
    raw = find_key_value(run_config, ["iog_exclude", "io_group_exclude", "excluded_chips"])
    out: Dict[Tuple[int, int], Set[int]] = defaultdict(set)
    active_pairs = find_active_tiles(run_config)
    tiles_by_iog: Dict[int, Set[int]] = defaultdict(set)
    for iog, tile in active_pairs:
        tiles_by_iog[iog].add(tile)

    def add(iog: Optional[int], tile: Optional[int], chips: Iterable[Any]) -> None:
        if iog is None:
            return
        chips_i = sorted_ints(chips)
        if tile is None:
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
                    if tile is None and str(tk).lower() in {"chips", "excluded_chips"}:
                        add(iog, None, values_as_list(chips))
                    else:
                        add(iog, tile, values_as_list(chips))
            elif isinstance(v, list) and all(isinstance(x, (list, tuple)) for x in v):
                for x in v:
                    if len(x) >= 3:
                        # [tile, chip] or [iog, tile, chip]
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


def parse_run_config(path: Path, hydra_pairs: Set[Tuple[int, int]], warnings: List[str]) -> List[Dict[str, str]]:
    data = load_json(path, warnings)
    if not isinstance(data, dict):
        warn(f"RUN_CONFIG is missing or not a JSON object: {path}", warnings)
        return []

    active_pairs = find_active_tiles(data)
    excludes = parse_run_excludes(data)

    pacman_versions = find_key_value(data, ["iog_pacman_version_", "iog_pacman_version", "pacman_version"])
    asic_versions = find_key_value(data, ["io_group_asic_version_", "io_group_asic_version", "iog_asic_version", "asic_version"])
    vddd = find_key_value(data, ["iog_VDDD_DAC", "VDDD_DAC", "iog_vddd_dac"])
    vdda = find_key_value(data, ["iog_VDDA_DAC", "VDDA_DAC", "iog_vdda_dac"])

    # Include active run-config pairs, hydra pairs, and explicit exclude pairs.
    all_pairs = set(active_pairs) | set(hydra_pairs) | set(excludes.keys())

    rows: List[Dict[str, str]] = []
    for iog, tile in sorted(all_pairs):
        excluded = excludes.get((iog, tile), set())
        rows.append(
            {
                "io_group": str(iog),
                "tile": str(tile),
                "active": "true" if (iog, tile) in active_pairs else "false",
                "pacman_version": stringify(lookup_iog_tile(pacman_versions, iog, tile)),
                "asic_version": stringify(lookup_iog_tile(asic_versions, iog, tile)),
                "VDDD_DAC": stringify(lookup_iog_tile(vddd, iog, tile)),
                "VDDA_DAC": stringify(lookup_iog_tile(vdda, iog, tile)),
                "excluded_chips": semicolon_list(excluded),
                "n_excluded_chips": str(len(excluded)),
            }
        )
    return rows


# -----------------------------------------------------------------------------
# Hydra parsing
# -----------------------------------------------------------------------------


HYDRA_RE = re.compile(r"iog-(\d+)-pacman-tile-(\d+)-hydra-network\.json$")


def parse_hydra_path(path: Path) -> Tuple[Optional[int], Optional[int]]:
    m = HYDRA_RE.search(str(path))
    if m:
        return int(m.group(1)), int(m.group(2))
    s = str(path)
    iog = None
    tile = None
    m_iog = re.search(r"iog[-_](\d+)", s, flags=re.IGNORECASE)
    m_tile = re.search(r"(?:pacman[-_])?tile[-_](\d+)", s, flags=re.IGNORECASE)
    if m_iog:
        iog = int(m_iog.group(1))
    if m_tile:
        tile = int(m_tile.group(1))
    return iog, tile


def node_chip_id(node: Any) -> Optional[Any]:
    if not isinstance(node, dict):
        return None
    for key in ("chip_id", "chip", "id", "name"):
        if key in node:
            return node[key]
    return None


def is_ext_node(node: Any) -> bool:
    cid = node_chip_id(node)
    return isinstance(cid, str) and cid.lower() == "ext"


def numeric_chip_id(node: Any) -> Optional[int]:
    cid = node_chip_id(node)
    return to_int(cid)


def child_nodes(node: Any) -> List[Any]:
    """Best-effort extraction of child nodes from common hydra-network shapes."""
    if isinstance(node, list):
        return list(node)
    if not isinstance(node, dict):
        return []

    children: List[Any] = []
    preferred_keys = [
        "nodes",
        "children",
        "downstream",
        "next",
        "branches",
        "chains",
        "network",
    ]
    for key in preferred_keys:
        val = node.get(key)
        if isinstance(val, list):
            children.extend(val)
        elif isinstance(val, dict):
            # A mapping can either be a child node or a branch dictionary.
            if any(k in val for k in ("chip_id", "nodes", "children")):
                children.append(val)
            else:
                children.extend(val.values())

    # Some files encode the ext node as {"ext": [...]} or branch maps.
    for key, val in node.items():
        key_l = str(key).lower()
        if key_l in preferred_keys or key_l in {"chip_id", "chip", "id", "name", "excluded_chips"}:
            continue
        if key_l == "ext":
            if isinstance(val, list):
                children.extend(val)
            elif isinstance(val, dict):
                children.append(val)
        elif isinstance(val, dict) and any(k in val for k in ("chip_id", "nodes", "children")):
            children.append(val)
    return children


def find_ext_nodes(obj: Any) -> List[Any]:
    found: List[Any] = []
    if isinstance(obj, dict):
        if is_ext_node(obj) or "ext" in {str(k).lower() for k in obj.keys()}:
            found.append(obj)
        for v in obj.values():
            found.extend(find_ext_nodes(v))
    elif isinstance(obj, list):
        for x in obj:
            found.extend(find_ext_nodes(x))
    return found


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


def flatten_chips_from_any(obj: Any) -> Set[int]:
    chips: Set[int] = set()
    if isinstance(obj, dict):
        cid = numeric_chip_id(obj)
        if cid is not None:
            chips.add(cid)
        for v in obj.values():
            chips.update(flatten_chips_from_any(v))
    elif isinstance(obj, list):
        for x in obj:
            chips.update(flatten_chips_from_any(x))
    else:
        i = to_int(obj)
        if i is not None:
            chips.add(i)
    return chips


def longest_depth_from(node: Any, seen: Optional[Set[int]] = None) -> int:
    """Longest reachable numeric-chip path length below node."""
    if seen is None:
        seen = set()
    cid = numeric_chip_id(node)
    self_count = 0
    local_seen = set(seen)
    if cid is not None:
        if cid in local_seen:
            return 0
        local_seen.add(cid)
        self_count = 1
    children = child_nodes(node)
    if not children:
        return self_count
    child_max = max(longest_depth_from(child, local_seen) for child in children)
    return self_count + child_max


def top_level_root_nodes(network: Any) -> List[Any]:
    ext_nodes = find_ext_nodes(network)
    roots: List[Any] = []
    for ext in ext_nodes:
        roots.extend(child_nodes(ext))
    if roots:
        return roots

    # If there is no explicit ext node, treat the network's first layer as roots.
    if isinstance(network, dict) and "network" in network:
        return child_nodes(network["network"])
    return child_nodes(network)


def parse_hydra_file(path: Path, repo: Optional[Path], warnings: List[str]) -> Optional[Dict[str, str]]:
    data = load_json(path, warnings)
    if data is None:
        return None
    iog, tile = parse_hydra_path(path)
    if iog is None or tile is None:
        warn(f"Could not parse IO group/tile from hydra filename: {path}", warnings)

    network = data.get("network", data) if isinstance(data, dict) else data
    connected = flatten_chips_from_any(network)

    excluded: Set[int] = set()
    for v in collect_key_values(data, "excluded_chips"):
        excluded.update(flatten_chips_from_any(v))

    root_nodes = top_level_root_nodes(network)
    roots: Set[int] = set()
    for node in root_nodes:
        cid = numeric_chip_id(node)
        if cid is not None:
            roots.add(cid)
        else:
            # Sometimes a branch wrapper has the chip one level lower.
            child_cids = [numeric_chip_id(child) for child in child_nodes(node)]
            roots.update(c for c in child_cids if c is not None)

    max_depth = 0
    for node in root_nodes:
        max_depth = max(max_depth, longest_depth_from(node))
    if not root_nodes and connected:
        max_depth = 1

    return {
        "io_group": stringify(iog),
        "tile": stringify(tile),
        "path": relpath_if_possible(path, repo),
        "connected_chip_count": str(len(connected)),
        "root_chips": semicolon_list(roots),
        "n_root_chips": str(len(roots)),
        "branch_count": str(len(root_nodes) if root_nodes else len(roots)),
        "max_chain_depth": str(max_depth),
        "excluded_chips": semicolon_list(excluded),
        "n_excluded_chips": str(len(excluded)),
        "connected_chips": semicolon_list(connected),
    }


def parse_hydra_files(patterns: Sequence[str], repo: Optional[Path], warnings: List[str]) -> List[Dict[str, str]]:
    paths: List[Path] = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if not matches:
            warn(f"Hydra glob matched no files: {pattern}", warnings)
        paths.extend(Path(m) for m in matches)
    unique_paths = sorted(set(paths), key=lambda p: str(p))

    rows: List[Dict[str, str]] = []
    for path in unique_paths:
        row = parse_hydra_file(path, repo, warnings)
        if row is not None:
            rows.append(row)

    rows.sort(key=lambda r: (to_int(r["io_group"]) or -1, to_int(r["tile"]) or -1, r["path"]))
    return rows


# -----------------------------------------------------------------------------
# ASIC config parsing
# -----------------------------------------------------------------------------


ASIC_RE = re.compile(r"config_(\d+)-(\d+)-(\d+)\.json$")
MODULE_RE = re.compile(r"^m(\d+)$", flags=re.IGNORECASE)
ASIC_STATE_KEYS = [
    "threshold_global",
    "vref_dac",
    "vcm_dac",
    "adc_hold_delay",
    "periodic_reset_cycles",
    "enable_periodic_reset",
    "enable_rolling_periodic_reset",
    "enable_miso_upstream",
    "enable_miso_downstream",
    "enable_miso_differential",
    "enable_mosi",
]


def recursive_find_key(obj: Any, target_key: str) -> Any:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) == target_key:
                return v
        # Prefer obvious register/config containers before arbitrary recursion.
        for container in ("registers", "register_values", "config", "chip_config"):
            if container in obj:
                val = recursive_find_key(obj[container], target_key)
                if val is not None:
                    return val
        for v in obj.values():
            val = recursive_find_key(v, target_key)
            if val is not None:
                return val
    elif isinstance(obj, list):
        for x in obj:
            val = recursive_find_key(x, target_key)
            if val is not None:
                return val
    return None


def boolish_disabled(value: Any) -> bool:
    """Interpret truthy channel_mask values as masked/disabled."""
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"0", "false", "f", "no", "n", "enabled"}:
            return False
        if s in {"1", "true", "t", "yes", "y", "disabled", "masked"}:
            return True
    return bool(value)


def boolish_enabled(value: Any) -> bool:
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"0", "false", "f", "no", "n", "disabled", "masked"}:
            return False
        if s in {"1", "true", "t", "yes", "y", "enabled"}:
            return True
    return bool(value)


def disabled_from_channel_mask(mask: Any) -> List[int]:
    if not isinstance(mask, list):
        return []
    return [idx for idx, val in enumerate(mask) if boolish_disabled(val)]


def disabled_from_csa_enable(csa_enable: Any) -> List[int]:
    if not isinstance(csa_enable, list):
        return []
    return [idx for idx, val in enumerate(csa_enable) if not boolish_enabled(val)]


def numeric_list(value: Any) -> List[float]:
    if not isinstance(value, list):
        return []
    out: List[float] = []
    for x in value:
        try:
            if isinstance(x, bool):
                continue
            out.append(float(x))
        except Exception:  # noqa: BLE001
            continue
    return out


def module_from_path(path: Path) -> str:
    for parent in [path.parent, *path.parents]:
        m = MODULE_RE.match(parent.name)
        if m:
            return m.group(1)
    return CSV_NONE


def parse_asic_file(path: Path, repo: Optional[Path], warnings: List[str]) -> Optional[Dict[str, str]]:
    m = ASIC_RE.search(path.name)
    if not m:
        return None
    io_group, io_channel, chip_id = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    module = module_from_path(path)

    data = load_json(path, warnings)
    if data is None:
        return None

    stripped = strip_provenance(data)
    row: Dict[str, str] = {
        "module": module,
        "io_group": str(io_group),
        "io_channel": str(io_channel),
        "chip_id": str(chip_id),
        "path": relpath_if_possible(path, repo),
    }

    for key in ASIC_STATE_KEYS:
        row[key] = stringify(recursive_find_key(data, key))

    mask = recursive_find_key(data, "channel_mask")
    disabled_channels = disabled_from_channel_mask(mask)
    row["n_disabled_channels"] = str(len(disabled_channels))
    row["disabled_channels"] = semicolon_list(disabled_channels)

    csa = recursive_find_key(data, "csa_enable")
    csa_disabled = disabled_from_csa_enable(csa)
    row["n_csa_disabled_channels"] = str(len(csa_disabled))
    row["csa_disabled_channels"] = semicolon_list(csa_disabled)

    pixel_trim = recursive_find_key(data, "pixel_trim_dac")
    trims = numeric_list(pixel_trim)
    if trims:
        row["pixel_trim_min"] = stringify(int(min(trims)) if min(trims).is_integer() else min(trims))
        row["pixel_trim_max"] = stringify(int(max(trims)) if max(trims).is_integer() else max(trims))
        row["pixel_trim_mean"] = f"{sum(trims) / len(trims):.6g}"
        row["pixel_trim_hash"] = stable_hash(pixel_trim)
    else:
        row["pixel_trim_min"] = CSV_NONE
        row["pixel_trim_max"] = CSV_NONE
        row["pixel_trim_mean"] = CSV_NONE
        row["pixel_trim_hash"] = CSV_NONE

    row["state_hash_without_meta"] = stable_hash(stripped)
    return row


def parse_asic_configs(config_dir: Optional[Path], repo: Optional[Path], warnings: List[str]) -> List[Dict[str, str]]:
    if config_dir is None:
        return []
    if not config_dir.exists():
        warn(f"ASIC config directory does not exist: {config_dir}", warnings)
        return []
    if not config_dir.is_dir():
        warn(f"ASIC config path is not a directory: {config_dir}", warnings)
        return []

    rows: List[Dict[str, str]] = []
    for path in sorted(config_dir.rglob("config_*-*-*.json")):
        row = parse_asic_file(path, repo, warnings)
        if row is not None:
            rows.append(row)

    rows.sort(
        key=lambda r: (
            to_int(r["module"]) if r["module"] else 9999,
            to_int(r["io_group"]) or -1,
            to_int(r["io_channel"]) or -1,
            to_int(r["chip_id"]) or -1,
        )
    )
    return rows


# -----------------------------------------------------------------------------
# CSV/JSON I/O
# -----------------------------------------------------------------------------


def write_csv(path: Path, fields: Sequence[str], rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, CSV_NONE) for field in fields})


def rows_to_csv_text(fields: Sequence[str], rows: Sequence[Dict[str, str]]) -> str:
    import io

    sio = io.StringIO()
    writer = csv.DictWriter(sio, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, CSV_NONE) for field in fields})
    return sio.getvalue()


def read_csv_text(text: Optional[str]) -> List[Dict[str, str]]:
    if not text:
        return []
    import io

    return list(csv.DictReader(io.StringIO(text)))


def load_previous_texts(out_dir: Path, repo: Optional[Path]) -> Dict[str, Optional[str]]:
    files = [
        "run_config_summary.csv",
        "hydra_summary.csv",
        "asic_config_summary.csv",
        "cockpit_summary.json",
        "changes_since_previous.txt",
    ]

    previous: Dict[str, Optional[str]] = {}
    rel_out = None
    if repo is not None:
        try:
            rel_out = str(out_dir.resolve().relative_to(repo.resolve()))
        except Exception:  # noqa: BLE001
            rel_out = None

    for name in files:
        text = None
        if rel_out is not None:
            text = git_show_text(repo, f"{rel_out}/{name}")
        if text is None:
            text = read_text_if_exists(out_dir / name)
        previous[name] = text
    return previous


# -----------------------------------------------------------------------------
# Consistency checks and changes summary
# -----------------------------------------------------------------------------


def row_key(row: Dict[str, str], fields: Sequence[str]) -> Tuple[Any, ...]:
    return tuple(to_int(row.get(f)) if to_int(row.get(f)) is not None else row.get(f, CSV_NONE) for f in fields)


def key_by(rows: Sequence[Dict[str, str]], fields: Sequence[str]) -> Dict[Tuple[Any, ...], Dict[str, str]]:
    return {row_key(r, fields): r for r in rows}


def diff_int_sets(prev_text: Any, curr_text: Any) -> Tuple[Set[int], Set[int]]:
    prev = parse_semicolon_ints(prev_text)
    curr = parse_semicolon_ints(curr_text)
    gained = curr - prev
    lost = prev - curr
    return gained, lost


def fmt_set(values: Iterable[int]) -> str:
    vals = sorted(values)
    return ", ".join(str(v) for v in vals) if vals else "none"


def add_consistency_warnings(
    run_rows: Sequence[Dict[str, str]],
    hydra_rows: Sequence[Dict[str, str]],
    asic_rows: Sequence[Dict[str, str]],
    previous_hydra_rows: Sequence[Dict[str, str]],
    warnings: List[str],
    warn_lost_chips: int,
    warn_disabled_channels: int,
) -> None:
    run_by_pair = key_by(run_rows, ["io_group", "tile"])
    hydra_by_pair = key_by(hydra_rows, ["io_group", "tile"])

    for key, run in run_by_pair.items():
        active = str(run.get("active", "")).lower() == "true"
        if active and key not in hydra_by_pair:
            warn(f"Active RUN_CONFIG pair iog {key[0]} tile {key[1]} has no hydra file.", warnings)

    for key, hydra in hydra_by_pair.items():
        run = run_by_pair.get(key)
        if run is None or str(run.get("active", "")).lower() != "true":
            warn(f"Hydra file exists for inactive or missing RUN_CONFIG pair iog {key[0]} tile {key[1]}: {hydra.get('path')}", warnings)
            continue

        run_excluded = parse_semicolon_ints(run.get("excluded_chips"))
        hydra_connected = parse_semicolon_ints(hydra.get("connected_chips"))
        hydra_excluded = parse_semicolon_ints(hydra.get("excluded_chips"))

        overlap = run_excluded & hydra_connected
        if overlap:
            warn(
                f"RUN_CONFIG excludes chips still present in hydra for iog {key[0]} tile {key[1]}: {fmt_set(overlap)}",
                warnings,
            )

        missing_from_run_excludes = hydra_excluded - run_excluded
        if missing_from_run_excludes:
            warn(
                f"Hydra excludes chips not listed in RUN_CONFIG for iog {key[0]} tile {key[1]}: {fmt_set(missing_from_run_excludes)}",
                warnings,
            )

    # Compare hydra losses against previous committed/disk snapshot.
    prev_hydra = key_by(previous_hydra_rows, ["io_group", "tile"])
    for key, curr in hydra_by_pair.items():
        prev = prev_hydra.get(key)
        if not prev:
            continue
        _, lost = diff_int_sets(prev.get("connected_chips"), curr.get("connected_chips"))
        if len(lost) >= warn_lost_chips and warn_lost_chips > 0:
            warn(
                f"Large hydra chip loss for iog {key[0]} tile {key[1]}: lost {len(lost)} chips ({fmt_set(lost)}).",
                warnings,
            )

    # ASIC/hydra consistency using IO group + chip ID. Tile is not encoded in the
    # ASIC filename, so this intentionally ignores tile.
    hydra_chips_by_iog: Dict[int, Set[int]] = defaultdict(set)
    for h in hydra_rows:
        iog = to_int(h.get("io_group"))
        if iog is None:
            continue
        hydra_chips_by_iog[iog].update(parse_semicolon_ints(h.get("connected_chips")))

    asic_chips_by_iog: Dict[int, Set[int]] = defaultdict(set)
    for a in asic_rows:
        iog = to_int(a.get("io_group"))
        chip = to_int(a.get("chip_id"))
        if iog is not None and chip is not None:
            asic_chips_by_iog[iog].add(chip)

    for iog, chips in asic_chips_by_iog.items():
        extra = chips - hydra_chips_by_iog.get(iog, set())
        if extra:
            warn(f"ASIC configs exist for chips not connected in hydra for iog {iog}: {fmt_set(extra)}", warnings)

    for iog, chips in hydra_chips_by_iog.items():
        missing = chips - asic_chips_by_iog.get(iog, set())
        if asic_rows and missing:
            warn(f"Connected hydra chips have no ASIC config summary for iog {iog}: {fmt_set(missing)}", warnings)

    total_disabled = sum(to_int(r.get("n_disabled_channels")) or 0 for r in asic_rows)
    if total_disabled >= warn_disabled_channels and warn_disabled_channels > 0:
        warn(f"ASIC summary has {total_disabled} total disabled channels.", warnings)


def summarize_run_config_changes(prev_rows: Sequence[Dict[str, str]], curr_rows: Sequence[Dict[str, str]]) -> List[str]:
    lines: List[str] = []
    prev = key_by(prev_rows, ["io_group", "tile"])
    curr = key_by(curr_rows, ["io_group", "tile"])
    all_keys = sorted(set(prev) | set(curr), key=lambda k: (natural_sort_key(k[0]), natural_sort_key(k[1])))

    for key in all_keys:
        p = prev.get(key)
        c = curr.get(key)
        label = f"iog {key[0]} tile {key[1]}"
        if p is None:
            lines.append(f"  {label} added to RUN_CONFIG summary")
            continue
        if c is None:
            lines.append(f"  {label} removed from RUN_CONFIG summary")
            continue

        for col, nice in [
            ("active", "active"),
            ("pacman_version", "PACMAN version"),
            ("asic_version", "ASIC version"),
            ("VDDD_DAC", "VDDD"),
            ("VDDA_DAC", "VDDA"),
        ]:
            if p.get(col, CSV_NONE) != c.get(col, CSV_NONE):
                lines.append(f"  {label} {nice} changed {p.get(col, CSV_NONE) or 'unset'} -> {c.get(col, CSV_NONE) or 'unset'}")

        gained, lost = diff_int_sets(p.get("excluded_chips"), c.get("excluded_chips"))
        if gained:
            lines.append(f"  {label} excludes +{len(gained)} chips: {fmt_set(gained)}")
        if lost:
            lines.append(f"  {label} excludes -{len(lost)} chips: {fmt_set(lost)}")

    if not lines:
        lines.append("  unchanged")
    return lines


def summarize_hydra_changes(prev_rows: Sequence[Dict[str, str]], curr_rows: Sequence[Dict[str, str]]) -> List[str]:
    lines: List[str] = []
    prev = key_by(prev_rows, ["io_group", "tile"])
    curr = key_by(curr_rows, ["io_group", "tile"])
    all_keys = sorted(set(prev) | set(curr), key=lambda k: (natural_sort_key(k[0]), natural_sort_key(k[1])))

    for key in all_keys:
        p = prev.get(key)
        c = curr.get(key)
        label = f"iog {key[0]} tile {key[1]}"
        if p is None:
            lines.append(f"  {label} hydra file added with {c.get('connected_chip_count', '0')} connected chips")
            continue
        if c is None:
            lines.append(f"  {label} hydra file removed")
            continue

        if p.get("connected_chip_count") != c.get("connected_chip_count"):
            lines.append(f"  {label} connected chips: {p.get('connected_chip_count')} -> {c.get('connected_chip_count')}")

        gained, lost = diff_int_sets(p.get("connected_chips"), c.get("connected_chips"))
        if gained:
            lines.append(f"  {label} gained chips: {fmt_set(gained)}")
        if lost:
            lines.append(f"  {label} lost chips: {fmt_set(lost)}")

        prev_roots = parse_semicolon_ints(p.get("root_chips"))
        curr_roots = parse_semicolon_ints(c.get("root_chips"))
        if prev_roots != curr_roots:
            lines.append(f"  {label} root chips changed: {fmt_set(prev_roots)} -> {fmt_set(curr_roots)}")

        for col, nice in [("branch_count", "branch count"), ("max_chain_depth", "max chain depth")]:
            if p.get(col, CSV_NONE) != c.get(col, CSV_NONE):
                lines.append(f"  {label} {nice}: {p.get(col, CSV_NONE) or 'unset'} -> {c.get(col, CSV_NONE) or 'unset'}")

    if not lines:
        lines.append("  unchanged")
    return lines


def summarize_asic_changes(prev_rows: Sequence[Dict[str, str]], curr_rows: Sequence[Dict[str, str]]) -> List[str]:
    lines: List[str] = []
    prev = key_by(prev_rows, ["io_group", "io_channel", "chip_id"])
    curr = key_by(curr_rows, ["io_group", "io_channel", "chip_id"])

    if not prev and not curr:
        return ["  not provided"]
    if not prev and curr:
        total_disabled = sum(to_int(r.get("n_disabled_channels")) or 0 for r in curr_rows)
        return [f"  ASIC summary added for {len(curr_rows)} chips", f"  total disabled channels: {total_disabled}"]
    if prev and not curr:
        return ["  ASIC summary removed or ASIC config dir not provided"]

    changed: List[Tuple[Any, ...]] = []
    added = sorted(set(curr) - set(prev), key=lambda k: (natural_sort_key(k[0]), natural_sort_key(k[1]), natural_sort_key(k[2])))
    removed = sorted(set(prev) - set(curr), key=lambda k: (natural_sort_key(k[0]), natural_sort_key(k[1]), natural_sort_key(k[2])))
    common = sorted(set(prev) & set(curr), key=lambda k: (natural_sort_key(k[0]), natural_sort_key(k[1]), natural_sort_key(k[2])))

    for key in common:
        if prev[key].get("state_hash_without_meta") != curr[key].get("state_hash_without_meta"):
            changed.append(key)

    if changed:
        lines.append(f"  {len(changed)} chips changed")
    if added:
        lines.append(f"  {len(added)} chip configs added")
    if removed:
        lines.append(f"  {len(removed)} chip configs removed")

    prev_disabled_total = sum(to_int(r.get("n_disabled_channels")) or 0 for r in prev_rows)
    curr_disabled_total = sum(to_int(r.get("n_disabled_channels")) or 0 for r in curr_rows)
    delta = curr_disabled_total - prev_disabled_total
    if delta:
        sign = "+" if delta > 0 else ""
        lines.append(f"  disabled channels {sign}{delta} ({prev_disabled_total} -> {curr_disabled_total})")
    else:
        lines.append("  disabled channels unchanged")

    threshold_changed = [key for key in common if prev[key].get("threshold_global") != curr[key].get("threshold_global")]
    if threshold_changed:
        lines.append(f"  threshold_global changed on {len(threshold_changed)} chips")
    else:
        lines.append("  threshold_global unchanged")

    # Include a few per-chip details for disabled channels.
    detail_count = 0
    for key in common:
        gained, lost = diff_int_sets(prev[key].get("disabled_channels"), curr[key].get("disabled_channels"))
        if gained or lost:
            label = f"chip {key[0]}-{key[1]}-{key[2]}"
            if gained:
                lines.append(f"  {label} disabled channels +{len(gained)}: {fmt_set(gained)}")
                detail_count += 1
            if lost:
                lines.append(f"  {label} disabled channels -{len(lost)}: {fmt_set(lost)}")
                detail_count += 1
        if detail_count >= 10:
            remaining = len(changed) - detail_count
            if remaining > 0:
                lines.append(f"  ... {remaining} more changed chip configs not shown")
            break

    if not lines:
        lines.append("  unchanged")
    return lines


def build_change_summary(
    prev_texts: Dict[str, Optional[str]],
    run_rows: Sequence[Dict[str, str]],
    hydra_rows: Sequence[Dict[str, str]],
    asic_rows: Sequence[Dict[str, str]],
) -> str:
    prev_run = read_csv_text(prev_texts.get("run_config_summary.csv"))
    prev_hydra = read_csv_text(prev_texts.get("hydra_summary.csv"))
    prev_asic = read_csv_text(prev_texts.get("asic_config_summary.csv"))

    if not prev_run and not prev_hydra and not prev_asic:
        return "Since previous snapshot:\n  No previous snapshot found. Created baseline snapshot.\n"

    lines: List[str] = ["Since previous snapshot:", "", "RUN_CONFIG:"]
    lines.extend(summarize_run_config_changes(prev_run, run_rows))
    lines.extend(["", "Hydra:"])
    lines.extend(summarize_hydra_changes(prev_hydra, hydra_rows))
    lines.extend(["", "ASIC configs:"])
    lines.extend(summarize_asic_changes(prev_asic, asic_rows))
    lines.append("")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Overall summary
# -----------------------------------------------------------------------------


def build_cockpit_summary(
    run_config_path: Path,
    hydra_globs: Sequence[str],
    asic_config_dir: Optional[Path],
    out_dir: Path,
    repo: Optional[Path],
    run_rows: Sequence[Dict[str, str]],
    hydra_rows: Sequence[Dict[str, str]],
    asic_rows: Sequence[Dict[str, str]],
    warnings: Sequence[str],
) -> Dict[str, Any]:
    commit, branch = git_head_info(repo)

    active_tiles = sum(1 for r in run_rows if str(r.get("active", "")).lower() == "true")
    total_run_excluded = sum(to_int(r.get("n_excluded_chips")) or 0 for r in run_rows)
    total_connected = sum(to_int(r.get("connected_chip_count")) or 0 for r in hydra_rows)
    total_hydra_excluded = sum(to_int(r.get("n_excluded_chips")) or 0 for r in hydra_rows)
    total_disabled = sum(to_int(r.get("n_disabled_channels")) or 0 for r in asic_rows)
    total_csa_disabled = sum(to_int(r.get("n_csa_disabled_channels")) or 0 for r in asic_rows)

    return {
        "schema_version": 1,
        "generated_at_utc": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds"),
        "git": {
            "commit": commit,
            "branch": branch,
            "repo_root": str(repo) if repo else CSV_NONE,
        },
        "sources": {
            "run_config": relpath_if_possible(run_config_path, repo),
            "hydra_globs": list(hydra_globs),
            "asic_config_dir": str(asic_config_dir) if asic_config_dir else CSV_NONE,
            "out_dir": relpath_if_possible(out_dir, repo),
        },
        "counts": {
            "run_config_rows": len(run_rows),
            "active_tiles": active_tiles,
            "run_config_excluded_chips_total": total_run_excluded,
            "hydra_files": len(hydra_rows),
            "hydra_connected_chips_total": total_connected,
            "hydra_excluded_chips_total": total_hydra_excluded,
            "asic_config_chips": len(asic_rows),
            "asic_disabled_channels_total": total_disabled,
            "asic_csa_disabled_channels_total": total_csa_disabled,
            "warnings": len(warnings),
        },
    }


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate compact, Git-friendly CRS cockpit-state summaries.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--run-config", default="RUN_CONFIG.json", help="Path to RUN_CONFIG.json")
    parser.add_argument(
        "--hydra-glob",
        action="append",
        default=None,
        help="Glob for hydra network JSONs. Can be provided multiple times.",
    )
    parser.add_argument(
        "--asic-config-dir",
        default=None,
        help="Optional raw ASIC config directory to summarize. This may be DAQ-local.",
    )
    parser.add_argument("--out-dir", default="config_history/current", help="Output directory for compact summaries")
    parser.add_argument(
        "--warn-lost-chips",
        type=int,
        default=8,
        help="Warn when at least this many hydra chips are lost relative to previous snapshot. Use 0 to disable.",
    )
    parser.add_argument(
        "--warn-disabled-channels",
        type=int,
        default=64,
        help="Warn when total disabled ASIC channels is at least this number. Use 0 to disable.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print the change summary to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    run_config_path = Path(args.run_config)
    out_dir = Path(args.out_dir)
    asic_config_dir = Path(args.asic_config_dir) if args.asic_config_dir else None
    hydra_globs = args.hydra_glob or ["configs/iog-*-pacman-tile-*-hydra-network.json"]

    repo = git_root(Path.cwd())
    warnings: List[str] = []

    # Load previous committed/disk outputs before overwriting current outputs.
    previous_texts = load_previous_texts(out_dir, repo)
    previous_hydra_rows = read_csv_text(previous_texts.get("hydra_summary.csv"))

    hydra_rows = parse_hydra_files(hydra_globs, repo, warnings)
    hydra_pairs = {
        (to_int(r.get("io_group")), to_int(r.get("tile")))
        for r in hydra_rows
        if to_int(r.get("io_group")) is not None and to_int(r.get("tile")) is not None
    }
    # Type narrowing for mypy/readability.
    hydra_pairs_clean: Set[Tuple[int, int]] = {(int(i), int(t)) for i, t in hydra_pairs if i is not None and t is not None}

    run_rows = parse_run_config(run_config_path, hydra_pairs_clean, warnings)
    asic_rows = parse_asic_configs(asic_config_dir, repo, warnings)

    add_consistency_warnings(
        run_rows,
        hydra_rows,
        asic_rows,
        previous_hydra_rows,
        warnings,
        warn_lost_chips=args.warn_lost_chips,
        warn_disabled_channels=args.warn_disabled_channels,
    )

    change_summary = build_change_summary(previous_texts, run_rows, hydra_rows, asic_rows)
    cockpit_summary = build_cockpit_summary(
        run_config_path=run_config_path,
        hydra_globs=hydra_globs,
        asic_config_dir=asic_config_dir,
        out_dir=out_dir,
        repo=repo,
        run_rows=run_rows,
        hydra_rows=hydra_rows,
        asic_rows=asic_rows,
        warnings=warnings,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "run_config_summary.csv", RUN_CONFIG_FIELDS, run_rows)
    write_csv(out_dir / "hydra_summary.csv", HYDRA_FIELDS, hydra_rows)
    write_csv(out_dir / "asic_config_summary.csv", ASIC_FIELDS, asic_rows)

    with (out_dir / "cockpit_summary.json").open("w", encoding="utf-8") as f:
        json.dump(cockpit_summary, f, indent=2, sort_keys=True)
        f.write("\n")

    with (out_dir / "warnings.txt").open("w", encoding="utf-8") as f:
        if warnings:
            for item in warnings:
                f.write(f"WARNING: {item}\n")
        else:
            f.write("No warnings.\n")

    with (out_dir / "changes_since_previous.txt").open("w", encoding="utf-8") as f:
        f.write(change_summary)

    if not args.quiet:
        print(change_summary, end="" if change_summary.endswith("\n") else "\n")
        if warnings:
            print(f"\nWarnings: {len(warnings)} written to {out_dir / 'warnings.txt'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
