#!/usr/bin/env python3
"""
Control one PACMAN tile's power-enable bit and its four UART RX channels.

Safety properties:
  * Uses physical tile numbering: Tile 1..8.
  * Power changes use PACMAN_IO.enable_tile()/disable_tile(), which perform
    a read-modify-write of the tile-enable register and preserve every
    other tile/control bit.
  * Receiver changes use a read-modify-write of PACMAN register 0x18 and
    touch only the selected tile's four UART RX bits.
  * A power cycle temporarily mutes the selected tile's receivers, then
    restores/forces them to the requested final state.
  * Does NOT touch global power register 0x14, DAC settings, MCLK, or reset.
  * Default mode is read-only. Pass --apply to actually write hardware.

Example for the current case:
    python tile_power_receiver_control.py \
        --io-group 6 --tile 5 \
        --power cycle --receivers enable \
        --off-time 0.5 --post-on-wait 0.5 \
        --apply
"""

import argparse
import os
import time

import larpix
import larpix.io


RX_ENABLE_REG = 0x18
GLOBAL_POWER_REG = 0x14


def validate_tile(tile: int) -> None:
    if not 1 <= tile <= 8:
        raise ValueError(f"Physical tile must be 1..8, got {tile}")


def pacman_config_from_io_group(io_group: int) -> str:
    return os.path.join("io", f"pacman_io{io_group}.json")


def tile_to_index(tile: int) -> int:
    """Physical tile 1..8 -> PACMAN zero-based tile index 0..7."""
    validate_tile(tile)
    return tile - 1


def tile_to_uart_channels(tile: int) -> list[int]:
    """Physical tile 1..8 -> four 1-based PACMAN UART channels."""
    validate_tile(tile)
    start = (tile - 1) * 4 + 1
    return list(range(start, start + 4))


def tile_uart_mask(tile: int) -> int:
    mask = 0
    for ch in tile_to_uart_channels(tile):
        mask |= 1 << (ch - 1)
    return mask


def fmt32(value: int) -> str:
    return f"0x{value:08x}"


def get_state(io, io_group: int):
    ctrl = io.get_reg(io._base_ctrl_reg, io_group=io_group)
    rx = io.get_reg(RX_ENABLE_REG, io_group=io_group)
    global_power = io.get_reg(GLOBAL_POWER_REG, io_group=io_group)
    return ctrl, rx, global_power


def print_state(label: str, io, io_group: int, tile: int) -> tuple[int, int, int]:
    ctrl, rx, global_power = get_state(io, io_group)
    idx = tile_to_index(tile)
    rxmask = tile_uart_mask(tile)
    channels = tile_to_uart_channels(tile)

    print(f"\n--- {label} ---")
    print(f"IO group              : {io_group}")
    print(f"Physical tile         : {tile}")
    print(f"PACMAN tile index     : {idx}")
    print(f"UART channels         : {channels}")
    print(f"Tile UART mask        : {fmt32(rxmask)}")
    print(f"Tile/control reg      : {fmt32(ctrl)}")
    print(f"  selected tile power : {'ON' if ctrl & (1 << idx) else 'OFF'}")
    print(f"UART RX reg 0x18      : {fmt32(rx)}")
    print(
        f"  selected tile RX    : "
        f"{'ALL ON' if (rx & rxmask) == rxmask else 'ALL OFF' if (rx & rxmask) == 0 else 'MIXED'}"
    )
    print(f"Global power reg 0x14 : {fmt32(global_power)}")
    return ctrl, rx, global_power


def requested_rx_value(rx_before: int, tile: int, receivers: str) -> int:
    mask = tile_uart_mask(tile)
    if receivers == "keep":
        return rx_before
    if receivers == "disable":
        return rx_before & ~mask
    if receivers == "enable":
        return rx_before | mask
    raise ValueError(receivers)


def set_rx(io, io_group: int, value: int, apply: bool, reason: str) -> None:
    current = io.get_reg(RX_ENABLE_REG, io_group=io_group)
    if current == value:
        print(f"{reason}: UART RX already {fmt32(value)}; no write needed")
        return

    print(f"{reason}: UART RX {fmt32(current)} -> {fmt32(value)}")
    if apply:
        io.set_reg(RX_ENABLE_REG, value, io_group=io_group)
        readback = io.get_reg(RX_ENABLE_REG, io_group=io_group)
        if readback != value:
            raise RuntimeError(
                f"UART RX readback mismatch: wrote {fmt32(value)}, "
                f"read {fmt32(readback)}"
            )


