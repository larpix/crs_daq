#!/usr/bin/env python3
"""
tile5_chip_crawler.py

Conservative, operator-driven LArPix-v2b Hydra crawler for one PACMAN io_channel.

Design goals
------------
* One unknown ASIC/link is exposed at a time.
* The already-established network is treated as a known-good rooted tree.
* Branching is supported: one mother chip may have multiple daughters.
* Candidate ASICs are configured repeatedly while their downstream PISO is OFF.
* Candidate channels stay masked until the downstream return path is enabled.
* Every hardware action and operator decision is timestamped to:
    - events.jsonl   (machine-readable)
    - crawler.log    (human-readable)
* No configuration readback is performed during normal crawler operation.
* Voltage/current monitoring and packet-rate monitoring are deliberately external.

The 10x10 tile geometry is:
    bottom row: 11 .. 20
    ...
    top row:   101 .. 110

so:
    RIGHT = +1
    LEFT  = -1
    UP    = +10
    DOWN  = -10

UART mapping, decoded from the 2x2 Hydra network JSON:
    daughter DOWN : mother PISO3 -> daughter POSI2 ; daughter PISO1 -> mother POSI0
    daughter LEFT : mother PISO0 -> daughter POSI3 ; daughter PISO2 -> mother POSI1
    daughter UP   : mother PISO1 -> daughter POSI0 ; daughter PISO3 -> mother POSI2
    daughter RIGHT: mother PISO2 -> daughter POSI1 ; daughter PISO0 -> mother POSI3

This script intentionally uses larpix-control for the ASIC operations.  If the
2x2 crs_daq ``base.pacman_base`` helper is importable, it can optionally be used
for the small amount of PACMAN-specific UART inversion / RX-enable setup.

IMPORTANT
---------
This tool cannot prove that a blind configuration write reached an ASIC.  A
successful software call only means the local send operation did not raise an
exception.  The operator decides whether a transition is good by inspecting the
external packet-rate / current / voltage monitoring.
"""

from __future__ import annotations

import argparse
import cmd
import json
import os
import shlex
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCRIPT_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Geometry / UART mapping
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DirectionSpec:
    name: str
    delta: int
    mother_upstream_piso: int
    daughter_posi_from_mother: int
    daughter_downstream_piso: int
    mother_posi_from_daughter: int


DIRECTIONS: Dict[str, DirectionSpec] = {
    "down": DirectionSpec(
        name="down",
        delta=-10,
        mother_upstream_piso=3,
        daughter_posi_from_mother=2,
        daughter_downstream_piso=1,
        mother_posi_from_daughter=0,
    ),
    "left": DirectionSpec(
        name="left",
        delta=-1,
        mother_upstream_piso=0,
        daughter_posi_from_mother=3,
        daughter_downstream_piso=2,
        mother_posi_from_daughter=1,
    ),
    "up": DirectionSpec(
        name="up",
        delta=+10,
        mother_upstream_piso=1,
        daughter_posi_from_mother=0,
        daughter_downstream_piso=3,
        mother_posi_from_daughter=2,
    ),
    "right": DirectionSpec(
        name="right",
        delta=+1,
        mother_upstream_piso=2,
        daughter_posi_from_mother=1,
        daughter_downstream_piso=0,
        mother_posi_from_daughter=3,
    ),
}

DIRECTION_ALIASES = {
    "d": "down",
    "down": "down",
    "l": "left",
    "left": "left",
    "u": "up",
    "up": "up",
    "r": "right",
    "right": "right",
}


def chip_xy(chip_id: int) -> Tuple[int, int]:
    """Return (x, y), each 0..9, for chip IDs 11..110."""
    if chip_id < 11 or chip_id > 110:
        raise ValueError(f"chip {chip_id} is outside the 10x10 tile (11..110)")
    offset = chip_id - 11
    return offset % 10, offset // 10


def neighbor(chip_id: int, direction: str) -> int:
    direction = normalize_direction(direction)
    x, y = chip_xy(chip_id)

    if direction == "left":
        if x == 0:
            raise ValueError(f"chip {chip_id} has no LEFT neighbor")
        return chip_id - 1
    if direction == "right":
        if x == 9:
            raise ValueError(f"chip {chip_id} has no RIGHT neighbor")
        return chip_id + 1
    if direction == "down":
        if y == 0:
            raise ValueError(f"chip {chip_id} has no DOWN neighbor")
        return chip_id - 10
    if direction == "up":
        if y == 9:
            raise ValueError(f"chip {chip_id} has no UP neighbor")
        return chip_id + 10

    raise AssertionError(direction)


def normalize_direction(value: str) -> str:
    try:
        return DIRECTION_ALIASES[value.strip().lower()]
    except KeyError:
        raise ValueError("direction must be one of: up/down/left/right (or u/d/l/r)")


# ---------------------------------------------------------------------------
# Experiment state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Edge:
    mother: int
    daughter: int
    direction: str

    @classmethod
    def make(cls, mother: int, direction: str) -> "Edge":
        direction = normalize_direction(direction)
        return cls(mother=mother, daughter=neighbor(mother, direction), direction=direction)


