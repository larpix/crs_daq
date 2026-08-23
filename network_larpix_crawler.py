#!/usr/bin/env python3
"""
network_larpix_crawler_v2_4.py

Automatic replay of a *known* LArPix-v2b Hydra network using the safe
chip-by-chip electrical bring-up sequence validated with tile5_chip_crawler.py.

Key difference from the diagnostic crawler
------------------------------------------
The diagnostic crawler reset/rebuilt the accepted tree before every trial
because the trial chip was unmasked for inspection and an active/unmasked chip
was intentionally not trusted as a configuration router afterward.

For a known-good JSON topology we do not need that experimental cycle:

  1. configure PACMAN communication for every active root io_channel;
  2. issue ONE tile-selective LArPix reset for the PACMAN tile described by the JSON;
  3. configure every root while keeping all 64 channels masked;
  4. walk each JSON tree from mother to daughters, one edge at a time:
       - prepare mother return POSI;
       - enable exactly one mother upstream PISO toward the daughter;
       - repeatedly claim/configure the daughter while it is masked and has
         all of its own upstream/downstream PISOs disabled;
       - enable only the daughter's downstream PISO back to its mother;
       - KEEP THE DAUGHTER MASKED;
  5. after every root/tree is completely established, unmask the whole tile.

The initial reset is TILE-SCOPED and intentionally uses the exact same
PACMAN_IO.reset_tiles(...) API and pulse profile as the known-working CRS path
base.network_base.network_v2b(..., tiles=[tile]): three iterations of two
2048-MCLK-cycle tile-selective reset pulses.

v2.4 delegates tile isolation to the locally installed
PACMAN_IO.reset_tiles() method, exactly like
network_single_bruno.py -> network_base.network_v2b().

CRITICAL PACMAN RX RULE:
The CRS helper ``enable_pacman_uart_from_io_channel`` overwrites register 0x18
with ONLY the supplied channels.  That is appropriate for some isolated
diagnostics but is destructive during live-anode operation because all other
PACMAN receivers disappear.  v2.4 therefore NEVER calls that helper.  It
read-modify-writes register 0x18 instead: preserve every currently enabled RX
channel and OR in the root channels required by this tile.

The final unmask is deliberately performed leaves-to-roots (reverse BFS).  That
keeps every ASIC that is still needed as a configuration router masked until
all of its descendants have already received their final channel-mask write.

The network JSON is used only as a topology description.  This script does NOT
call Controller.init_network(), network_base.network_v2b(), reconcile the
configuration, or use the standard enforce_parallel path.

Branching networks are traversed breadth-first, preserving JSON slot order:
DOWN, LEFT, UP, RIGHT.  Every active ext->root io_channel in the selected
io_group is included by default.

Examples
--------
Validate topology and exact execution plan without touching hardware:

    python network_larpix_crawler_v2_4.py \
        configs/my-working-hydra-network.json \
        --dry-run --print-plan

Run the whole JSON topology on hardware:

    python network_larpix_crawler_v2_4.py \
        configs/my-working-hydra-network.json

For debugging only, one rooted io_channel can still be selected explicitly:

    python network_larpix_crawler_v2_4.py \
        configs/my-working-hydra-network.json \
        --io-channel 19 --dry-run --print-plan
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tile5_chip_crawler import (
    DIRECTIONS,
    Edge,
    HardwareOptions,
    LarpixHardware,
    neighbor,
)


SCRIPT_VERSION = "2.4.0"

# Operational ASIC configuration. These defaults reproduce the values used by
# the validated diagnostic crawler, but v2.4 exposes the nine run-dependent
# values below as CLI arguments so this script no longer depends on editing
# tile5_chip_crawler.py.
DEFAULT_OPERATIONAL_CONFIG: Dict[str, Any] = {
    "periodic_reset_cycles": 4,
    "periodic_trigger_cycles": 578125,
    "vref_dac": 185,
    "vcm_dac": 50,
    "threshold_global": 255,
    "enable_rolling_periodic_reset": 1,
    "enable_periodic_reset": 1,
    "enable_rolling_periodic_trigger": 1,
    "enable_periodic_trigger": 1,
}

# PACMAN tile-selective hard-reset profile.
#
# Match base.network_base.network_v2b(..., tiles=[tile]) exactly:
# three iterations, two calls to PACMAN_IO.reset_tiles(), 2048 cycles each.
TILE_RESET_LENGTH = 2048
TILE_RESET_BURSTS = 3
TILE_RESET_PULSES_PER_BURST = 2
LARPIX_MCLK_HZ = 10_000_000.0


# In the controller JSON, miso_us slots describe the daughter's physical
# location relative to the mother.  This matches the mapping established and
# tested by tile5_chip_crawler.py.
SLOT_TO_DIRECTION = {
    0: "down",
    1: "left",
    2: "up",
    3: "right",
}

# Refuse to reinterpret a JSON that uses a UART convention different from the
# one used by the experimentally validated crawler.
EXPECTED_UART_MAPS = {
    "miso_us_uart_map": [3, 0, 1, 2],
    "miso_ds_uart_map": [1, 2, 3, 0],
    "mosi_uart_map": [2, 3, 0, 1],
}


@dataclass(frozen=True)
class TargetTree:
    io_group: int
    io_channel: int
    root_chip: int
    root_slot: int
    root_posi: int
    root_downstream_piso: int
    edges: List[Edge]
    chip_ids: List[int]  # BFS order: root, then daughters parent-before-child

    @property
    def chip_count(self) -> int:
        return len(self.chip_ids)


@dataclass(frozen=True)
class TargetNetwork:
    io_group: int
    pacman_tile: int
    trees: List[TargetTree]

    @property
    def io_channels(self) -> List[int]:
        return [tree.io_channel for tree in self.trees]

    @property
    def chip_count(self) -> int:
        return sum(tree.chip_count for tree in self.trees)

    @property
    def edge_count(self) -> int:
        return sum(len(tree.edges) for tree in self.trees)


class ConsoleLogger:
    """Minimal logger adapter for the crawler hardware implementation.

    No files, session directories, JSONL, timestamps, or state snapshots are
    created.  By default only warnings/errors are printed.  --verbose-hardware
    also prints events that the original crawler marked for the console.
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def event(
        self,
        event: str,
        message: str = "",
        *,
        console: bool = False,
        level: str = "INFO",
        **data: Any,
    ) -> Dict[str, Any]:
        if level in {"WARNING", "ERROR"} or (self.verbose and console):
            text = f"[{level}] {event}"
            if message:
                text += f": {message}"
            print(text)
        return {"event": event, "level": level, **data}


