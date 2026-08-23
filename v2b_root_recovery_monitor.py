#!/usr/bin/env python3
"""
v2b_root_recovery_monitor.py

Monitor recovery of Module-2 LArPix-v2b ROOT chips after an in-cold power loss.

Scope
-----
* io_group 5 and 6 only.
* root ASICs only: io_channel 1..32, roots [21, 41, 71, 91] repeated per tile.
* io_group 6 / tile 5 (io_channels 17..20) is skipped by default.
* No Hydra daughters are ever enabled.
* Pixel channels stay masked and CSA/periodic activity is suppressed.
* Every hardware trial is timestamped in JSONL and CSV.

The monitor intentionally cycles through several recovery regimes:
    1. fast_poke      : no reset, short read/write/read attempt
    2. double_reset   : standard v2b-style pair of 16384-cycle global resets
    3. reset_burst    : short high-frequency reset burst
    4. slow_poke      : no reset, slower/more patient full verification

A PACMAN LArPix reset is global to one io_group, so reset regimes are applied once
per io_group and then all roots on that io_group are scanned.  This avoids
resetting the other 31 roots before every single probe while still testing
whether reset activity helps unlock the ASICs.

Receiver modes
--------------
all-on (default):
    Enable all 32 PACMAN RX channels for an io_group for the duration of a pass.

per-channel:
    Enable only the root's PACMAN RX channel during that root's trial, then
    disable RX again before moving on.  This is the packet-storm containment mode.

The PACMAN RX register is restored to its pre-script value on clean exit.

Important
---------
This script uses only reset/configuration/readback operations that are already
present in crs_daq / larpix-control.  It does NOT invent a separate chip-local
"state-machine reset" command: no such public command was identified in the
current larpix-control API.  The no-reset poke passes exercise the current ASIC
state; the reset passes exercise the verified PACMAN reset_larpix path.

Memory containment
------------------
The DAQ server uses an older larpix-control revision where Controller.reads and
PACMAN_IO._sender_replies grow indefinitely.  This monitor explicitly clears
those histories after their contents have been reduced to logged metrics, and it
prints current process RSS at the end of every recovery cycle.
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import signal
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import larpix
import larpix.io

from base import pacman_base


SCRIPT_VERSION = "2026-08-17-root-recovery-v2-memoryfix"
ROOT_IDS = (21, 41, 71, 91)
IO_GROUPS = (5, 6)
N_TILES = 8
RX_ENABLE_REG = 0x18
UART_CLOCK_RATIO = 10
ROOT_POSI = 1
ROOT_DOWNSTREAM_PISO = 0

# Standard hydra_v2b.py uses 4096*4 twice.
STANDARD_RESET_LENGTH = 4096 * 4
STANDARD_RESET_PULSES = 2

# power-up code in crs_daq uses a 64-cycle reset; use that established short
# pulse length for the high-frequency burst rather than inventing a new value.
BURST_RESET_LENGTH = 64
BURST_RESET_PULSES = 8
BURST_RESET_GAP_S = 0.001

CSV_FIELDS = [
    "timestamp_utc",
    "timestamp_local",
    "elapsed_s",
    "cycle",
    "phase",
    "io_group",
    "tile",
    "io_channel",
    "chip_id",
    "step",
    "status",
    "any_reply",
    "all_match",
    "total_registers",
    "matched_registers",
    "wrong_registers",
    "missing_registers",
    "timeout_s",
    "n_verify",
    "duration_s",
    "details",
]


@dataclass(frozen=True)
class Target:
    io_group: int
    tile: int
    io_channel: int
    chip_id: int

    @property
    def key(self) -> str:
        return f"{self.io_group}-{self.io_channel}-{self.chip_id}"


@dataclass(frozen=True)
class Phase:
    name: str
    timeout_s: float
    connection_delay_s: float
    n_verify: int
    reset_kind: str = "none"  # none | double | burst


PHASES: Tuple[Phase, ...] = (
    Phase("fast_poke", timeout_s=0.010, connection_delay_s=0.003, n_verify=1),
    Phase(
        "double_reset",
        timeout_s=0.015,
        connection_delay_s=0.005,
        n_verify=1,
        reset_kind="double",
    ),
    Phase(
        "reset_burst",
        timeout_s=0.015,
        connection_delay_s=0.003,
        n_verify=1,
        reset_kind="burst",
    ),
    Phase("slow_poke", timeout_s=0.080, connection_delay_s=0.020, n_verify=3),
)


class StopRequested(Exception):
    pass


class SessionLogger:
    def __init__(self, base_dir: Path):
        now = datetime.now().astimezone()
        session_name = now.strftime("root_recovery_%Y%m%d_%H%M%S_%Z")
        self.session_dir = base_dir / session_name
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self.jsonl_path = self.session_dir / "events.jsonl"
        self.csv_path = self.session_dir / "trials.csv"
        self.summary_path = self.session_dir / "latest_summary.json"
        self._t0_ns = time.monotonic_ns()
        self._seq = 0

        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()

    def event(self, event: str, message: str = "", **data: Any) -> Dict[str, Any]:
        self._seq += 1
        mono_ns = time.monotonic_ns()
        record = {
            "seq": self._seq,
            "event": event,
            "message": message,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "timestamp_local": datetime.now().astimezone().isoformat(timespec="microseconds"),
            "epoch_ns": time.time_ns(),
            "monotonic_ns": mono_ns,
            "elapsed_s": (mono_ns - self._t0_ns) / 1e9,
            **data,
        }
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        return record

    def trial_row(self, **data: Any) -> None:
        now_utc = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        now_local = datetime.now().astimezone().isoformat(timespec="microseconds")
        row = {field: "" for field in CSV_FIELDS}
        row.update(
            timestamp_utc=now_utc,
            timestamp_local=now_local,
            elapsed_s=(time.monotonic_ns() - self._t0_ns) / 1e9,
        )
        row.update(data)
        with self.csv_path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)

    def write_summary(self, payload: Dict[str, Any]) -> None:
        tmp = self.summary_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        tmp.replace(self.summary_path)


class IOGHardware:
    def __init__(self, io_group: int, repo_root: Path, log: SessionLogger):
        self.io_group = io_group
        self.repo_root = repo_root
        self.log = log
        self.io = None
        self.controller = None
        self.initial_rx_reg: Optional[int] = None

    def connect(self) -> None:
        config = self.repo_root / "io" / f"pacman_io{self.io_group}.json"
        if not config.exists():
            raise FileNotFoundError(f"PACMAN config not found: {config}")

        self.io = larpix.io.PACMAN_IO(relaxed=True, config_filepath=str(config))
        self.controller = larpix.Controller()
        self.controller.io = self.io

        try:
            self.initial_rx_reg = int(self.io.get_reg(RX_ENABLE_REG, io_group=self.io_group))
        except Exception as exc:
            self.initial_rx_reg = None
            self.log.event(
                "RX_SNAPSHOT_ERROR",
                io_group=self.io_group,
                error=f"{exc.__class__.__name__}: {exc}",
            )

        # Match the existing 2x2 v2b setup: inversion on all 8 tiles and ratio 10.
        pacman_base.invert_pacman_uart(
            self.io, self.io_group, "2b", list(range(1, N_TILES + 1))
        )
        for io_channel in range(1, 33):
            self.io.set_uart_clock_ratio(
                io_channel, UART_CLOCK_RATIO, io_group=self.io_group
            )

        for target in targets_for_iog(self.io_group, include_problem_tile=True):
            self.controller.add_chip(target.key, version="2b")
            prepare_safe_monitor_configuration(self.controller[target.key], target.chip_id)

        self.log.event(
            "IOG_CONNECTED",
            io_group=self.io_group,
            pacman_config=str(config),
            initial_rx_reg=self.initial_rx_reg,
            uart_clock_ratio=UART_CLOCK_RATIO,
        )

    def restore_receivers(self) -> None:
        if self.io is None or self.initial_rx_reg is None:
            return
        try:
            self.io.set_reg(RX_ENABLE_REG, self.initial_rx_reg, io_group=self.io_group)
            self.log.event(
                "RX_RESTORED", io_group=self.io_group, value=self.initial_rx_reg
            )
        except Exception as exc:
            self.log.event(
                "RX_RESTORE_ERROR",
                io_group=self.io_group,
                error=f"{exc.__class__.__name__}: {exc}",
            )

    def set_all_receivers(self, enabled: bool) -> None:
        # Literal all-32 helper. Used for disabling (value=0) and retained for
        # explicit full-IOG operations. Normal all-on monitoring uses the target
        # mask below so a skipped/problem tile stays physically RX-disabled.
        value = 0xFFFFFFFF if enabled else 0
        self.io.set_reg(RX_ENABLE_REG, value, io_group=self.io_group)
        self.log.event(
            "RX_SET_ALL", io_group=self.io_group, enabled=enabled, value=value
        )

    def set_receivers_for_targets(self, targets: Sequence[Target]) -> None:
        value = 0
        channels = []
        for target in targets:
            if target.io_group != self.io_group:
                continue
            value |= 1 << (target.io_channel - 1)
            channels.append(target.io_channel)
        self.io.set_reg(RX_ENABLE_REG, value, io_group=self.io_group)
        self.log.event(
            "RX_SET_TARGET_MASK",
            io_group=self.io_group,
            io_channels=channels,
            value=value,
        )

    def set_one_receiver(self, io_channel: int) -> None:
        value = 1 << (io_channel - 1)
        self.io.set_reg(RX_ENABLE_REG, value, io_group=self.io_group)
        self.log.event(
            "RX_SET_ONE",
            io_group=self.io_group,
            io_channel=io_channel,
            value=value,
        )

    def reset_for_phase(self, phase: Phase, cycle: int) -> None:
        if phase.reset_kind == "none":
            return

        if phase.reset_kind == "double":
            pulses = STANDARD_RESET_PULSES
            length = STANDARD_RESET_LENGTH
            gap_s = max(length * 1e-6, 0.001)
        elif phase.reset_kind == "burst":
            pulses = BURST_RESET_PULSES
            length = BURST_RESET_LENGTH
            gap_s = BURST_RESET_GAP_S
        else:
            raise ValueError(phase.reset_kind)

        self.log.event(
            "RESET_SERIES_BEGIN",
            cycle=cycle,
            phase=phase.name,
            io_group=self.io_group,
            reset_kind=phase.reset_kind,
            pulses=pulses,
            length=length,
            gap_s=gap_s,
        )
        for pulse in range(1, pulses + 1):
            t0 = time.monotonic()
            self.io.reset_larpix(length=length, io_group=self.io_group)
            time.sleep(gap_s)
            self.log.event(
                "RESET_PULSE",
                cycle=cycle,
                phase=phase.name,
                io_group=self.io_group,
                pulse=pulse,
                pulses=pulses,
                length=length,
                duration_s=time.monotonic() - t0,
            )
        self.log.event(
            "RESET_SERIES_END",
            cycle=cycle,
            phase=phase.name,
            io_group=self.io_group,
        )

    def claim_root_id(self, target: Target) -> None:
        """Blindly assign desired root ID to a reset-default chip at hardware ID 1."""
        chip = self.controller[target.key]
        chip.config.chip_id = target.chip_id
        regs = list(chip.config.register_map["chip_id"])
        packets = chip.get_configuration_write_packets(registers=regs)
        for packet in packets:
            packet.chip_id = 1
            packet.assign_parity()
        self.controller.send(packets)

    def bootstrap_root_return(self, target: Target) -> None:
        """Blindly establish only the direct root command/return path."""
        chip = self.controller[target.key]
        cfg = chip.config
        cfg.chip_id = target.chip_id
        cfg.enable_posi = [0, 1, 0, 0]
        cfg.enable_piso_upstream = [0, 0, 0, 0]
        cfg.enable_piso_downstream = [1, 0, 0, 0]
        cfg.channel_mask = [1] * 64

        names = (
            "enable_posi",
            "enable_piso_upstream",
            "enable_piso_downstream",
            "channel_mask",
        )
        addresses = ordered_register_addresses(cfg, names)
        self.controller.write_configuration(
            target.key, registers=addresses, write_read=0, connection_delay=0
        )

    def verify(
        self,
        target: Target,
        *,
        timeout_s: float,
        connection_delay_s: float,
        n_verify: int,
    ) -> Dict[str, Any]:
        """Verify a full chip config without retaining larpix read history.

        Older larpix-control revisions append every PacketCollection to
        Controller.reads and never clear it.  This monitor can perform hundreds
        of full-register reads per cycle, so leaving that history intact causes
        unbounded memory growth.  The diff returned by verify_configuration is
        self-contained, so the raw PacketCollections are no longer needed once
        summarize_diff has reduced the result to our metrics.
        """
        t0 = time.monotonic()
        try:
            ok, diff = self.controller.verify_configuration(
                target.key,
                timeout=timeout_s,
                connection_delay=connection_delay_s,
                n=n_verify,
            )
            duration = time.monotonic() - t0
            return summarize_diff(
                self.controller[target.key], ok, diff, duration_s=duration
            )
        finally:
            # Critical memory-leak containment for the older larpix-control
            # version used on the DAQ server.  Safe here because
            # verify_configuration has already consumed self.reads[-1] and
            # returned its diff before this finally block runs.
            self.controller.reads.clear()

    def clear_software_history(self) -> Dict[str, int]:
        """Drop larpix/PACMAN histories that otherwise grow without bound."""
        controller_reads = len(self.controller.reads) if self.controller else 0
        if self.controller is not None:
            self.controller.reads.clear()

        sender_replies = 0
        if self.io is not None and hasattr(self.io, "_sender_replies"):
            try:
                sender_replies = sum(len(v) for v in self.io._sender_replies.values())
                self.io._sender_replies.clear()
            except Exception:
                # History cleanup is best-effort and must never interfere with
                # the hardware recovery loop.
                pass

        return {
            "controller_reads_dropped": controller_reads,
            "sender_replies_dropped": sender_replies,
        }

    def write_full_config(self, target: Target) -> float:
        t0 = time.monotonic()
        self.controller.write_configuration(
            target.key, registers=None, write_read=0, connection_delay=0
        )
        return time.monotonic() - t0


def targets_for_iog(io_group: int, include_problem_tile: bool) -> List[Target]:
    result: List[Target] = []
    for tile in range(1, N_TILES + 1):
        if io_group == 6 and tile == 5 and not include_problem_tile:
            continue
        for slot, chip_id in enumerate(ROOT_IDS):
            io_channel = (tile - 1) * 4 + slot + 1
            result.append(Target(io_group, tile, io_channel, chip_id))
    return result


def ordered_register_addresses(config: Any, names: Sequence[str]) -> List[int]:
    addresses: List[int] = []
    seen = set()
    for name in names:
        if name not in config.register_map:
            continue
        for address in config.register_map[name]:
            if address not in seen:
                seen.add(address)
                addresses.append(address)
    return addresses


def set_if_present(config: Any, name: str, value: Any) -> None:
    if name in getattr(config, "register_map", {}):
        setattr(config, name, list(value) if isinstance(value, list) else value)


def prepare_safe_monitor_configuration(chip: Any, chip_id: int) -> None:
    """
    Build a deterministic, quiet v2b configuration for write/readback testing.

    This deliberately keeps all pixels masked and all upstream PISOs disabled.
    PISO0 is the root return to PACMAN; POSI1 is the PACMAN command input.
    """
    cfg = chip.config

    # Known-good electrical values used by the crawler / CRS v2b bring-up work.
    set_if_present(cfg, "ref_current_trim", 0)
    for idx in range(4):
        set_if_present(cfg, f"tx_slices{idx}", 15)
        set_if_present(cfg, f"i_tx_diff{idx}", 0)
        set_if_present(cfg, f"r_term{idx}", 8)
        set_if_present(cfg, f"i_rx{idx}", 8)

    set_if_present(cfg, "threshold_global", 255)
    set_if_present(cfg, "vcm_dac", 50)
    set_if_present(cfg, "vref_dac", 185)

    # Keep the test electrically quiet: no hit/periodic data production.
    set_if_present(cfg, "channel_mask", [1] * 64)
    set_if_present(cfg, "csa_enable", [0] * 64)
    set_if_present(cfg, "periodic_trigger_mask", [1] * 64)
    set_if_present(cfg, "enable_periodic_trigger", 0)
    set_if_present(cfg, "enable_rolling_periodic_trigger", 0)
    set_if_present(cfg, "enable_periodic_reset", 0)
    set_if_present(cfg, "enable_rolling_periodic_reset", 0)
    set_if_present(cfg, "enable_hit_veto", 0)
    set_if_present(cfg, "enable_periodic_trigger_veto", 1)

    # Root-only topology. Never expose a daughter.
    set_if_present(cfg, "enable_piso_upstream", [0, 0, 0, 0])
    set_if_present(cfg, "enable_posi", [0, 1, 0, 0])
    set_if_present(cfg, "enable_piso_downstream", [1, 0, 0, 0])
    set_if_present(cfg, "chip_id", chip_id)


def summarize_diff(chip: Any, ok: bool, diff: Dict[Any, Any], duration_s: float) -> Dict[str, Any]:
    total = int(chip.config.num_registers)
    key = chip.chip_key
    registers = (diff or {}).get(key, {})

    missing = 0
    wrong = 0
    mismatch_details = []
    for register, values in registers.items():
        expected = None
        actual = None
        if isinstance(values, (list, tuple)) and len(values) >= 2:
            expected, actual = values[0], values[1]
        else:
            actual = values
        if actual is None:
            missing += 1
        else:
            wrong += 1
        if len(mismatch_details) < 12:
            mismatch_details.append(
                {"register": register, "expected": expected, "actual": actual}
            )

    n_bad = len(registers)
    matched = max(0, total - n_bad)
    any_reply = missing < total

    return {
        "all_match": bool(ok),
        "any_reply": bool(any_reply),
        "total_registers": total,
        "matched_registers": matched,
        "wrong_registers": wrong,
        "missing_registers": missing,
        "mismatch_details": mismatch_details,
        "duration_s": duration_s,
    }


def compact_result(result: Dict[str, Any]) -> str:
    if result.get("all_match"):
        return f"OK {result['matched_registers']}/{result['total_registers']}"
    if not result.get("any_reply"):
        return f"NO REPLY 0/{result['total_registers']}"
    return (
        f"PARTIAL {result['matched_registers']}/{result['total_registers']} "
        f"wrong={result['wrong_registers']} missing={result['missing_registers']}"
    )


def record_step(
    log: SessionLogger,
    *,
    cycle: int,
    phase: Phase,
    target: Target,
    step: str,
    result: Optional[Dict[str, Any]] = None,
    status: str = "OK",
    duration_s: Optional[float] = None,
    details: Any = "",
) -> None:
    payload = {
        "cycle": cycle,
        "phase": phase.name,
        **asdict(target),
        "step": step,
        "status": status,
    }
    if result:
        payload.update(result)
    if duration_s is not None:
        payload["duration_s"] = duration_s
    if details:
        payload["details"] = details
    log.event("TRIAL_STEP", **payload)

    log.trial_row(
        cycle=cycle,
        phase=phase.name,
        io_group=target.io_group,
        tile=target.tile,
        io_channel=target.io_channel,
        chip_id=target.chip_id,
        step=step,
        status=status,
        any_reply="" if not result else result.get("any_reply", ""),
        all_match="" if not result else result.get("all_match", ""),
        total_registers="" if not result else result.get("total_registers", ""),
        matched_registers="" if not result else result.get("matched_registers", ""),
        wrong_registers="" if not result else result.get("wrong_registers", ""),
        missing_registers="" if not result else result.get("missing_registers", ""),
        timeout_s=phase.timeout_s if result else "",
        n_verify=phase.n_verify if result else "",
        duration_s=(result or {}).get("duration_s", duration_s if duration_s is not None else ""),
        details=json.dumps(details if details else (result or {}).get("mismatch_details", [])),
    )


def probe_target(
    hw: IOGHardware,
    target: Target,
    phase: Phase,
    cycle: int,
    receiver_mode: str,
    latest: Dict[str, Any],
) -> None:
    if receiver_mode == "per-channel":
        hw.set_one_receiver(target.io_channel)

    hw.log.event("TRIAL_BEGIN", cycle=cycle, phase=phase.name, **asdict(target))
    print(
        f"  IOG{target.io_group} tile{target.tile} ioc{target.io_channel:02d} "
        f"chip{target.chip_id:02d}",
        end="  ",
        flush=True,
    )

    try:
        # 1) READ current state before touching the ASIC. This is our cleanest
        #    measure of whether the root is already talking at this timestamp.
        pre = hw.verify(
            target,
            timeout_s=phase.timeout_s,
            connection_delay_s=phase.connection_delay_s,
            n_verify=phase.n_verify,
        )
        record_step(
            hw.log, cycle=cycle, phase=phase, target=target,
            step="pre_read", result=pre,
        )

        # 2) Always try the reset-default address claim. If the chip already has
        #    its root ID this packet simply does not address it; then the normal
        #    root-ID writes below still reinforce the configuration.
        t0 = time.monotonic()
        hw.claim_root_id(target)
        claim_duration = time.monotonic() - t0
        record_step(
            hw.log, cycle=cycle, phase=phase, target=target,
            step="claim_id", duration_s=claim_duration,
        )

        # 3) Blindly bootstrap only the direct root return path while all channels
        #    remain masked. This is required before a reset-default root can reply.
        t0 = time.monotonic()
        hw.bootstrap_root_return(target)
        bootstrap_duration = time.monotonic() - t0
        record_step(
            hw.log, cycle=cycle, phase=phase, target=target,
            step="bootstrap_return", duration_s=bootstrap_duration,
        )

        # 4) Full configuration write.
        write_duration = hw.write_full_config(target)
        record_step(
            hw.log, cycle=cycle, phase=phase, target=target,
            step="full_write", duration_s=write_duration,
        )

        # 5) Full readback / comparison.
        post = hw.verify(
            target,
            timeout_s=phase.timeout_s,
            connection_delay_s=phase.connection_delay_s,
            n_verify=phase.n_verify,
        )
        record_step(
            hw.log, cycle=cycle, phase=phase, target=target,
            step="post_read", result=post,
        )

        # 6) A second full readback, with no write in between. This is a direct
        #    short-term repeatability measurement once the chip has replied.
        repeat = hw.verify(
            target,
            timeout_s=phase.timeout_s,
            connection_delay_s=phase.connection_delay_s,
            n_verify=phase.n_verify,
        )
        record_step(
            hw.log, cycle=cycle, phase=phase, target=target,
            step="repeat_read", result=repeat,
        )

        state_key = target.key
        old = latest.get(state_key, {})
        latest[state_key] = {
            **asdict(target),
            "last_cycle": cycle,
            "last_phase": phase.name,
            "pre_read": pre,
            "post_read": post,
            "repeat_read": repeat,
            "first_reply_timestamp_utc": old.get("first_reply_timestamp_utc"),
            "first_full_match_timestamp_utc": old.get("first_full_match_timestamp_utc"),
            "successful_full_reads": old.get("successful_full_reads", 0)
            + int(post["all_match"]) + int(repeat["all_match"]),
            "total_full_reads": old.get("total_full_reads", 0) + 2,
        }
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        if latest[state_key]["first_reply_timestamp_utc"] is None and (
            pre["any_reply"] or post["any_reply"] or repeat["any_reply"]
        ):
            latest[state_key]["first_reply_timestamp_utc"] = now
        if latest[state_key]["first_full_match_timestamp_utc"] is None and (
            post["all_match"] or repeat["all_match"]
        ):
            latest[state_key]["first_full_match_timestamp_utc"] = now

        print(
            f"pre={compact_result(pre)}  post={compact_result(post)}  "
            f"repeat={compact_result(repeat)}"
        )

    except KeyboardInterrupt:
        raise
    except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"
        record_step(
            hw.log,
            cycle=cycle,
            phase=phase,
            target=target,
            step="exception",
            status="ERROR",
            details={"error": error, "traceback": traceback.format_exc()},
        )
        print(f"ERROR {error}")
    finally:
        hw.log.event("TRIAL_END", cycle=cycle, phase=phase.name, **asdict(target))
        if receiver_mode == "per-channel":
            try:
                hw.set_all_receivers(False)
            except Exception as exc:
                hw.log.event(
                    "RX_DISABLE_ERROR",
                    cycle=cycle,
                    phase=phase.name,
                    **asdict(target),
                    error=f"{exc.__class__.__name__}: {exc}",
                )

        # PACMAN_IO also keeps every synchronous command-server reply in an
        # unbounded defaultdict(list).  None of those replies are needed after
        # the corresponding synchronous call returns, so prune the history at
        # the end of every root trial.
        dropped = hw.clear_software_history()
        if dropped["controller_reads_dropped"] or dropped["sender_replies_dropped"]:
            hw.log.event(
                "SOFTWARE_HISTORY_CLEARED",
                cycle=cycle,
                phase=phase.name,
                **asdict(target),
                **dropped,
            )


def read_process_memory_kib() -> Dict[str, int]:
    """Read current Linux process-memory counters from /proc/self/status."""
    wanted = {"VmRSS", "VmHWM", "VmSize", "RssAnon", "RssFile", "RssShmem"}
    result: Dict[str, int] = {}
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                key = line.split(":", 1)[0]
                if key not in wanted:
                    continue
                fields = line.split()
                if len(fields) >= 2:
                    result[key] = int(fields[1])
    except OSError:
        pass
    return result


def report_memory(
    log: SessionLogger,
    hardware: Dict[int, IOGHardware],
    *,
    cycle: int,
    phase: Optional[str] = None,
) -> None:
    """Print and log RSS plus the two histories we intentionally bound."""
    memory = read_process_memory_kib()
    controller_reads = {
        str(iog): len(hw.controller.reads) if hw.controller is not None else 0
        for iog, hw in hardware.items()
    }
    sender_replies = {}
    for iog, hw in hardware.items():
        count = 0
        if hw.io is not None and hasattr(hw.io, "_sender_replies"):
            try:
                count = sum(len(v) for v in hw.io._sender_replies.values())
            except Exception:
                count = -1
        sender_replies[str(iog)] = count

    rss_mib = memory.get("VmRSS", 0) / 1024.0
    hwm_mib = memory.get("VmHWM", 0) / 1024.0
    print(
        f"Memory: RSS={rss_mib:.1f} MiB HWM={hwm_mib:.1f} MiB "
        f"controller.reads={controller_reads} sender_replies={sender_replies}"
    )
    log.event(
        "MEMORY_STATUS",
        cycle=cycle,
        phase=phase,
        memory_kib=memory,
        controller_reads=controller_reads,
        sender_replies=sender_replies,
    )


def print_cycle_summary(latest: Dict[str, Any], targets: Sequence[Target]) -> None:
    n = len(targets)
    replied = 0
    matched = 0
    for target in targets:
        state = latest.get(target.key, {})
        post = state.get("post_read", {})
        repeat = state.get("repeat_read", {})
        if post.get("any_reply") or repeat.get("any_reply"):
            replied += 1
        if post.get("all_match") and repeat.get("all_match"):
            matched += 1
    print(f"Cycle status: replying {replied}/{n}; two consecutive full matches {matched}/{n}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Monitor Module-2 v2b root-chip recovery after cold power loss."
    )
    p.add_argument(
        "--receiver-mode",
        choices=("all-on", "per-channel"),
        default="all-on",
        help="PACMAN RX handling (default: all-on)",
    )
    p.add_argument(
        "--include-iog6-tile5",
        action="store_true",
        help="also probe known-problematic IOG6 tile5 (channels 17..20)",
    )
    p.add_argument(
        "--loops",
        type=int,
        default=0,
        help="number of complete cycles; 0 means run until Ctrl-C (default)",
    )
    p.add_argument(
        "--between-cycles",
        type=float,
        default=30.0,
        help="seconds to sleep after each complete cycle (default: 30)",
    )
    p.add_argument(
        "--log-dir",
        type=Path,
        default=Path("root_recovery_logs"),
        help="base log directory (default: root_recovery_logs)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.loops < 0:
        raise SystemExit("--loops must be >= 0")
    if args.between_cycles < 0:
        raise SystemExit("--between-cycles must be >= 0")

    repo_root = Path(__file__).resolve().parent
    # If this file is run from a copied/downloaded location rather than from
    # crs_daq itself, prefer the current working directory when it looks like the repo.
    if not (repo_root / "io" / "pacman_io5.json").exists():
        cwd = Path.cwd().resolve()
        if (cwd / "io" / "pacman_io5.json").exists():
            repo_root = cwd

    log_base = args.log_dir
    if not log_base.is_absolute():
        log_base = repo_root / log_base
    log = SessionLogger(log_base)

    targets = [
        target
        for io_group in IO_GROUPS
        for target in targets_for_iog(io_group, args.include_iog6_tile5)
    ]

    session_info = {
        "script_version": SCRIPT_VERSION,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "repo_root": str(repo_root),
        "receiver_mode": args.receiver_mode,
        "include_iog6_tile5": args.include_iog6_tile5,
        "loops": args.loops,
        "between_cycles_s": args.between_cycles,
        "targets": [asdict(t) for t in targets],
        "phases": [asdict(p) for p in PHASES],
        "root_mapping": {
            "root_ids": list(ROOT_IDS),
            "root_posi": ROOT_POSI,
            "root_downstream_piso": ROOT_DOWNSTREAM_PISO,
            "uart_clock_ratio": UART_CLOCK_RATIO,
        },
    }
    (log.session_dir / "session.json").write_text(
        json.dumps(session_info, indent=2, sort_keys=True), encoding="utf-8"
    )
    log.event("SESSION_START", **session_info)

    print(f"v2b root recovery monitor {SCRIPT_VERSION}")
    print(f"Logs: {log.session_dir}")
    print(f"Targets: {len(targets)} roots")
    if not args.include_iog6_tile5:
        print("IOG6 tile5 / channels 17-20: SKIPPED")
    print(f"PACMAN receivers: {args.receiver_mode}")
    print("Ctrl-C stops after the current Python call and restores PACMAN RX state.\n")

    hardware: Dict[int, IOGHardware] = {}
    latest: Dict[str, Any] = {}

    try:
        for io_group in IO_GROUPS:
            hw = IOGHardware(io_group, repo_root, log)
            hw.connect()
            hardware[io_group] = hw
            if args.receiver_mode == "per-channel":
                hw.set_all_receivers(False)

        cycle = 0
        while args.loops == 0 or cycle < args.loops:
            cycle += 1
            log.event("CYCLE_BEGIN", cycle=cycle)
            print(f"\n========== RECOVERY CYCLE {cycle} ==========")

            for phase in PHASES:
                print(f"\n--- phase: {phase.name} ---")
                log.event("PHASE_BEGIN", cycle=cycle, phase=asdict(phase))

                for io_group in IO_GROUPS:
                    hw = hardware[io_group]
                    group_targets = [t for t in targets if t.io_group == io_group]
                    if not group_targets:
                        continue

                    if args.receiver_mode == "all-on":
                        # Enable every monitored channel, but keep deliberately
                        # skipped channels (notably IOG6 tile5 by default) OFF.
                        hw.set_receivers_for_targets(group_targets)

                    # Reset is global to this io_group, so do it once then scan roots.
                    hw.reset_for_phase(phase, cycle)

                    for target in group_targets:
                        probe_target(
                            hw,
                            target,
                            phase,
                            cycle,
                            args.receiver_mode,
                            latest,
                        )
                        log.write_summary(
                            {
                                "script_version": SCRIPT_VERSION,
                                "updated_utc": datetime.now(timezone.utc).isoformat(
                                    timespec="microseconds"
                                ),
                                "cycle": cycle,
                                "phase": phase.name,
                                "chips": latest,
                            }
                        )

                log.event("PHASE_END", cycle=cycle, phase=phase.name)

            print_cycle_summary(latest, targets)

            # Force collection of any temporary cyclic Python objects, then
            # report current RSS.  The explicit history clears above are the
            # actual leak fix; gc.collect() is only diagnostic housekeeping.
            gc.collect()
            report_memory(log, hardware, cycle=cycle)
            log.event("CYCLE_END", cycle=cycle)

            if args.loops != 0 and cycle >= args.loops:
                break
            if args.between_cycles:
                print(f"Sleeping {args.between_cycles:g} s before next cycle...")
                time.sleep(args.between_cycles)

    except KeyboardInterrupt:
        print("\nStopping on Ctrl-C.")
        log.event("SESSION_INTERRUPTED", reason="KeyboardInterrupt")
    finally:
        for hw in hardware.values():
            hw.restore_receivers()
        log.event("SESSION_END")
        print(f"Logs saved in: {log.session_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