@dataclass
class CrawlerState:
    root_chip: int
    accepted_edges: List[Edge]
    pending: Optional[Edge] = None
    hardware_dirty: bool = True

    @property
    def accepted_chips(self) -> List[int]:
        chips = [self.root_chip]
        chips.extend(edge.daughter for edge in self.accepted_edges)
        return chips

    @property
    def visible_chips(self) -> List[int]:
        chips = list(self.accepted_chips)
        if self.pending is not None:
            chips.append(self.pending.daughter)
        return chips

    def is_accepted(self, chip_id: int) -> bool:
        return chip_id in set(self.accepted_chips)

    def children(self, mother: int, include_pending: bool = False) -> List[Edge]:
        result = [e for e in self.accepted_edges if e.mother == mother]
        if include_pending and self.pending is not None and self.pending.mother == mother:
            result.append(self.pending)
        return result

    def validate_new_edge(self, edge: Edge) -> None:
        if not self.is_accepted(edge.mother):
            raise ValueError(
                f"mother chip {edge.mother} is not in the accepted known-good tree"
            )

        used = set(self.accepted_chips)
        if self.pending is not None:
            used.add(self.pending.daughter)

        if edge.daughter in used:
            raise ValueError(
                f"chip {edge.daughter} is already in the current tree/trial; "
                "loops and duplicate parents are intentionally forbidden"
            )

        # One physical side on a mother can only go to one neighbor.
        for existing in self.children(edge.mother, include_pending=True):
            if existing.direction == edge.direction:
                raise ValueError(
                    f"mother {edge.mother} already uses its {edge.direction.upper()} side "
                    f"for chip {existing.daughter}"
                )

    def accept_pending(self) -> Edge:
        if self.pending is None:
            raise ValueError("there is no pending candidate to accept")
        edge = self.pending
        self.accepted_edges.append(edge)
        self.pending = None
        return edge

    def back_one(self) -> Tuple[str, Optional[Edge]]:
        if self.pending is not None:
            edge = self.pending
            self.pending = None
            return "pending", edge
        if self.accepted_edges:
            edge = self.accepted_edges.pop()
            return "accepted", edge
        return "root", None

    def validate_tree(self) -> None:
        known = {self.root_chip}
        for idx, edge in enumerate(self.accepted_edges):
            if edge.mother not in known:
                raise ValueError(
                    f"accepted edge #{idx} has mother {edge.mother} before that mother "
                    "exists in the tree"
                )
            if edge.daughter in known:
                raise ValueError(f"chip {edge.daughter} occurs more than once in tree")
            expected = neighbor(edge.mother, edge.direction)
            if expected != edge.daughter:
                raise ValueError(
                    f"edge {edge.mother}->{edge.daughter} does not match {edge.direction}"
                )
            known.add(edge.daughter)

        if self.pending is not None:
            if self.pending.mother not in known:
                raise ValueError("pending mother is not accepted")
            if self.pending.daughter in known:
                raise ValueError("pending daughter is already accepted")

    def as_json(self) -> Dict[str, Any]:
        return {
            "root_chip": self.root_chip,
            "accepted_edges": [asdict(e) for e in self.accepted_edges],
            "pending": asdict(self.pending) if self.pending is not None else None,
            "hardware_dirty": self.hardware_dirty,
        }


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class SessionLogger:
    def __init__(self, base_dir: Path, session_name: Optional[str] = None):
        now = datetime.now().astimezone()
        if session_name is None:
            session_name = now.strftime("crawler_%Y%m%d_%H%M%S_%Z")

        self.session_dir = base_dir / session_name
        self.session_dir.mkdir(parents=True, exist_ok=False)

        self.jsonl_path = self.session_dir / "events.jsonl"
        self.text_path = self.session_dir / "crawler.log"
        self.state_path = self.session_dir / "state.json"

        self._seq = 0
        self._t0_ns = time.monotonic_ns()
        self.recent: List[Dict[str, Any]] = []

    def event(
        self,
        event: str,
        message: str = "",
        *,
        console: bool = False,
        level: str = "INFO",
        **data: Any,
    ) -> Dict[str, Any]:
        self._seq += 1
        utc = datetime.now(timezone.utc)
        local = datetime.now().astimezone()
        mono_ns = time.monotonic_ns()

        record = {
            "seq": self._seq,
            "event": event,
            "level": level,
            "message": message,
            "timestamp_utc": utc.isoformat(timespec="microseconds"),
            "timestamp_local": local.isoformat(timespec="microseconds"),
            "epoch_ns": time.time_ns(),
            "monotonic_ns": mono_ns,
            "elapsed_s": (mono_ns - self._t0_ns) / 1e9,
            **data,
        }

        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")

        compact = ""
        if data:
            compact = " " + " ".join(
                f"{key}={json.dumps(value, default=str)}"
                for key, value in data.items()
            )
        line = (
            f"{record['timestamp_local']} "
            f"#{self._seq:05d} {level:<5} {event}"
            f"{': ' + message if message else ''}{compact}\n"
        )
        with self.text_path.open("a", encoding="utf-8") as f:
            f.write(line)

        self.recent.append(record)
        self.recent = self.recent[-500:]

        if console:
            print(line.rstrip())

        return record

    def save_state(self, state: CrawlerState, run_config: Dict[str, Any]) -> None:
        payload = {
            "script_version": SCRIPT_VERSION,
            "saved_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "run_config": run_config,
            "state": state.as_json(),
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_path)

    def write_session_info(self, payload: Dict[str, Any]) -> None:
        path = self.session_dir / "session.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Pedestal configuration
# ---------------------------------------------------------------------------

PEDESTAL_VALUES: Dict[str, Any] = {
    "ref_current_trim": 0,

    "tx_slices0": 15,
    "tx_slices1": 15,
    "tx_slices2": 15,
    "tx_slices3": 15,

    "i_tx_diff0": 0,
    "i_tx_diff1": 0,
    "i_tx_diff2": 0,
    "i_tx_diff3": 0,

    "r_term0": 8,
    "r_term1": 8,
    "r_term2": 8,
    "r_term3": 8,

    "threshold_global": 255,
    "vcm_dac": 50,
    "vref_dac": 185,

    "enable_periodic_trigger": 1,
    "enable_rolling_periodic_trigger": 1,
    "enable_periodic_reset": 1,
    "enable_rolling_periodic_reset": 1,

    "enable_hit_veto": 0,
    "enable_periodic_trigger_veto": 0,

    "periodic_trigger_cycles": 578125,
    "periodic_reset_cycles": 4,

    "periodic_trigger_mask": [0] * 64,
    "csa_enable": [1] * 64,
}

# Deliberately write these in a readable/stable order.
PEDESTAL_REGISTER_NAMES = [
    "ref_current_trim",
    "tx_slices0", "tx_slices1", "tx_slices2", "tx_slices3",
    "i_tx_diff0", "i_tx_diff1", "i_tx_diff2", "i_tx_diff3",
    "r_term0", "r_term1", "r_term2", "r_term3",
    "threshold_global",
    "vcm_dac",
    "vref_dac",
    "csa_enable",
    "enable_periodic_trigger",
    "enable_rolling_periodic_trigger",
    "enable_periodic_reset",
    "enable_rolling_periodic_reset",
    "enable_hit_veto",
    "enable_periodic_trigger_veto",
    "periodic_trigger_mask",
    "periodic_trigger_cycles",
    "periodic_reset_cycles",
    "channel_mask",
    "enable_piso_upstream",
    "enable_piso_downstream",
    "enable_posi",
    "chip_id",
]


# ---------------------------------------------------------------------------
# Hardware interface
# ---------------------------------------------------------------------------

@dataclass
class HardwareOptions:
    io_group: int
    io_channel: int
    pacman_config: str
    pacman_tile: Optional[int]
    use_crs_pacman_helpers: bool
    uart_clock_ratio: int
    root_posi: int
    root_downstream_piso: int
    config_write_repeats: int
    write_delay_s: float
    reset_length: int
    reset_pulses: int
    reset_gap_s: float


class HardwareBase:
    def __init__(self, options: HardwareOptions, log: SessionLogger):
        self.options = options
        self.log = log

    def connect(self) -> None:
        raise NotImplementedError

    def hard_reset(self) -> None:
        raise NotImplementedError

    def rebuild(self, state: CrawlerState, include_pending: bool = True) -> None:
        raise NotImplementedError