class MaskedCrawlerHardware(LarpixHardware):
    """Crawler ASIC operations with the diagnostic unmask step separated out."""

    def __init__(
        self,
        options: HardwareOptions,
        log: ConsoleLogger,
        operational_config: Dict[str, Any],
    ):
        super().__init__(options, log)
        self.operational_config = dict(operational_config)

    def _set_safe_config(
        self,
        chip_id: int,
        *,
        input_posi: int,
    ) -> None:
        """Apply crawler-safe masked configuration with v2.4 run parameters.

        Start from the validated crawler defaults, override the nine explicitly
        configurable operational fields, and force the safety-critical network
        bootstrap state: all channels masked, no upstream/downstream PISO, and
        only the required command-input POSI enabled.
        """
        # Import here so the source of all unchanged crawler defaults remains
        # explicit and we do not duplicate the full register configuration.
        from tile5_chip_crawler import PEDESTAL_VALUES

        chip = self._ensure_chip(chip_id)
        cfg = chip.config

        values = dict(PEDESTAL_VALUES)
        values.update(self.operational_config)

        for name, value in values.items():
            setattr(cfg, name, list(value) if isinstance(value, list) else value)

        cfg.chip_id = chip_id
        cfg.enable_piso_upstream = [0, 0, 0, 0]
        cfg.enable_piso_downstream = [0, 0, 0, 0]

        posi = [0, 0, 0, 0]
        posi[input_posi] = 1
        cfg.enable_posi = posi

        # Safety invariant for the complete network build.
        cfg.channel_mask = [1] * 64

    def reset_target_tile(self) -> None:
        """Reset ONLY the selected PACMAN tile using PACMAN_IO.reset_tiles().

        This is intentionally copied from the proven CRS networking path:

            for _ in range(3):
                io.reset_tiles(tiles=[tile], length=2048, io_group=io_group)
                sleep(2048 / 10e6)
                io.reset_tiles(tiles=[tile], length=2048, io_group=io_group)
                sleep(2048 / 10e6)

        Do not substitute reset_larpix() here. Tile isolation is delegated to
        the locally installed PACMAN_IO.reset_tiles() implementation, exactly
        as network_single_bruno.py -> network_base.network_v2b() does.
        """
        assert self.io is not None

        tile = self.options.pacman_tile
        if tile is None:
            raise RuntimeError(
                "Tile-selective reset requires a PACMAN tile number; none was supplied"
            )
        if tile < 1 or tile > 8:
            raise RuntimeError(f"PACMAN tile {tile} is outside the valid range 1..8")

        io_group = self.options.io_group

        reset_tiles = getattr(self.io, "reset_tiles", None)
        if reset_tiles is None or not callable(reset_tiles):
            raise RuntimeError(
                "This PACMAN_IO does not provide reset_tiles(). "
                "Refusing to fall back to reset_larpix(), because that would reset "
                "the entire io_group."
            )

        # Make the runtime implementation visible so an operator can verify
        # which larpix-control installation is actually being used.
        try:
            import inspect
            implementation_file = inspect.getsourcefile(type(self.io))
            reset_method_file = inspect.getsourcefile(reset_tiles)
        except Exception:
            implementation_file = None
            reset_method_file = None

        print(
            f"[reset] using PACMAN_IO.reset_tiles(tiles=[{tile}], "
            f"length={TILE_RESET_LENGTH}, io_group={io_group})"
        )
        if implementation_file:
            print(f"[reset] PACMAN_IO implementation: {implementation_file}")
        if reset_method_file and reset_method_file != implementation_file:
            print(f"[reset] reset_tiles implementation: {reset_method_file}")

        pulse_wait_s = TILE_RESET_LENGTH / LARPIX_MCLK_HZ
        pulse_number = 0

        for burst in range(1, TILE_RESET_BURSTS + 1):
            for pulse_in_burst in range(1, TILE_RESET_PULSES_PER_BURST + 1):
                pulse_number += 1

                print(
                    f"[reset] tile {tile} selective pulse {pulse_number}/"
                    f"{TILE_RESET_BURSTS * TILE_RESET_PULSES_PER_BURST} "
                    f"(burst {burst}, pulse {pulse_in_burst})"
                )

                # CRITICAL: this must remain reset_tiles(), not reset_larpix().
                self.io.reset_tiles(
                    tiles=[tile],
                    length=TILE_RESET_LENGTH,
                    io_group=io_group,
                )
                time.sleep(pulse_wait_s)

        # The target ASICs have been reset, so discard their in-memory configs.
        self._new_controller()

        self.log.event(
            "TILE_RESET_END",
            "PACMAN_IO.reset_tiles tile-selective hard reset complete",
            console=True,
            io_group=io_group,
            pacman_tile=tile,
            pulses=pulse_number,
            pulse_length=TILE_RESET_LENGTH,
        )

    def _enable_downstream_masked(
        self,
        chip_id: int,
        *,
        downstream_piso: int,
        return_target: Any,
    ) -> None:
        chip = self._ensure_chip(chip_id)
        downstream = [0, 0, 0, 0]
        downstream[downstream_piso] = 1
        chip.config.enable_piso_downstream = downstream

        self.log.event(
            "DOWNSTREAM_ENABLE_BEGIN",
            "Enable the one allowed return PISO; channels remain masked",
            console=True,
            chip=chip_id,
            piso=downstream_piso,
            return_target=return_target,
        )
        self._write_register_names(
            chip_id,
            ["enable_piso_downstream"],
            reason=f"enable downstream return from chip {chip_id} to {return_target}",
        )
        self.log.event(
            "DOWNSTREAM_ENABLE_END",
            chip=chip_id,
            piso=downstream_piso,
            return_target=return_target,
        )

    def bring_up_root_masked(self, root_chip: int) -> None:
        self.log.event(
            "ROOT_BRINGUP_BEGIN",
            "Configure root but keep all channels masked",
            console=True,
            root_chip=root_chip,
            root_posi=self.options.root_posi,
            root_downstream_piso=self.options.root_downstream_piso,
        )
        self._blind_configure_masked(
            root_chip,
            input_posi=self.options.root_posi,
            is_trial=False,
        )
        self._enable_downstream_masked(
            root_chip,
            downstream_piso=self.options.root_downstream_piso,
            return_target="PACMAN",
        )
        self.log.event("ROOT_BRINGUP_END", root_chip=root_chip, console=True)

    def bring_up_edge_masked(self, edge: Edge) -> None:
        spec = DIRECTIONS[edge.direction]
        self.log.event(
            "EDGE_BRINGUP_BEGIN",
            "Expose/configure one known daughter; keep it masked",
            console=True,
            mother=edge.mother,
            daughter=edge.daughter,
            direction=edge.direction,
        )

        # Same experimentally validated crawler ordering:
        # mother return RX first -> expose one candidate -> blind masked writes
        # -> enable one daughter return TX.  The only omitted step is unmasking.
        self._prepare_mother_for_daughter(edge)
        self._blind_configure_masked(
            edge.daughter,
            input_posi=spec.daughter_posi_from_mother,
            is_trial=False,
        )
        self._enable_downstream_masked(
            edge.daughter,
            downstream_piso=spec.daughter_downstream_piso,
            return_target=edge.mother,
        )

        self.log.event(
            "EDGE_BRINGUP_END",
            mother=edge.mother,
            daughter=edge.daughter,
            direction=edge.direction,
            console=True,
        )

    def unmask_chip(self, chip_id: int) -> None:
        chip = self._ensure_chip(chip_id)
        chip.config.channel_mask = [0] * 64
        self.log.event(
            "UNMASK_BEGIN",
            "Final network-complete unmask",
            console=True,
            chip=chip_id,
        )
        self._write_register_names(
            chip_id,
            ["channel_mask"],
            reason=f"final unmask of chip {chip_id}",
        )
        self.log.event("CHIP_LIVE", chip=chip_id, console=True)