def set_tile_power(io, io_group: int, tile: int, on: bool, apply: bool) -> None:
    idx = tile_to_index(tile)
    ctrl_before = io.get_reg(io._base_ctrl_reg, io_group=io_group)
    bit = 1 << idx
    currently_on = bool(ctrl_before & bit)

    if currently_on == on:
        print(f"Tile {tile} power already {'ON' if on else 'OFF'}; no write needed")
        return

    print(
        f"Tile {tile} power {'ON' if currently_on else 'OFF'}"
        f" -> {'ON' if on else 'OFF'}"
    )

    if not apply:
        return

    if on:
        io.enable_tile(tile_indices=idx, io_group=io_group)
    else:
        io.disable_tile(tile_indices=idx, io_group=io_group)

    ctrl_after = io.get_reg(io._base_ctrl_reg, io_group=io_group)

    # No bit other than this tile's enable bit may change.
    if (ctrl_after & ~bit) != (ctrl_before & ~bit):
        raise RuntimeError(
            "Unexpected change outside selected tile bit in tile/control register: "
            f"{fmt32(ctrl_before)} -> {fmt32(ctrl_after)}"
        )

    if bool(ctrl_after & bit) != on:
        raise RuntimeError(
            f"Tile {tile} power readback did not become {'ON' if on else 'OFF'}"
        )