class DryRunHardware(HardwareBase):
    def connect(self) -> None:
        self.log.event(
            "DRYRUN_CONNECT",
            "No hardware will be touched",
            console=True,
            io_group=self.options.io_group,
            io_channel=self.options.io_channel,
            pacman_config=self.options.pacman_config,
        )

    def hard_reset(self) -> None:
        self.log.event(
            "RESET_BEGIN",
            "DRY RUN global LArPix reset",
            console=True,
            pulses=self.options.reset_pulses,
            length=self.options.reset_length,
        )
        for pulse in range(1, self.options.reset_pulses + 1):
            self.log.event(
                "RESET_PULSE",
                "DRY RUN reset pulse",
                pulse=pulse,
                length=self.options.reset_length,
            )
        self.log.event("RESET_END", "DRY RUN reset complete", console=True)

    def _simulate_chip(
        self,
        chip_id: int,
        *,
        mother: Optional[int],
        edge: Optional[Edge],
        is_root: bool,
        is_trial: bool,
    ) -> None:
        if edge is not None:
            spec = DIRECTIONS[edge.direction]
            self.log.event(
                "PARENT_RETURN_POSI_ENABLE",
                "DRY RUN prepare mother RX for daughter return",
                mother=edge.mother,
                daughter=edge.daughter,
                posi=spec.mother_posi_from_daughter,
            )
            self.log.event(
                "CANDIDATE_EXPOSED",
                "DRY RUN enable mother upstream PISO toward candidate",
                console=True,
                mother=edge.mother,
                daughter=edge.daughter,
                direction=edge.direction,
                mother_upstream_piso=spec.mother_upstream_piso,
            )

        for attempt in range(1, self.options.config_write_repeats + 1):
            self.log.event(
                "CHIP_ID_CLAIM",
                "DRY RUN write target chip_id while addressing hardware ID 1",
                chip=chip_id,
                attempt=attempt,
                address_chip_id=1,
                new_chip_id=chip_id,
            )
            self.log.event(
                "SAFE_CONFIG_WRITE",
                "DRY RUN masked pedestal configuration; downstream remains off",
                chip=chip_id,
                attempt=attempt,
                channel_mask="all masked",
                downstream="all off",
            )

        if is_root:
            ds_piso = self.options.root_downstream_piso
            return_target = "PACMAN"
        else:
            assert edge is not None
            ds_piso = DIRECTIONS[edge.direction].daughter_downstream_piso
            return_target = edge.mother

        self.log.event(
            "DOWNSTREAM_ENABLE",
            "DRY RUN enable single downstream PISO while channels remain masked",
            console=True,
            chip=chip_id,
            piso=ds_piso,
            return_target=return_target,
            is_trial=is_trial,
        )
        self.log.event(
            "CHIP_LIVE",
            "DRY RUN unmask all 64 channels; pedestal output may now flow",
            console=True,
            chip=chip_id,
            is_trial=is_trial,
        )

    def rebuild(self, state: CrawlerState, include_pending: bool = True) -> None:
        self.log.event(
            "REBUILD_BEGIN",
            "DRY RUN rebuild known-good tree",
            console=True,
            accepted_edges=[asdict(e) for e in state.accepted_edges],
            pending=asdict(state.pending) if state.pending else None,
            include_pending=include_pending,
        )
        self.hard_reset()
        self._simulate_chip(
            state.root_chip,
            mother=None,
            edge=None,
            is_root=True,
            is_trial=False,
        )
        for edge in state.accepted_edges:
            self._simulate_chip(
                edge.daughter,
                mother=edge.mother,
                edge=edge,
                is_root=False,
                is_trial=False,
            )
        if include_pending and state.pending is not None:
            self._simulate_chip(
                state.pending.daughter,
                mother=state.pending.mother,
                edge=state.pending,
                is_root=False,
                is_trial=True,
            )
        self.log.event("REBUILD_END", "DRY RUN rebuild complete", console=True)