def _integer_group_keys(network: Dict[str, Any]) -> List[int]:
    groups: List[int] = []
    for key in network:
        try:
            groups.append(int(key))
        except (TypeError, ValueError):
            pass
    return sorted(groups)


def _require_uart_maps(network: Dict[str, Any]) -> None:
    for name, expected in EXPECTED_UART_MAPS.items():
        actual = network.get(name)
        if actual != expected:
            raise ValueError(
                f"{name}={actual!r}, but the validated crawler expects {expected!r}. "
                "Refusing to guess a different UART convention."
            )


def _node_chip_id(node: Dict[str, Any]) -> Any:
    value = node.get("chip_id")
    if value == "ext":
        return value
    if isinstance(value, bool):
        raise ValueError(f"invalid chip_id {value!r}")
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid chip_id {value!r}") from exc
    if value < 11 or value > 110:
        raise ValueError(f"chip_id {value} is outside the 10x10 tile (11..110)")
    return value


def _miso_us(node: Dict[str, Any], *, label: str) -> List[Any]:
    links = node.get("miso_us")
    if not isinstance(links, list) or len(links) != 4:
        raise ValueError(f"{label} must have a four-entry miso_us list")
    return links


def _active_root(nodes: List[Dict[str, Any]], io_channel: int) -> Optional[Tuple[int, int]]:
    ext_nodes = [node for node in nodes if node.get("chip_id") == "ext"]
    if len(ext_nodes) != 1:
        raise ValueError(
            f"io_channel {io_channel}: expected exactly one ext node, found {len(ext_nodes)}"
        )

    links = _miso_us(ext_nodes[0], label=f"io_channel {io_channel} ext node")
    roots = [(slot, value) for slot, value in enumerate(links) if value is not None]
    if not roots:
        return None
    if len(roots) != 1:
        raise ValueError(
            f"io_channel {io_channel}: ext node exposes {len(roots)} roots; "
            "expected exactly one physical root on that io_channel"
        )

    slot, chip_id = roots[0]
    try:
        chip_id = int(chip_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"io_channel {io_channel}: invalid root chip_id {chip_id!r}"
        ) from exc
    if chip_id < 11 or chip_id > 110:
        raise ValueError(
            f"io_channel {io_channel}: root chip_id {chip_id} is outside 11..110"
        )
    return slot, chip_id