def verify_final_state(
    io,
    io_group: int,
    tile: int,
    ctrl_before: int,
    rx_before: int,
    global_before: int,
    power: str,
    receivers: str,
) -> None:
    ctrl_after, rx_after, global_after = get_state(io, io_group)
    idx = tile_to_index(tile)
    tile_bit = 1 << idx
    rxmask = tile_uart_mask(tile)

    # Global power must never change.
    if global_after != global_before:
        raise RuntimeError(
            f"GLOBAL POWER REGISTER CHANGED: "
            f"{fmt32(global_before)} -> {fmt32(global_after)}"
        )

    # Every tile/control bit except the selected tile's bit must be identical.
    if (ctrl_after & ~tile_bit) != (ctrl_before & ~tile_bit):
        raise RuntimeError(
            "Unexpected tile/control change outside selected tile: "
            f"{fmt32(ctrl_before)} -> {fmt32(ctrl_after)}"
        )

    # Check final selected-tile power state.
    if power in ("none", "cycle"):
        expected_power_on = bool(ctrl_before & tile_bit)
    elif power == "on":
        expected_power_on = True
    elif power == "off":
        expected_power_on = False
    else:
        raise ValueError(power)

    if bool(ctrl_after & tile_bit) != expected_power_on:
        raise RuntimeError(
            f"Unexpected final power state for tile {tile}: "
            f"{'ON' if ctrl_after & tile_bit else 'OFF'}"
        )

    # Receiver bits belonging to every other tile must be untouched.
    if (rx_after & ~rxmask) != (rx_before & ~rxmask):
        raise RuntimeError(
            "Unexpected UART RX change outside selected tile: "
            f"{fmt32(rx_before)} -> {fmt32(rx_after)}"
        )

    expected_rx = requested_rx_value(rx_before, tile, receivers)
    if rx_after != expected_rx:
        raise RuntimeError(
            f"Unexpected final UART RX state: expected {fmt32(expected_rx)}, "
            f"got {fmt32(rx_after)}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Safely control one PACMAN tile's power and UART receivers"
    )
    parser.add_argument(
        "--io-group",
        type=int,
        required=True,
        help="PACMAN IO group, e.g. 6",
    )
    parser.add_argument(
        "--tile",
        type=int,
        required=True,
        help="PHYSICAL tile number 1..8. Tile 5 is written as --tile 5.",
    )
    parser.add_argument(
        "--power",
        choices=("none", "off", "on", "cycle"),
        default="none",
        help="Power action for this tile only",
    )
    parser.add_argument(
        "--receivers",
        choices=("keep", "disable", "enable"),
        default="keep",
        help=(
            "Final state of this tile's four PACMAN UART RX channels. "
            "'keep' restores/preserves their original state."
        ),
    )
    parser.add_argument(
        "--off-time",
        type=float,
        default=0.5,
        help="Seconds to keep the tile disabled during --power cycle (default: 0.5)",
    )
    parser.add_argument(
        "--post-on-wait",
        type=float,
        default=0.5,
        help=(
            "Seconds to wait after enabling the tile before restoring/enabling RX "
            "(default: 0.5)"
        ),
    )
    parser.add_argument(
        "--pacman-config",
        default=None,
        help="Override PACMAN config path; default io/pacman_io<IOGROUP>.json",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write hardware. Without this flag the script is read-only.",
    )
    args = parser.parse_args()

    validate_tile(args.tile)

    if args.off_time < 0 or args.post_on_wait < 0:
        raise ValueError("Timing values must be >= 0")

    pacman_config = args.pacman_config or pacman_config_from_io_group(args.io_group)
    if not os.path.exists(pacman_config):
        raise FileNotFoundError(f"PACMAN config not found: {pacman_config}")

    print(f"Using PACMAN config: {pacman_config}")
    print(f"Mode: {'APPLY WRITES' if args.apply else 'READ-ONLY / DRY RUN'}")
    print(f"Requested power action    : {args.power}")
    print(f"Requested receiver state  : {args.receivers}")

    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(
        relaxed=True,
        config_filepath=pacman_config,
    )

    ctrl_before, rx_before, global_before = print_state(
        "INITIAL STATE", c.io, args.io_group, args.tile
    )

    idx = tile_to_index(args.tile)
    tile_bit = 1 << idx
    rxmask = tile_uart_mask(args.tile)
    final_rx = requested_rx_value(rx_before, args.tile, args.receivers)

    if not args.apply:
        print("\nNo writes will be issued. Planned operation:")
        if args.power == "cycle":
            if not (ctrl_before & tile_bit):
                raise RuntimeError(
                    f"Tile {args.tile} is already OFF. Refusing to call that a power cycle; "
                    "use --power on explicitly if you want to turn it on."
                )
            print(f"  1. Temporarily DISABLE Tile {args.tile} RX channels "
                  f"{tile_to_uart_channels(args.tile)}")
            print(f"  2. Power OFF physical Tile {args.tile} only")
            print(f"  3. Hold OFF for {args.off_time:.3f} s")
            print(f"  4. Power ON physical Tile {args.tile} only")
            print(f"  5. Wait {args.post_on_wait:.3f} s")
            print(f"  6. Set Tile {args.tile} receivers to final state: {args.receivers}")
        elif args.power == "off":
            if args.receivers == "disable":
                print(f"  1. DISABLE Tile {args.tile} RX channels "
                      f"{tile_to_uart_channels(args.tile)}")
                print(f"  2. Power OFF physical Tile {args.tile} only")
            else:
                print(f"  1. Power OFF physical Tile {args.tile} only")
                print(f"  2. Set Tile {args.tile} receivers to final state: {args.receivers}")
        elif args.power == "on":
            print(f"  1. Power ON physical Tile {args.tile} only")
            print(f"  2. Wait {args.post_on_wait:.3f} s")
            print(f"  3. Set Tile {args.tile} receivers to final state: {args.receivers}")
        else:
            print(f"  1. Leave Tile {args.tile} power unchanged")
            print(f"  2. Set Tile {args.tile} receivers to final state: {args.receivers}")

        predicted_ctrl = ctrl_before
        if args.power == "off":
            predicted_ctrl &= ~tile_bit
        elif args.power == "on":
            predicted_ctrl |= tile_bit
        # cycle and none end with the original control register

        print("\nPredicted final state:")
        print(f"  Tile/control reg : {fmt32(predicted_ctrl)}")
        print(f"  UART RX reg      : {fmt32(final_rx)}")
        print(f"  Global power 0x14: {fmt32(global_before)} (UNCHANGED)")
        print("\nDry run complete. Re-run with --apply to perform these writes.")
        return

    if args.power == "cycle":
        if not (ctrl_before & tile_bit):
            raise RuntimeError(
                f"Tile {args.tile} is already OFF. Refusing to call that a power cycle; "
                "use --power on explicitly if you want to turn it on."
            )

        # Always mute this tile's receivers during the power transition.
        muted_rx = rx_before & ~rxmask
        set_rx(
            c.io,
            args.io_group,
            muted_rx,
            args.apply,
            reason=f"Temporarily muting Tile {args.tile} RX before power-off",
        )

        set_tile_power(
            c.io, args.io_group, args.tile, on=False, apply=args.apply
        )

        if args.apply and args.off_time:
            time.sleep(args.off_time)
        else:
            print(f"Would hold power OFF for {args.off_time:.3f} s")

        set_tile_power(
            c.io, args.io_group, args.tile, on=True, apply=args.apply
        )

        if args.apply and args.post_on_wait:
            time.sleep(args.post_on_wait)
        else:
            print(f"Would wait {args.post_on_wait:.3f} s after power-on")

        set_rx(
            c.io,
            args.io_group,
            final_rx,
            args.apply,
            reason=f"Setting Tile {args.tile} RX final state ({args.receivers})",
        )

    elif args.power == "off":
        # If final state is disabled, mute before removing power.
        if args.receivers == "disable":
            set_rx(
                c.io,
                args.io_group,
                final_rx,
                args.apply,
                reason=f"Disabling Tile {args.tile} RX before power-off",
            )

        set_tile_power(
            c.io, args.io_group, args.tile, on=False, apply=args.apply
        )

        # Handle keep/enable exactly as requested after power state is set.
        if args.receivers != "disable":
            set_rx(
                c.io,
                args.io_group,
                final_rx,
                args.apply,
                reason=f"Setting Tile {args.tile} RX final state ({args.receivers})",
            )

    elif args.power == "on":
        set_tile_power(
            c.io, args.io_group, args.tile, on=True, apply=args.apply
        )

        if args.apply and args.post_on_wait:
            time.sleep(args.post_on_wait)
        elif args.post_on_wait:
            print(f"Would wait {args.post_on_wait:.3f} s after power-on")

        set_rx(
            c.io,
            args.io_group,
            final_rx,
            args.apply,
            reason=f"Setting Tile {args.tile} RX final state ({args.receivers})",
        )

    elif args.power == "none":
        set_rx(
            c.io,
            args.io_group,
            final_rx,
            args.apply,
            reason=f"Setting Tile {args.tile} RX final state ({args.receivers})",
        )

    verify_final_state(
        c.io,
        args.io_group,
        args.tile,
        ctrl_before,
        rx_before,
        global_before,
        args.power,
        args.receivers,
    )
    print_state("FINAL STATE", c.io, args.io_group, args.tile)
    print("\nSUCCESS: requested operation completed and safety checks passed.")


if __name__ == "__main__":
    main()