class LarpixHardware(HardwareBase):
    """
    Real larpix-control implementation.

    PACMAN-specific setup is intentionally tiny:
      * PACMAN_IO(...)
      * set_uart_clock_ratio(...)
      * optional crs_daq pacman_base inversion / UART-RX helpers
      * reset_larpix(...)

    All Hydra topology and ASIC configuration logic lives in this file.
    """

    def __init__(self, options: HardwareOptions, log: SessionLogger):
        super().__init__(options, log)
        self.larpix = None
        self.io = None
        self.controller = None
        self._pacman_helper = None
        self._added_chip_ids = set()

    def connect(self) -> None:
        self.log.event(
            "HARDWARE_IMPORT_BEGIN",
            "Importing larpix-control",
            pacman_config=self.options.pacman_config,
        )

        try:
            import larpix
            import larpix.io
        except Exception as exc:
            self.log.event(
                "HARDWARE_IMPORT_ERROR",
                str(exc),
                console=True,
                level="ERROR",
                traceback=traceback.format_exc(),
            )
            raise RuntimeError(
                "Could not import larpix-control. Run this inside the CRS Python "
                "environment where `import larpix` works."
            ) from exc

        self.larpix = larpix
        self.io = larpix.io.PACMAN_IO(
            relaxed=True,
            config_filepath=self.options.pacman_config,
        )

        self.log.event(
            "PACMAN_IO_CREATED",
            "Created PACMAN_IO",
            console=True,
            io_group=self.options.io_group,
            io_channel=self.options.io_channel,
            pacman_config=self.options.pacman_config,
        )

        # Match the 2x2 hydra_v2b setup.
        self.log.event(
            "UART_CLOCK_SET_BEGIN",
            uart_clock_ratio=self.options.uart_clock_ratio,
            io_group=self.options.io_group,
            io_channel=self.options.io_channel,
        )
        self.io.set_uart_clock_ratio(
            self.options.io_channel,
            self.options.uart_clock_ratio,
            io_group=self.options.io_group,
        )
        self.log.event("UART_CLOCK_SET_END")

        if self.options.use_crs_pacman_helpers:
            self._setup_with_crs_pacman_helpers()

        self._new_controller()
        self.log.event("HARDWARE_CONNECT_END", "Hardware interface ready", console=True)

    def _setup_with_crs_pacman_helpers(self) -> None:
        try:
            from base import pacman_base
        except Exception as exc:
            self.log.event(
                "CRS_PACMAN_HELPER_UNAVAILABLE",
                "Could not import base.pacman_base; continuing without its "
                "PACMAN inversion/RX-enable helpers",
                console=True,
                level="WARNING",
                error=repr(exc),
            )
            return

        self._pacman_helper = pacman_base

        if self.options.pacman_tile is not None:
            self.log.event(
                "PACMAN_UART_INVERT_BEGIN",
                "Calling the existing 2x2 PACMAN UART inversion helper",
                io_group=self.options.io_group,
                pacman_tile=self.options.pacman_tile,
                asic_version="2b",
            )
            pacman_base.invert_pacman_uart(
                self.io,
                self.options.io_group,
                "2b",
                [self.options.pacman_tile],
            )
            self.log.event("PACMAN_UART_INVERT_END")
        else:
            self.log.event(
                "PACMAN_UART_INVERT_SKIPPED",
                "No --pacman-tile supplied; PACMAN inversion helper not called",
                console=True,
                level="WARNING",
            )

        # The 2x2 network/configure path enables the PACMAN receive UARTs that
        # correspond to the LArPix io_channel.  Keep this deliberately narrow.
        try:
            self.log.event(
                "PACMAN_UART_RX_ENABLE_BEGIN",
                io_group=self.options.io_group,
                io_channel=self.options.io_channel,
            )
            pacman_base.enable_pacman_uart_from_io_channel(
                self.io,
                self.options.io_group,
                [self.options.io_channel],
            )
            self.log.event("PACMAN_UART_RX_ENABLE_END")
        except AttributeError:
            self.log.event(
                "PACMAN_UART_RX_ENABLE_UNAVAILABLE",
                "base.pacman_base has no enable_pacman_uart_from_io_channel; "
                "continuing without it",
                console=True,
                level="WARNING",
            )

    def _new_controller(self) -> None:
        assert self.larpix is not None
        self.controller = self.larpix.Controller()
        self.controller.io = self.io
        self._added_chip_ids = set()
        self.log.event("CONTROLLER_RECREATED")

    def hard_reset(self) -> None:
        assert self.io is not None

        self.log.event(
            "RESET_BEGIN",
            "Global LArPix reset",
            console=True,
            io_group=self.options.io_group,
            pulses=self.options.reset_pulses,
            length=self.options.reset_length,
        )

        for pulse in range(1, self.options.reset_pulses + 1):
            t0 = time.monotonic()
            self.log.event(
                "RESET_PULSE_BEGIN",
                pulse=pulse,
                length=self.options.reset_length,
            )
            self.io.reset_larpix(
                length=self.options.reset_length,
                io_group=self.options.io_group,
            )
            # hydra_v2b sleeps for approximately the reset pulse length.
            wait_s = max(
                self.options.reset_gap_s,
                self.options.reset_length * 1e-6,
            )
            time.sleep(wait_s)
            self.log.event(
                "RESET_PULSE_END",
                pulse=pulse,
                duration_s=time.monotonic() - t0,
                wait_s=wait_s,
            )

        # Forget the previous in-memory chip objects.  Hardware has just been
        # reset, so reconstructing the software controller is conceptually clean.
        self._new_controller()

        self.log.event("RESET_END", "Global LArPix reset complete", console=True)

    def _key(self, chip_id: int) -> str:
        return f"{self.options.io_group}-{self.options.io_channel}-{chip_id}"

    def _ensure_chip(self, chip_id: int):
        key = self._key(chip_id)
        # Controller.chips is keyed by larpix.Key objects.  Keep our own small
        # integer set instead of relying on string-vs-Key dictionary membership.
        if chip_id not in self._added_chip_ids:
            self.controller.add_chip(key, version="2b")
            self._added_chip_ids.add(chip_id)
            self.log.event("CONTROLLER_ADD_CHIP", chip=chip_id, chip_key=key)
        return self.controller[key]

    @staticmethod
    def _ordered_register_addresses(config, names: Sequence[str]) -> List[int]:
        result: List[int] = []
        seen = set()
        for name in names:
            if name not in config.register_map:
                raise KeyError(f"configuration has no register named {name!r}")
            for register in config.register_map[name]:
                if register not in seen:
                    seen.add(register)
                    result.append(register)
        return result

    def _write_register_names(
        self,
        chip_id: int,
        names: Sequence[str],
        *,
        reason: str,
        attempt: Optional[int] = None,
    ) -> None:
        chip = self._ensure_chip(chip_id)
        addresses = self._ordered_register_addresses(chip.config, names)

        self.log.event(
            "REGISTER_WRITE_BEGIN",
            reason,
            chip=chip_id,
            chip_key=self._key(chip_id),
            register_names=list(names),
            register_addresses=addresses,
            attempt=attempt,
        )
        t0 = time.monotonic()
        self.controller.write_configuration(
            self._key(chip_id),
            registers=addresses,
            write_read=0,
        )
        self.log.event(
            "REGISTER_WRITE_END",
            reason,
            chip=chip_id,
            attempt=attempt,
            duration_s=time.monotonic() - t0,
        )

    def _claim_chip_id(self, chip_id: int, attempt: int) -> None:
        """
        Write chip_id=<chip_id> to a chip addressed as hardware ID 1.

        This follows the same packet-address override pattern used by
        larpix-control Controller.init_network(): the configuration object is
        keyed by the desired final chip ID, but the chip_id register write packet
        is explicitly addressed to 1 for the unconfigured candidate.
        """
        chip = self._ensure_chip(chip_id)
        chip.config.chip_id = chip_id

        regs = list(chip.config.register_map["chip_id"])
        packets = chip.get_configuration_write_packets(registers=regs)
        for packet in packets:
            packet.chip_id = 1
            packet.assign_parity()

        self.log.event(
            "CHIP_ID_CLAIM_BEGIN",
            "Address hardware ID 1 and assign the intended unique chip ID",
            chip=chip_id,
            address_chip_id=1,
            new_chip_id=chip_id,
            attempt=attempt,
            packet_count=len(packets),
        )
        t0 = time.monotonic()
        self.controller.send(packets)
        self.log.event(
            "CHIP_ID_CLAIM_END",
            chip=chip_id,
            attempt=attempt,
            duration_s=time.monotonic() - t0,
        )

    def _set_safe_config(
        self,
        chip_id: int,
        *,
        input_posi: int,
    ) -> None:
        chip = self._ensure_chip(chip_id)
        cfg = chip.config

        for name, value in PEDESTAL_VALUES.items():
            # Copy lists so larpix smart-list wrappers do not share Python
            # objects across chip configurations.
            setattr(cfg, name, list(value) if isinstance(value, list) else value)

        cfg.chip_id = chip_id

        # Safe bootstrap state:
        #   * no upstream PISO (cannot expose another unknown chip)
        #   * no downstream PISO (cannot send data back yet)
        #   * only the POSI needed to receive commands from the mother/PACMAN
        #   * all 64 channels masked
        cfg.enable_piso_upstream = [0, 0, 0, 0]
        cfg.enable_piso_downstream = [0, 0, 0, 0]
        posi = [0, 0, 0, 0]
        posi[input_posi] = 1
        cfg.enable_posi = posi
        cfg.channel_mask = [1] * 64

    def _blind_configure_masked(
        self,
        chip_id: int,
        *,
        input_posi: int,
        is_trial: bool,
    ) -> None:
        self._set_safe_config(chip_id, input_posi=input_posi)

        for attempt in range(1, self.options.config_write_repeats + 1):
            self.log.event(
                "SAFE_CONFIG_ATTEMPT_BEGIN",
                "Candidate/root remains masked and downstream-disabled",
                console=True,
                chip=chip_id,
                attempt=attempt,
                repeats=self.options.config_write_repeats,
                is_trial=is_trial,
            )

            # Re-try the transition from default hardware ID 1 to the intended
            # chip ID every pass.  If the first one already succeeded, the
            # address-1 packet simply no longer targets this chip, while the
            # following writes to the final ID continue to reinforce config.
            self._claim_chip_id(chip_id, attempt)

            self._write_register_names(
                chip_id,
                PEDESTAL_REGISTER_NAMES,
                reason="blind masked pedestal configuration",
                attempt=attempt,
            )

            self.log.event(
                "SAFE_CONFIG_ATTEMPT_END",
                chip=chip_id,
                attempt=attempt,
                is_trial=is_trial,
            )

            if attempt != self.options.config_write_repeats:
                time.sleep(self.options.write_delay_s)

    def _prepare_mother_for_daughter(self, edge: Edge) -> None:
        spec = DIRECTIONS[edge.direction]
        mother = self._ensure_chip(edge.mother)

        # Prepare the mother's receiver before the daughter is allowed to
        # return anything.
        posi = list(mother.config.enable_posi)
        posi[spec.mother_posi_from_daughter] = 1
        mother.config.enable_posi = posi
        self._write_register_names(
            edge.mother,
            ["enable_posi"],
            reason=(
                f"prepare mother {edge.mother} POSI"
                f"{spec.mother_posi_from_daughter} for daughter {edge.daughter} return"
            ),
        )

        # This is the experimentally important exposure transition: config
        # commands can now reach the otherwise-reset candidate.
        upstream = list(mother.config.enable_piso_upstream)
        upstream[spec.mother_upstream_piso] = 1
        mother.config.enable_piso_upstream = upstream

        self.log.event(
            "CANDIDATE_EXPOSURE_BEGIN",
            "About to enable exactly one mother upstream PISO",
            console=True,
            mother=edge.mother,
            daughter=edge.daughter,
            direction=edge.direction,
            mother_upstream_piso=spec.mother_upstream_piso,
        )
        self._write_register_names(
            edge.mother,
            ["enable_piso_upstream"],
            reason=(
                f"expose candidate {edge.daughter} from mother {edge.mother} "
                f"toward {edge.direction}"
            ),
        )
        self.log.event(
            "CANDIDATE_EXPOSED",
            "Candidate is now reachable for configuration; its downstream is still OFF",
            console=True,
            mother=edge.mother,
            daughter=edge.daughter,
            direction=edge.direction,
            mother_upstream_piso=spec.mother_upstream_piso,
        )

    def _enable_downstream_and_unmask(
        self,
        chip_id: int,
        *,
        downstream_piso: int,
        return_target: Any,
        is_trial: bool,
    ) -> None:
        chip = self._ensure_chip(chip_id)

        downstream = [0, 0, 0, 0]
        downstream[downstream_piso] = 1
        chip.config.enable_piso_downstream = downstream

        self.log.event(
            "DOWNSTREAM_ENABLE_BEGIN",
            "Channels are still masked; enabling the one allowed return PISO",
            console=True,
            chip=chip_id,
            piso=downstream_piso,
            return_target=return_target,
            is_trial=is_trial,
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
            is_trial=is_trial,
        )

        # The final transition.  Channel mask has priority over periodic trigger
        # behavior, so this is the cleanest timestamp for "candidate went live".
        chip.config.channel_mask = [0] * 64
        self.log.event(
            "UNMASK_BEGIN",
            "About to unmask all 64 channels",
            console=True,
            chip=chip_id,
            is_trial=is_trial,
        )
        self._write_register_names(
            chip_id,
            ["channel_mask"],
            reason=f"unmask chip {chip_id} after downstream is established",
        )
        self.log.event(
            "CHIP_LIVE",
            "All channels unmasked; pedestal packets may now flow",
            console=True,
            chip=chip_id,
            is_trial=is_trial,
            return_target=return_target,
        )

    def _bring_up_root(self, root_chip: int) -> None:
        self.log.event(
            "ROOT_BRINGUP_BEGIN",
            "Only the physical root should be reachable as hardware ID 1",
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
        self._enable_downstream_and_unmask(
            root_chip,
            downstream_piso=self.options.root_downstream_piso,
            return_target="PACMAN",
            is_trial=False,
        )
        self.log.event("ROOT_BRINGUP_END", root_chip=root_chip, console=True)

    def _bring_up_edge(self, edge: Edge, *, is_trial: bool) -> None:
        spec = DIRECTIONS[edge.direction]

        self.log.event(
            "EDGE_BRINGUP_BEGIN",
            "Bring up one daughter from an already configured mother",
            console=True,
            mother=edge.mother,
            daughter=edge.daughter,
            direction=edge.direction,
            is_trial=is_trial,
            uart_mapping={
                "mother_upstream_piso": spec.mother_upstream_piso,
                "daughter_posi_from_mother": spec.daughter_posi_from_mother,
                "daughter_downstream_piso": spec.daughter_downstream_piso,
                "mother_posi_from_daughter": spec.mother_posi_from_daughter,
            },
        )

        self._prepare_mother_for_daughter(edge)

        # Candidate is reachable through its reset-default POSIs, but its PISOs
        # are still disabled.  Repeatedly claim/configure it in that state.
        self._blind_configure_masked(
            edge.daughter,
            input_posi=spec.daughter_posi_from_mother,
            is_trial=is_trial,
        )

        self._enable_downstream_and_unmask(
            edge.daughter,
            downstream_piso=spec.daughter_downstream_piso,
            return_target=edge.mother,
            is_trial=is_trial,
        )

        self.log.event(
            "EDGE_BRINGUP_END",
            mother=edge.mother,
            daughter=edge.daughter,
            direction=edge.direction,
            is_trial=is_trial,
            console=True,
        )

    def rebuild(self, state: CrawlerState, include_pending: bool = True) -> None:
        state.validate_tree()

        self.log.event(
            "REBUILD_BEGIN",
            "Reset and reconstruct topology from the root outward",
            console=True,
            root_chip=state.root_chip,
            accepted_edges=[asdict(e) for e in state.accepted_edges],
            pending=asdict(state.pending) if state.pending else None,
            include_pending=include_pending,
        )

        self.hard_reset()
        self._bring_up_root(state.root_chip)

        # accepted_edges are kept in insertion/parent-before-child order.
        for edge in state.accepted_edges:
            self._bring_up_edge(edge, is_trial=False)

        if include_pending and state.pending is not None:
            self._bring_up_edge(state.pending, is_trial=True)
            self.log.event(
                "TRIAL_HOLD",
                "Crawler is holding. Inspect external packet-rate/current/voltage monitors.",
                console=True,
                pending=asdict(state.pending),
            )

        self.log.event("REBUILD_END", "Topology reconstruction complete", console=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render_tree(state: CrawlerState) -> str:
    accepted_children: Dict[int, List[Tuple[Edge, bool]]] = {}
    for edge in state.accepted_edges:
        accepted_children.setdefault(edge.mother, []).append((edge, False))
    if state.pending is not None:
        accepted_children.setdefault(state.pending.mother, []).append((state.pending, True))

    lines: List[str] = [f"{state.root_chip} [ROOT]"]

    def walk(mother: int, prefix: str) -> None:
        children = accepted_children.get(mother, [])
        for idx, (edge, pending) in enumerate(children):
            last = idx == len(children) - 1
            connector = "└── " if last else "├── "
            status = " [TRIAL ?]" if pending else " [GOOD]"
            lines.append(
                f"{prefix}{connector}{edge.daughter}{status} "
                f"({edge.direction.upper()} from {edge.mother})"
            )
            walk(edge.daughter, prefix + ("    " if last else "│   "))

    walk(state.root_chip, "")
    return "\n".join(lines)


def render_grid(state: CrawlerState) -> str:
    accepted = set(state.accepted_chips)
    pending = state.pending.daughter if state.pending else None

    rows = []
    legend = "R=root  A=accepted  ?=trial  .=not in tree"
    rows.append(legend)
    for y in range(9, -1, -1):
        row = []
        for x in range(10):
            chip = 11 + 10 * y + x
            if chip == state.root_chip:
                marker = "R"
            elif chip == pending:
                marker = "?"
            elif chip in accepted:
                marker = "A"
            else:
                marker = "."
            row.append(f"{chip:3d}{marker}")
        rows.append(" ".join(row))
    return "\n".join(rows)


def uart_map_text() -> str:
    header = (
        "direction | delta | mother config TX | daughter config RX | "
        "daughter return TX | mother return RX"
    )
    sep = "-" * len(header)
    lines = [header, sep]
    for direction in ("down", "left", "up", "right"):
        d = DIRECTIONS[direction]
        lines.append(
            f"{direction:9s} | {d.delta:+4d} | "
            f"PISO{d.mother_upstream_piso:1d}            | "
            f"POSI{d.daughter_posi_from_mother:1d}             | "
            f"PISO{d.daughter_downstream_piso:1d}              | "
            f"POSI{d.mother_posi_from_daughter:1d}"
        )
    return "\n".join(lines)


class CrawlerShell(cmd.Cmd):
    intro = (
        "\nLArPix-v2b chip-by-chip crawler.\n"
        "Type 'help' for commands.  Use 'note <text>' while watching the external monitors.\n"
    )
    prompt = "crawler> "

    def __init__(
        self,
        state: CrawlerState,
        hardware: HardwareBase,
        log: SessionLogger,
        run_config: Dict[str, Any],
        *,
        assume_yes: bool = False,
    ):
        super().__init__()
        self.state = state
        self.hardware = hardware
        self.log = log
        self.run_config = run_config
        self.assume_yes = assume_yes
        self._save()

    # ---- helpers ---------------------------------------------------------

    def _save(self) -> None:
        self.log.save_state(self.state, self.run_config)

    def _confirm(self, text: str, default: bool = False) -> bool:
        if self.assume_yes:
            self.log.event(
                "AUTO_CONFIRM",
                text,
                response=True,
            )
            return True
        suffix = " [Y/n] " if default else " [y/N] "
        answer = input(text + suffix).strip().lower()
        if not answer:
            return default
        return answer in {"y", "yes"}

    def _run_rebuild(self, *, include_pending: bool) -> bool:
        try:
            self.hardware.rebuild(self.state, include_pending=include_pending)
        except KeyboardInterrupt:
            self.state.hardware_dirty = True
            self._save()
            self.log.event(
                "REBUILD_INTERRUPTED",
                "KeyboardInterrupt during hardware operation",
                console=True,
                level="ERROR",
            )
            print(
                "\nHardware state is UNKNOWN. Use 'safe-reset' or 'reconfigure' "
                "before trusting the topology."
            )
            return False
        except Exception as exc:
            self.state.hardware_dirty = True
            self._save()
            self.log.event(
                "REBUILD_ERROR",
                str(exc),
                console=True,
                level="ERROR",
                exception=repr(exc),
                traceback=traceback.format_exc(),
            )
            print(
                "\nHardware operation raised an exception. The software topology "
                "was preserved, but hardware state is marked DIRTY/UNKNOWN."
            )
            return False
        else:
            self.state.hardware_dirty = False
            self._save()
            return True

    # ---- commands --------------------------------------------------------

    def do_status(self, arg: str) -> None:
        """status
        Show the current software topology and whether hardware matches the last rebuild.
        """
        print()
        print(render_tree(self.state))
        print()
        print(f"hardware_dirty : {self.state.hardware_dirty}")
        print(f"accepted chips : {len(self.state.accepted_chips)}")
        print(
            "pending trial  : "
            + (
                f"{self.state.pending.mother} -> {self.state.pending.daughter} "
                f"({self.state.pending.direction})"
                if self.state.pending
                else "none"
            )
        )
        print(f"log directory  : {self.log.session_dir}")

    def do_tree(self, arg: str) -> None:
        """tree
        Show the rooted known-good tree plus the current trial candidate.
        """
        print(render_tree(self.state))

    def do_grid(self, arg: str) -> None:
        """grid
        Show the 10x10 chip-ID grid with root/accepted/trial markers.
        """
        print(render_grid(self.state))

    def do_map(self, arg: str) -> None:
        """map
        Show the hard-coded geometry <-> UART mapping used by the crawler.
        """
        print(uart_map_text())

    def do_config(self, arg: str) -> None:
        """config
        Show the pedestal values applied during blind candidate configuration.
        """
        payload = dict(PEDESTAL_VALUES)
        payload["channel_mask_during_safe_config"] = [1] * 64
        payload["channel_mask_after_go_live"] = [0] * 64
        print(json.dumps(payload, indent=2))

    def do_note(self, arg: str) -> None:
        """note <free-form operator observation>
        Add a timestamped observation to both logs.
        Example: note packet rate jumped immediately after unmask
        """
        text = arg.strip()
        if not text:
            print("usage: note <text>")
            return
        self.log.event(
            "OPERATOR_NOTE",
            text,
            console=True,
            topology=self.state.as_json(),
        )

    def do_history(self, arg: str) -> None:
        """history [N]
        Show the most recent N logged events (default 20).
        """
        try:
            n = int(arg.strip()) if arg.strip() else 20
        except ValueError:
            print("usage: history [integer]")
            return
        for rec in self.log.recent[-n:]:
            print(
                f"{rec['timestamp_local']} #{rec['seq']:05d} "
                f"{rec['event']}: {rec['message']}"
            )

    def do_accept(self, arg: str) -> None:
        """accept
        Promote the current trial candidate to the known-good tree WITHOUT touching hardware.
        This is a software bookkeeping decision after you inspect the external monitors.
        """
        if self.state.pending is None:
            print("No pending candidate.")
            return
        edge = self.state.accept_pending()
        self._save()
        self.log.event(
            "USER_ACCEPT",
            "Operator accepted the current trial as known-good",
            console=True,
            edge=asdict(edge),
        )
        print(
            f"Accepted {edge.daughter}. No hardware command was sent. "
            "The next rebuild will treat it as known-good."
        )

    def do_expand(self, arg: str) -> None:
        """expand <mother_chip> <direction>
        Reset, rebuild the known-good tree, then expose/configure exactly one new candidate.

        If a previous trial is still pending, 'expand' first asks to accept that
        trial as known-good.  This matches the normal crawl workflow:
            inspect -> expand again means "the last one looked good".
        """
        try:
            parts = shlex.split(arg)
        except ValueError as exc:
            print(exc)
            return
        if len(parts) != 2:
            print("usage: expand <mother_chip> <up|down|left|right>")
            return

        try:
            mother = int(parts[0])
            direction = normalize_direction(parts[1])
        except ValueError as exc:
            print(exc)
            return

        if self.state.pending is not None:
            p = self.state.pending
            question = (
                f"Current trial is {p.mother}->{p.daughter} ({p.direction}). "
                "Starting another expansion means ACCEPTING it as known-good. Continue?"
            )
            if not self._confirm(question):
                print("Expansion cancelled; current trial remains pending.")
                return
            accepted = self.state.accept_pending()
            self.log.event(
                "USER_ACCEPT",
                "Pending trial auto-accepted because operator requested next expansion",
                console=True,
                edge=asdict(accepted),
            )

        try:
            edge = Edge.make(mother, direction)
            self.state.validate_new_edge(edge)
        except ValueError as exc:
            print(f"Cannot expand: {exc}")
            return

        self.state.pending = edge
        self.state.hardware_dirty = True
        self._save()

        spec = DIRECTIONS[direction]
        self.log.event(
            "USER_EXPAND",
            "Operator requested one-chip expansion",
            console=True,
            edge=asdict(edge),
            uart_mapping=asdict(spec),
        )

        print()
        print(
            f"TRIAL: {edge.mother} -> {edge.daughter} "
            f"({edge.direction.upper()})"
        )
        print(
            f"  mother config TX : PISO{spec.mother_upstream_piso}\n"
            f"  daughter config RX: POSI{spec.daughter_posi_from_mother}\n"
            f"  daughter return TX: PISO{spec.daughter_downstream_piso}\n"
            f"  mother return RX : POSI{spec.mother_posi_from_daughter}"
        )
        print()

        ok = self._run_rebuild(include_pending=True)
        if ok:
            print(
                "\nHOLDING on the trial candidate. Inspect packet-rate/current/voltage.\n"
                "Then use:  accept   |   expand <mother> <dir>   |   back   |   "
                "reconfigure   |   note <observation>\n"
            )

    def do_back(self, arg: str) -> None:
        """back
        Roll back one experimental step, then RESET and rebuild.

        * If a trial candidate exists: discard that trial.
        * Otherwise: remove the most recently accepted edge.
        """
        kind, edge = self.state.back_one()
        if kind == "root":
            print("Already at the root-only topology; nothing to remove.")
        else:
            assert edge is not None
            self.log.event(
                "USER_BACK",
                f"Operator removed {kind} edge",
                console=True,
                removed_kind=kind,
                edge=asdict(edge),
            )
            print(
                f"Removed {kind} edge {edge.mother}->{edge.daughter} "
                f"({edge.direction})."
            )

        self.state.hardware_dirty = True
        self._save()
        self._run_rebuild(include_pending=False)

    def do_reconfigure(self, arg: str) -> None:
        """reconfigure
        RESET and replay the current topology.

        If a trial candidate exists, it is re-tested using the full safe sequence:
        expose -> repeated masked blind writes -> downstream enable -> unmask.
        """
        self.log.event(
            "USER_RECONFIGURE",
            "Operator requested reset/reconfigure",
            console=True,
            include_pending=self.state.pending is not None,
        )
        self.state.hardware_dirty = True
        self._save()
        self._run_rebuild(include_pending=self.state.pending is not None)

    def do_start(self, arg: str) -> None:
        """start
        Alias for reconfigure. Useful when the shell was started without touching hardware.
        """
        self.do_reconfigure(arg)

    def do_safe_reset(self, arg: str) -> None:
        """safe-reset
        Issue only the global LArPix reset. Do NOT rebuild any topology afterward.

        Software topology is retained, but hardware is marked DIRTY/UNKNOWN until
        'reconfigure' is run.
        """
        if not self._confirm(
            "Issue a global LArPix reset and leave the network reset?",
            default=False,
        ):
            print("safe-reset cancelled.")
            return

        self.log.event(
            "USER_SAFE_RESET",
            "Operator requested reset-only",
            console=True,
        )
        try:
            self.hardware.hard_reset()
        except Exception as exc:
            self.log.event(
                "SAFE_RESET_ERROR",
                str(exc),
                console=True,
                level="ERROR",
                traceback=traceback.format_exc(),
            )
            print(f"Reset raised: {exc}")
        finally:
            self.state.hardware_dirty = True
            self._save()

    # cmd.Cmd treats '-' as a delimiter, so expose an underscore alias too.
    do_safe_reset.__doc__ += "\nAlias in the shell: safe_reset"

    def do_safe_reset_alias(self, arg: str) -> None:
        self.do_safe_reset(arg)

    def default(self, line: str) -> None:
        # Friendly support for the hyphenated spelling and common short aliases.
        stripped = line.strip()
        if stripped == "safe-reset":
            self.do_safe_reset("")
            return
        if stripped in {"r", "reset", "reconf"}:
            self.do_reconfigure("")
            return
        if stripped in {"b", "rollback"}:
            self.do_back("")
            return
        if stripped in {"q", "exit"}:
            self.do_quit("")
            return

        if stripped.startswith("e "):
            self.do_expand(stripped[2:])
            return

        print(f"Unknown command: {line!r}. Type 'help'.")

    def do_quit(self, arg: str) -> bool:
        """quit
        Exit the crawler. Hardware is left exactly as it is.
        Use 'safe-reset' first if you want to leave all ASICs reset.
        """
        self.log.event(
            "USER_QUIT",
            "Crawler shell exiting; hardware left unchanged",
            console=True,
            topology=self.state.as_json(),
        )
        return True

    def do_EOF(self, arg: str) -> bool:
        print()
        return self.do_quit(arg)

    def emptyline(self) -> None:
        # Do not repeat the previous command. That would be dangerous for a
        # hardware-control shell.
        pass


# ---------------------------------------------------------------------------
# Argument parsing / main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Conservative one-chip-at-a-time LArPix-v2b Hydra crawler with "
            "branching, rollback, and timestamped JSONL logging."
        )
    )

    p.add_argument("--io-group", type=int, default=6)
    p.add_argument(
        "--io-channel",
        type=int,
        required=True,
        help="Single PACMAN/LArPix io_channel used by this crawler.",
    )
    p.add_argument(
        "--root-chip",
        type=int,
        required=True,
        help="Physical root ASIC chip ID to assign after reset (11..110).",
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
            "PACMAN tile passed only to the optional crs_daq UART inversion helper. "
            "No tile<->io_channel inference is done by this script."
        ),
    )

    p.add_argument(
        "--no-crs-pacman-helpers",
        action="store_true",
        help=(
            "Do not import base.pacman_base for PACMAN UART inversion/RX enable. "
            "Core Hydra logic never depends on crs_daq."
        ),
    )

    p.add_argument("--uart-clock-ratio", type=int, default=10)

    # From the network JSON: ext->root uses geometric slot 3, which maps to a
    # root POSI1 input and root downstream PISO0. Keep configurable anyway.
    p.add_argument("--root-posi", type=int, choices=range(4), default=1)
    p.add_argument("--root-downstream-piso", type=int, choices=range(4), default=0)

    p.add_argument(
        "--config-write-repeats",
        type=int,
        default=5,
        help="Number of blind ID/config attempts for every chip (default: 5).",
    )
    p.add_argument(
        "--write-delay",
        type=float,
        default=0.050,
        help="Seconds between blind configuration attempts (default: 0.050).",
    )

    p.add_argument("--reset-length", type=int, default=4096 * 4)
    p.add_argument("--reset-pulses", type=int, default=2)
    p.add_argument("--reset-gap", type=float, default=0.020)

    p.add_argument(
        "--log-dir",
        default="crawler_logs",
        help="Parent directory for per-session logs (default: crawler_logs).",
    )
    p.add_argument(
        "--session-name",
        default=None,
        help="Optional explicit session directory name.",
    )

    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Exercise the full CLI/state machine without importing or touching hardware.",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Automatically answer yes to crawler confirmations.",
    )
    p.add_argument(
        "--no-auto-start",
        action="store_true",
        help="Open the shell without immediately resetting/configuring the root.",
    )

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # Geometry validation happens before any hardware object is made.
    chip_xy(args.root_chip)

    if args.config_write_repeats < 1:
        raise SystemExit("--config-write-repeats must be >= 1")
    if args.reset_pulses < 1:
        raise SystemExit("--reset-pulses must be >= 1")

    pacman_config = args.pacman_config or f"io/pacman_io{args.io_group}.json"

    run_config: Dict[str, Any] = {
        "io_group": args.io_group,
        "io_channel": args.io_channel,
        "root_chip": args.root_chip,
        "pacman_config": pacman_config,
        "pacman_tile": args.pacman_tile,
        "use_crs_pacman_helpers": not args.no_crs_pacman_helpers,
        "uart_clock_ratio": args.uart_clock_ratio,
        "root_posi": args.root_posi,
        "root_downstream_piso": args.root_downstream_piso,
        "config_write_repeats": args.config_write_repeats,
        "write_delay_s": args.write_delay,
        "reset_length": args.reset_length,
        "reset_pulses": args.reset_pulses,
        "reset_gap_s": args.reset_gap,
        "dry_run": args.dry_run,
    }

    log = SessionLogger(Path(args.log_dir), session_name=args.session_name)
    log.write_session_info(
        {
            "script_version": SCRIPT_VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "argv": sys.argv,
            "cwd": os.getcwd(),
            "run_config": run_config,
            "uart_map": {k: asdict(v) for k, v in DIRECTIONS.items()},
            "pedestal_values": PEDESTAL_VALUES,
        }
    )
    log.event(
        "SESSION_START",
        "Crawler session created",
        console=True,
        script_version=SCRIPT_VERSION,
        run_config=run_config,
        log_dir=str(log.session_dir),
    )

    state = CrawlerState(
        root_chip=args.root_chip,
        accepted_edges=[],
        pending=None,
        hardware_dirty=True,
    )

    hw_options = HardwareOptions(
        io_group=args.io_group,
        io_channel=args.io_channel,
        pacman_config=pacman_config,
        pacman_tile=args.pacman_tile,
        use_crs_pacman_helpers=not args.no_crs_pacman_helpers,
        uart_clock_ratio=args.uart_clock_ratio,
        root_posi=args.root_posi,
        root_downstream_piso=args.root_downstream_piso,
        config_write_repeats=args.config_write_repeats,
        write_delay_s=args.write_delay,
        reset_length=args.reset_length,
        reset_pulses=args.reset_pulses,
        reset_gap_s=args.reset_gap,
    )

    hardware: HardwareBase
    if args.dry_run:
        hardware = DryRunHardware(hw_options, log)
    else:
        hardware = LarpixHardware(hw_options, log)

    try:
        hardware.connect()
    except Exception as exc:
        log.event(
            "SESSION_ABORT_CONNECT",
            str(exc),
            console=True,
            level="ERROR",
        )
        print(f"\nLogs were still created at: {log.session_dir}")
        return 2

    shell = CrawlerShell(
        state,
        hardware,
        log,
        run_config,
        assume_yes=args.yes,
    )

    print()
    print("=" * 78)
    print("CRAWLER TARGET")
    print(f"  io_group              : {args.io_group}")
    print(f"  io_channel            : {args.io_channel}")
    print(f"  root chip             : {args.root_chip}")
    print(f"  PACMAN config         : {pacman_config}")
    print(f"  PACMAN tile helper    : {args.pacman_tile}")
    print(f"  blind write repeats   : {args.config_write_repeats}")
    print(f"  root POSI from PACMAN : POSI{args.root_posi}")
    print(f"  root return PISO      : PISO{args.root_downstream_piso}")
    print(f"  dry run               : {args.dry_run}")
    print(f"  logs                  : {log.session_dir}")
    print("=" * 78)
    print()

    if not args.no_auto_start:
        if args.dry_run or args.yes:
            start = True
        else:
            start = shell._confirm(
                f"RESET io_group {args.io_group} and bring up root chip "
                f"{args.root_chip} in pedestal mode?",
                default=False,
            )

        if start:
            log.event(
                "AUTO_START",
                "Initial root-only reset/rebuild",
                console=True,
            )
            shell._run_rebuild(include_pending=False)
        else:
            log.event(
                "AUTO_START_DECLINED",
                "Shell started without touching ASIC state",
                console=True,
            )
            print("No ASIC reset/configuration has been issued. Use 'start' when ready.")

    try:
        shell.cmdloop()
    except KeyboardInterrupt:
        print("\nCtrl-C at shell prompt; exiting without changing hardware.")
        log.event(
            "SHELL_KEYBOARD_INTERRUPT",
            "Exited at command prompt; hardware left unchanged",
            console=True,
            level="WARNING",
        )

    log.event(
        "SESSION_END",
        "Crawler process ending",
        topology=state.as_json(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