def _parse_tree(
    network: Dict[str, Any],
    *,
    io_group: int,
    io_channel: int,
) -> TargetTree:
    group = network.get(str(io_group))
    if not isinstance(group, dict):
        raise ValueError(f"network has no io_group {io_group}")

    channel = group.get(str(io_channel))
    if not isinstance(channel, dict):
        raise ValueError(f"network has no io_channel {io_channel} in io_group {io_group}")

    nodes = channel.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError(f"io_channel {io_channel}: missing/empty nodes list")

    root = _active_root(nodes, io_channel)
    if root is None:
        raise ValueError(f"io_channel {io_channel}: ext node has no active root")
    root_slot, root_chip = root

    by_id: Dict[int, Dict[str, Any]] = {}
    for node in nodes:
        chip_id = _node_chip_id(node)
        if chip_id == "ext":
            continue
        if chip_id in by_id:
            raise ValueError(f"io_channel {io_channel}: duplicate chip node {chip_id}")
        by_id[chip_id] = node

    if root_chip not in by_id:
        raise ValueError(
            f"io_channel {io_channel}: ext points to root {root_chip}, "
            "but no node with that chip_id exists"
        )

    # Breadth-first traversal is intentionally simple and deterministic.  Every
    # mother is therefore configured before one of its daughters is exposed.
    queue = deque([root_chip])
    discovered = {root_chip}
    parent_of: Dict[int, int] = {}
    edges: List[Edge] = []
    chip_ids: List[int] = [root_chip]

    while queue:
        mother = queue.popleft()
        node = by_id[mother]
        links = _miso_us(node, label=f"chip {mother}")

        for slot, raw_daughter in enumerate(links):
            if raw_daughter is None:
                continue
            if raw_daughter == "ext":
                raise ValueError(
                    f"chip {mother}: unexpected 'ext' in miso_us slot {slot}; "
                    "only ext->root is expected in this topology format"
                )
            try:
                daughter = int(raw_daughter)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"chip {mother}: invalid daughter {raw_daughter!r} in slot {slot}"
                ) from exc

            if daughter not in by_id:
                raise ValueError(
                    f"chip {mother}: daughter {daughter} is referenced but has no node"
                )

            direction = SLOT_TO_DIRECTION[slot]
            expected_daughter = neighbor(mother, direction)
            if daughter != expected_daughter:
                raise ValueError(
                    f"chip {mother}: miso_us slot {slot} ({direction}) points to {daughter}, "
                    f"but tile geometry requires {expected_daughter}"
                )

            if daughter == root_chip:
                raise ValueError(f"loop detected: chip {mother} points back to root {root_chip}")
            if daughter in parent_of:
                raise ValueError(
                    f"chip {daughter} has multiple parents: "
                    f"{parent_of[daughter]} and {mother}"
                )
            if daughter in discovered:
                raise ValueError(f"loop/duplicate path detected at chip {daughter}")

            edge = Edge.make(mother, direction)
            if edge.daughter != daughter:
                raise AssertionError("crawler geometry and JSON parser geometry disagree")

            parent_of[daughter] = mother
            discovered.add(daughter)
            edges.append(edge)
            chip_ids.append(daughter)
            queue.append(daughter)

    disconnected = sorted(set(by_id) - discovered)
    if disconnected:
        raise ValueError(
            f"io_channel {io_channel}: {len(disconnected)} chip node(s) are disconnected "
            f"from root {root_chip}: {disconnected}"
        )

    root_posi = network["mosi_uart_map"][root_slot]
    root_downstream_piso = network["miso_ds_uart_map"][root_slot]

    return TargetTree(
        io_group=io_group,
        io_channel=io_channel,
        root_chip=root_chip,
        root_slot=root_slot,
        root_posi=root_posi,
        root_downstream_piso=root_downstream_piso,
        edges=edges,
        chip_ids=chip_ids,
    )


def inferred_pacman_tile(io_channel: int) -> int:
    if io_channel < 1 or io_channel > 32:
        raise ValueError(f"io_channel {io_channel} is outside PACMAN range 1..32")
    return (io_channel - 1) // 4 + 1


def load_target_network(
    path: Path,
    *,
    requested_io_group: Optional[int],
    requested_io_channel: Optional[int],
) -> Tuple[Dict[str, Any], TargetNetwork]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if payload.get("asic_version") != "2b":
        raise ValueError(
            f"{path}: asic_version={payload.get('asic_version')!r}; "
            "this script is intentionally v2b-only"
        )

    network = payload.get("network")
    if not isinstance(network, dict):
        raise ValueError(f"{path}: missing network object")

    _require_uart_maps(network)

    groups = _integer_group_keys(network)
    if requested_io_group is None:
        if len(groups) != 1:
            raise ValueError(
                f"{path}: found io_groups {groups}; select one with --io-group"
            )
        io_group = groups[0]
    else:
        io_group = requested_io_group
        if io_group not in groups:
            raise ValueError(f"{path}: io_group {io_group} is not present (found {groups})")

    group = network[str(io_group)]
    if not isinstance(group, dict):
        raise ValueError(f"{path}: malformed io_group {io_group} network")

    channel_ids = sorted(int(key) for key in group if str(key).isdigit())
    active_channels: List[int] = []
    for io_channel in channel_ids:
        channel = group[str(io_channel)]
        nodes = channel.get("nodes") if isinstance(channel, dict) else None
        if not isinstance(nodes, list) or not nodes:
            continue
        if _active_root(nodes, io_channel) is not None:
            active_channels.append(io_channel)

    if not active_channels:
        raise ValueError(f"{path}: no active rooted io_channel found in io_group {io_group}")

    if requested_io_channel is not None:
        if requested_io_channel not in active_channels:
            raise ValueError(
                f"{path}: io_channel {requested_io_channel} is not an active rooted channel; "
                f"active rooted channels are {active_channels}"
            )
        selected_channels = [requested_io_channel]
    else:
        # Operational default: consume every root that the JSON declares.
        selected_channels = active_channels

    trees = [
        _parse_tree(network, io_group=io_group, io_channel=io_channel)
        for io_channel in selected_channels
    ]

    # A tile JSON should describe roots belonging to one PACMAN tile.  The UART
    # inversion helper is tile-wide, so refuse a mixed-tile target rather than
    # silently applying the wrong electrical setup.
    pacman_tiles = {inferred_pacman_tile(tree.io_channel) for tree in trees}
    if len(pacman_tiles) != 1:
        raise ValueError(
            f"{path}: active roots span PACMAN tiles {sorted(pacman_tiles)}; "
            "use a single-tile Hydra JSON"
        )
    pacman_tile = next(iter(pacman_tiles))

    # The same physical ASIC must never be claimed through two independent
    # roots.  Catch overlapping/malformed trees before touching hardware.
    owner_by_chip: Dict[int, int] = {}
    for tree in trees:
        for chip_id in tree.chip_ids:
            previous = owner_by_chip.get(chip_id)
            if previous is not None:
                raise ValueError(
                    f"chip {chip_id} appears in rooted io_channels {previous} and "
                    f"{tree.io_channel}; trees overlap"
                )
            owner_by_chip[chip_id] = tree.io_channel

    return payload, TargetNetwork(
        io_group=io_group,
        pacman_tile=pacman_tile,
        trees=trees,
    )


def chip_depths(tree: TargetTree) -> Dict[int, int]:
    """Return root-relative graph depth for every chip in one rooted tree."""
    depths: Dict[int, int] = {tree.root_chip: 0}
    for edge in tree.edges:
        depths[edge.daughter] = depths[edge.mother] + 1
    return depths


def global_build_order(
    target: TargetNetwork,
) -> List[Tuple[int, TargetTree, Edge]]:
    """Return all edges in a deterministic breadth-first order across roots.

    All roots are configured first outside this function.  Then depth-1 edges
    from every tree are handled before any depth-2 edge, and so on.  Within a
    tree/depth, the original JSON/BFS slot order is preserved.
    """
    items: List[Tuple[int, int, int, TargetTree, Edge]] = []
    for tree_index, tree in enumerate(target.trees):
        depths = chip_depths(tree)
        for edge_index, edge in enumerate(tree.edges):
            items.append(
                (depths[edge.daughter], tree_index, edge_index, tree, edge)
            )
    items.sort(key=lambda item: (item[0], item[1], item[2]))
    return [(depth, tree, edge) for depth, _, _, tree, edge in items]


def final_unmask_order(target: TargetNetwork) -> List[Tuple[int, int, int]]:
    """Return (depth, io_channel, chip_id) deepest-first, roots last."""
    items: List[Tuple[int, int, int, int, int]] = []
    for tree_index, tree in enumerate(target.trees):
        depths = chip_depths(tree)
        bfs_index = {chip_id: idx for idx, chip_id in enumerate(tree.chip_ids)}
        for chip_id in tree.chip_ids:
            items.append(
                (
                    -depths[chip_id],
                    tree_index,
                    -bfs_index[chip_id],
                    tree.io_channel,
                    chip_id,
                )
            )
    items.sort()
    return [(-neg_depth, io_channel, chip_id) for neg_depth, _, _, io_channel, chip_id in items]


def print_plan(
    path: Path,
    payload: Dict[str, Any],
    target: TargetNetwork,
    *,
    full: bool,
) -> None:
    print("=" * 78)
    print("AUTOMATIC MASKED CRAWLER NETWORK")
    print(f"  script version         : {SCRIPT_VERSION}")
    print(f"  file                   : {path}")
    print(f"  network name           : {payload.get('name', '<unnamed>')}")
    print(f"  io_group               : {target.io_group}")
    print(f"  PACMAN tile            : {target.pacman_tile}")
    print(f"  active rooted channels : {target.io_channels}")
    print(f"  roots                  : {[tree.root_chip for tree in target.trees]}")
    print(f"  total chips            : {target.chip_count}")
    print(f"  total edges            : {target.edge_count}")
    print("  traversal              : all roots first; global BFS; DOWN, LEFT, UP, RIGHT")
    print(f"  ASIC reset             : PACMAN tile {target.pacman_tile} ONLY")
    print("  reset API              : PACMAN_IO.reset_tiles() (no direct reset_larpix)")
    print("  reset profile          : 6 x 2048-cycle tile-selective pulses")
    print("  PACMAN RX behavior     : preserve existing RX mask; OR in root channels")
    print("  during network build   : ALL channels stay masked")
    print("  final transition       : unmask all chips deepest-to-roots")
    print("  configuration readback : NONE")
    print("=" * 78)

    print("Root interfaces:")
    for tree in target.trees:
        print(
            f"  io_channel {tree.io_channel:2d}: root {tree.root_chip:3d}, "
            f"ext slot {tree.root_slot}, command POSI{tree.root_posi}, "
            f"return PISO{tree.root_downstream_piso}, chips={tree.chip_count}"
        )
    print()

    if full:
        print("Build plan (all chips remain masked):")
        for idx, (depth, tree, edge) in enumerate(global_build_order(target), 1):
            spec = DIRECTIONS[edge.direction]
            print(
                f"  {idx:3d}. depth {depth:2d}  io_channel {tree.io_channel:2d}  "
                f"{edge.mother:3d} -> {edge.daughter:3d} "
                f"{edge.direction.upper():5s}  "
                f"mother PISO{spec.mother_upstream_piso} -> "
                f"daughter POSI{spec.daughter_posi_from_mother}; "
                f"return PISO{spec.daughter_downstream_piso}"
            )

        print("\nFinal unmask order (global deepest -> roots):")
        for idx, (depth, io_channel, chip_id) in enumerate(final_unmask_order(target), 1):
            print(
                f"  {idx:3d}. depth {depth:2d}  io_channel {io_channel:2d} "
                f"chip {chip_id:3d}"
            )
        print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Automatically replay a known v2b Hydra JSON using the crawler's safe "
            "chip-by-chip electrical sequence, keeping the complete network masked "
            "until one final unmask phase."
        )
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SCRIPT_VERSION}",
    )
    p.add_argument("network_json", type=Path, help="Known-good Hydra network JSON")
    p.add_argument("--io-group", type=int, default=None)
    p.add_argument(
        "--io-channel",
        type=int,
        default=None,
        help=(
            "Debug filter for one rooted io_channel. Default: configure EVERY active "
            "ext->root io_channel declared by the JSON."
        ),
    )
    p.add_argument(
        "--pacman-config",
        default=None,
        help="PACMAN_IO JSON. Default: io/pacman_io<io-group>.json",
    )
    p.add_argument(
        "--pacman-tile",
        type=int,
        default=None,
        help=(
            "PACMAN tile for the existing crs_daq UART inversion helper. "
            "Default: infer from the active io_channels."
        ),
    )
    p.add_argument(
        "--no-crs-pacman-helpers",
        action="store_true",
        help="Skip crs_daq PACMAN UART inversion/RX-enable helpers.",
    )

    # Keep the experimentally-used crawler defaults unchanged.
    p.add_argument("--uart-clock-ratio", type=int, default=10)
    p.add_argument("--config-write-repeats", type=int, default=5)
    p.add_argument("--write-delay", type=float, default=0.050)
    # The tile-selective hard-reset profile is intentionally fixed in v2.2 to
    # reproduce network_base.network_v2b(..., tiles=[tile]): 6 x 2048 cycles.
    # It is not exposed as a casual CLI tuning knob.
    # Run-dependent ASIC operating parameters. Defaults reproduce the validated
    # crawler configuration. Enable fields intentionally accept explicit 0/1
    # rather than store_true/store_false so the command line mirrors the ASIC
    # register values and is unambiguous in shell history.
    p.add_argument(
        "--periodic-reset-cycles",
        type=int,
        default=DEFAULT_OPERATIONAL_CONFIG["periodic_reset_cycles"],
    )
    p.add_argument(
        "--periodic-trigger-cycles",
        type=int,
        default=DEFAULT_OPERATIONAL_CONFIG["periodic_trigger_cycles"],
    )
    p.add_argument(
        "--vref-dac",
        type=int,
        default=DEFAULT_OPERATIONAL_CONFIG["vref_dac"],
    )
    p.add_argument(
        "--vcm-dac",
        type=int,
        default=DEFAULT_OPERATIONAL_CONFIG["vcm_dac"],
    )
    p.add_argument(
        "--threshold-global",
        type=int,
        default=DEFAULT_OPERATIONAL_CONFIG["threshold_global"],
    )
    p.add_argument(
        "--enable-rolling-periodic-reset",
        type=int,
        choices=(0, 1),
        default=DEFAULT_OPERATIONAL_CONFIG["enable_rolling_periodic_reset"],
    )
    p.add_argument(
        "--enable-periodic-reset",
        type=int,
        choices=(0, 1),
        default=DEFAULT_OPERATIONAL_CONFIG["enable_periodic_reset"],
    )
    p.add_argument(
        "--enable-rolling-periodic-trigger",
        type=int,
        choices=(0, 1),
        default=DEFAULT_OPERATIONAL_CONFIG["enable_rolling_periodic_trigger"],
    )
    p.add_argument(
        "--enable-periodic-trigger",
        type=int,
        choices=(0, 1),
        default=DEFAULT_OPERATIONAL_CONFIG["enable_periodic_trigger"],
    )

    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse/validate/print execution without importing larpix or touching hardware.",
    )
    p.add_argument(
        "--reset-only",
        action="store_true",
        help=(
            "Perform only the verified tile-selective hard-reset sequence, then exit "
            "without configuring any ASIC network. Useful for hardware validation."
        ),
    )
    p.add_argument(
        "--print-plan",
        action="store_true",
        help="Print every mother->daughter edge and final unmask order.",
    )
    p.add_argument(
        "--verbose-hardware",
        action="store_true",
        help="Print the original crawler's console-level hardware events.",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the final confirmation before the initial tile-selective reset.",
    )
    return p


def _make_hardware_options(
    tree: TargetTree,
    *,
    pacman_config: str,
    pacman_tile: int,
    use_crs_pacman_helpers: bool,
    args: argparse.Namespace,
) -> HardwareOptions:
    return HardwareOptions(
        io_group=tree.io_group,
        io_channel=tree.io_channel,
        pacman_config=pacman_config,
        pacman_tile=pacman_tile,
        use_crs_pacman_helpers=use_crs_pacman_helpers,
        uart_clock_ratio=args.uart_clock_ratio,
        root_posi=tree.root_posi,
        root_downstream_piso=tree.root_downstream_piso,
        config_write_repeats=args.config_write_repeats,
        write_delay_s=args.write_delay,
        reset_length=TILE_RESET_LENGTH,
        reset_pulses=TILE_RESET_BURSTS * TILE_RESET_PULSES_PER_BURST,
        reset_gap_s=TILE_RESET_LENGTH / LARPIX_MCLK_HZ,
    )


def operational_config_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "periodic_reset_cycles": args.periodic_reset_cycles,
        "periodic_trigger_cycles": args.periodic_trigger_cycles,
        "vref_dac": args.vref_dac,
        "vcm_dac": args.vcm_dac,
        "threshold_global": args.threshold_global,
        "enable_rolling_periodic_reset": args.enable_rolling_periodic_reset,
        "enable_periodic_reset": args.enable_periodic_reset,
        "enable_rolling_periodic_trigger": args.enable_rolling_periodic_trigger,
        "enable_periodic_trigger": args.enable_periodic_trigger,
    }


PACMAN_RX_ENABLE_REG = 0x18


def io_channel_mask(io_channels: Sequence[int]) -> int:
    """Convert 1-based PACMAN io_channels to the RX-enable register bit mask."""
    mask = 0
    for io_channel in io_channels:
        if io_channel < 1 or io_channel > 32:
            raise ValueError(
                f"PACMAN io_channel {io_channel} is outside the valid range 1..32"
            )
        mask |= 1 << (io_channel - 1)
    return mask


def preserve_and_enable_pacman_rx(
    io,
    *,
    io_group: int,
    io_channels: Sequence[int],
) -> Tuple[int, int]:
    """Preserve the live PACMAN RX mask and add required root channels.

    The old CRS helper ``enable_pacman_uart_from_io_channel`` builds a mask from
    zero and therefore disables every receiver not listed in ``io_channels``.
    For live detector operation that makes unrelated tiles vanish from PacMon.

    This function is intentionally read-modify-write:
        after = before | mask(io_channels)

    No previously-enabled PACMAN receiver may be disabled by this operation.
    """
    required = io_channel_mask(io_channels)

    try:
        before = int(io.get_reg(PACMAN_RX_ENABLE_REG, io_group=io_group))
    except Exception as exc:
        raise RuntimeError(
            f"Could not read PACMAN RX-enable register 0x{PACMAN_RX_ENABLE_REG:x}; "
            "refusing to modify receiver state"
        ) from exc

    after = before | required

    print(
        f"[PACMAN RX] reg 0x{PACMAN_RX_ENABLE_REG:02x} before=0x{before:08x} "
        f"required=0x{required:08x} after=0x{after:08x}"
    )
    print(
        f"[PACMAN RX] preserving existing receivers; adding io_channels "
        f"{list(io_channels)}"
    )

    if after != before:
        io.set_reg(PACMAN_RX_ENABLE_REG, after, io_group=io_group)

    readback = int(io.get_reg(PACMAN_RX_ENABLE_REG, io_group=io_group))
    if readback != after:
        raise RuntimeError(
            "PACMAN RX-enable readback mismatch: "
            f"wrote 0x{after:08x}, read 0x{readback:08x}"
        )

    # Strong safety invariant: no bit which was enabled before may turn off.
    lost = before & ~readback
    if lost:
        raise RuntimeError(
            "PACMAN RX safety check failed: previously enabled receiver bits "
            f"0x{lost:08x} disappeared"
        )

    print(f"[PACMAN RX] verified readback=0x{readback:08x}; no existing RX disabled")
    return before, readback


def connect_all_hardware(
    target: TargetNetwork,
    *,
    pacman_config: str,
    pacman_tile: int,
    args: argparse.Namespace,
    logger: ConsoleLogger,
) -> Dict[int, MaskedCrawlerHardware]:
    """Create one shared PACMAN_IO and one crawler Controller per root channel."""
    try:
        import larpix
        import larpix.io
    except Exception as exc:
        raise RuntimeError(
            "Could not import larpix-control. Run inside the CRS Python environment "
            "where `import larpix` works."
        ) from exc

    io = larpix.io.PACMAN_IO(relaxed=True, config_filepath=pacman_config)

    for io_channel in target.io_channels:
        io.set_uart_clock_ratio(
            io_channel,
            args.uart_clock_ratio,
            io_group=target.io_group,
        )

    pacman_helper = None
    if not args.no_crs_pacman_helpers:
        try:
            from base import pacman_base
        except Exception as exc:
            raise RuntimeError(
                "Could not import base.pacman_base. Use --no-crs-pacman-helpers only "
                "if PACMAN UART inversion/RX setup has already been done externally."
            ) from exc

        pacman_helper = pacman_base
        pacman_base.invert_pacman_uart(
            io,
            target.io_group,
            "2b",
            [pacman_tile],
        )

        # IMPORTANT: do NOT call
        # pacman_base.enable_pacman_uart_from_io_channel() here.
        # That helper overwrites the whole RX-enable register and would disable
        # every unrelated tile on this PACMAN. Preserve all existing receivers
        # and add only the root channels required by this network.
        preserve_and_enable_pacman_rx(
            io,
            io_group=target.io_group,
            io_channels=target.io_channels,
        )

    hardware_by_channel: Dict[int, MaskedCrawlerHardware] = {}
    for tree in target.trees:
        options = _make_hardware_options(
            tree,
            pacman_config=pacman_config,
            pacman_tile=pacman_tile,
            use_crs_pacman_helpers=not args.no_crs_pacman_helpers,
            args=args,
        )
        hardware = MaskedCrawlerHardware(
            options,
            logger,
            operational_config_from_args(args),
        )

        # We intentionally share one PACMAN_IO across all root channels but keep
        # independent larpix Controllers so the crawler's single-io_channel chip
        # bookkeeping remains unchanged and chip IDs cannot collide in software.
        hardware.larpix = larpix
        hardware.io = io
        hardware._pacman_helper = pacman_helper
        hardware._new_controller()
        hardware_by_channel[tree.io_channel] = hardware

    return hardware_by_channel


def execute_dry_run(target: TargetNetwork) -> None:
    print("DRY RUN EXECUTION")
    print(
        f"[PACMAN RX] preserve current reg 0x18 and OR in root io_channels "
        f"{target.io_channels}; unrelated receivers remain enabled"
    )
    print(
        f"[reset] PACMAN_IO.reset_tiles tile-selective hard reset: io_group {target.io_group}, "
        f"PACMAN tile {target.pacman_tile} ONLY; 6 x 2048 cycles "
        f"({len(target.trees)} rooted channels)"
    )

    for tree in target.trees:
        print(
            f"[root io={tree.io_channel:2d}] configure root {tree.root_chip:3d} "
            "MASKED; enable return to PACMAN"
        )

    total = target.edge_count
    for idx, (depth, tree, edge) in enumerate(global_build_order(target), 1):
        print(
            f"[{idx:3d}/{total:3d} depth={depth:2d} io={tree.io_channel:2d}] add "
            f"{edge.mother}->{edge.daughter} ({edge.direction}); KEEP MASKED"
        )

    print(f"[final] network complete; unmask {target.chip_count} chips deepest-to-roots")
    for depth, io_channel, chip_id in final_unmask_order(target):
        print(
            f"        depth {depth:2d}  unmask io_channel {io_channel:2d} "
            f"chip {chip_id:3d}"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    print(f"network_larpix_crawler version {SCRIPT_VERSION}")

    if args.config_write_repeats < 1:
        raise SystemExit("--config-write-repeats must be >= 1")
    if args.write_delay < 0:
        raise SystemExit("--write-delay must be >= 0")
    if args.periodic_reset_cycles < 0:
        raise SystemExit("--periodic-reset-cycles must be >= 0")
    if args.periodic_trigger_cycles < 0:
        raise SystemExit("--periodic-trigger-cycles must be >= 0")
    for flag_name, value in (
        ("--vref-dac", args.vref_dac),
        ("--vcm-dac", args.vcm_dac),
        ("--threshold-global", args.threshold_global),
    ):
        if value < 0 or value > 255:
            raise SystemExit(f"{flag_name} must be in the range 0..255")

    try:
        payload, target = load_target_network(
            args.network_json,
            requested_io_group=args.io_group,
            requested_io_channel=args.io_channel,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    inferred_tile = target.pacman_tile
    pacman_tile = args.pacman_tile or inferred_tile
    # The same tile number controls BOTH PACMAN UART inversion and the
    # tile-selective ASIC reset.  Never allow an override to point at a tile
    # different from the one implied by the selected JSON io_channels.
    if pacman_tile != inferred_tile:
        print(
            f"ERROR: --pacman-tile {pacman_tile} disagrees with io_channel-derived "
            f"tile {inferred_tile}; refusing to risk resetting the wrong tile",
            file=sys.stderr,
        )
        return 2

    pacman_config = args.pacman_config or f"io/pacman_io{target.io_group}.json"

    print_plan(args.network_json, payload, target, full=args.print_plan)
    print(f"PACMAN config           : {pacman_config}")
    print(f"PACMAN tile helper      : {pacman_tile}")
    print(f"ASIC reset scope        : PACMAN tile {pacman_tile} ONLY")
    print(f"blind write repeats     : {args.config_write_repeats}")
    print("ASIC operating config   :")
    for key, value in operational_config_from_args(args).items():
        print(f"  {key:32s}: {value}")
    print(f"dry run                 : {args.dry_run}")
    print()

    if args.dry_run:
        if args.reset_only:
            print("DRY RUN RESET-ONLY")
            print(
                f"[reset] would call PACMAN_IO.reset_tiles() and reset ONLY "
                f"PACMAN tile {pacman_tile} with 6 x 2048-cycle pulses"
            )
        else:
            execute_dry_run(target)
        return 0

    if not args.yes:
        if args.reset_only:
            prompt = (
                f"RESET-ONLY: call PACMAN_IO.reset_tiles() to hard-reset ONLY PACMAN tile "
                f"{pacman_tile} on io_group {target.io_group}, then exit? [y/N] "
            )
        else:
            prompt = (
                f"Reset ONLY PACMAN tile {pacman_tile} on io_group {target.io_group}, build "
                f"{target.chip_count} chips from roots "
                f"{[tree.root_chip for tree in target.trees]} while masked, then unmask "
                "the complete network? [y/N] "
            )
        if input(prompt).strip().lower() not in {"y", "yes"}:
            print("Cancelled; no LArPix reset/configuration was issued.")
            return 0

    logger = ConsoleLogger(verbose=args.verbose_hardware)

    try:
        hardware_by_channel = connect_all_hardware(
            target,
            pacman_config=pacman_config,
            pacman_tile=pacman_tile,
            args=args,
            logger=logger,
        )

        # ONE TILE-SCOPED reset sequence only. v2.3 uses the exact PACMAN_IO.reset_tiles() API used by network_base. Recreate all rooted-channel Controllers afterward.
        primary = hardware_by_channel[target.trees[0].io_channel]
        print(
            f"[reset] PACMAN_IO.reset_tiles tile-selective hard reset: io_group {target.io_group}, "
            f"PACMAN tile {pacman_tile} ONLY; 6 x 2048-cycle pulses"
        )
        primary.reset_target_tile()
        for tree in target.trees[1:]:
            hardware_by_channel[tree.io_channel]._new_controller()

        if args.reset_only:
            print()
            print(
                f"RESET-ONLY SUCCESS: completed PACMAN_IO.reset_tiles tile-selective hard reset "
                f"for io_group {target.io_group}, PACMAN tile {pacman_tile}; "
                "no ASIC network configuration was attempted."
            )
            return 0

        # Establish every physical root first.  Roots stay masked and expose no
        # daughter until their own root configuration is complete.
        for tree in target.trees:
            print(
                f"[root io={tree.io_channel:2d}] configure root {tree.root_chip:3d} "
                "MASKED"
            )
            hardware_by_channel[tree.io_channel].bring_up_root_masked(tree.root_chip)

        # Now walk the declared maps globally breadth-first across all roots.
        # No reset and no unmask happens here.
        total = target.edge_count
        for idx, (depth, tree, edge) in enumerate(global_build_order(target), 1):
            print(
                f"[{idx:3d}/{total:3d} depth={depth:2d} io={tree.io_channel:2d}] add "
                f"{edge.mother}->{edge.daughter} ({edge.direction}); KEEP MASKED"
            )
            hardware_by_channel[tree.io_channel].bring_up_edge_masked(edge)

        # Only now is any ASIC allowed to become active.  Global deepest-first
        # ordering keeps every parent/root that is still needed as a quiet
        # configuration router masked until all descendants have their final
        # channel-mask write.  All roots are therefore unmasked last.
        print(
            f"[final] complete topology established; unmasking "
            f"{target.chip_count} chips deepest-to-roots"
        )
        for depth, io_channel, chip_id in final_unmask_order(target):
            hardware_by_channel[io_channel].unmask_chip(chip_id)

    except KeyboardInterrupt:
        print(
            "\nINTERRUPTED: hardware state is PARTIAL/UNKNOWN. "
            "No cleanup or additional reset was issued.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        print(
            "\nERROR during automatic masked crawler bring-up. Hardware state is "
            "PARTIAL/UNKNOWN; no automatic cleanup or reset was attempted.",
            file=sys.stderr,
        )
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        if args.verbose_hardware:
            traceback.print_exc()
        return 3

    print()
    print(
        f"SUCCESS: configured and unmasked {target.chip_count} chips across "
        f"io_channels {target.io_channels} with one PACMAN_IO.reset_tiles tile-selective ASIC hard-reset sequence."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
