#!/usr/bin/env python3
"""Configure and power CERN Rev5 PACMAN tiles from RUN_CONFIG.json."""

import argparse
import time

import larpix
import larpix.io

from base.asic_family import control_io_settings
from base import pacman_base, utility_base
from runenv import runenv as RUN

RESET_CYCLES = 4096


def main(verbose=False, pacman_config="io/pacman.json", settle=0.5):
    io_group, asic_family, packet_family = control_io_settings(pacman_config)
    tiles = RUN.io_group_pacman_tile_[io_group]
    vdda = RUN.iog_VDDA_DAC[io_group]
    vddd = RUN.iog_VDDD_DAC[io_group]
    if RUN.iog_pacman_version_[io_group] != "v1rev5":
        raise ValueError("The CERN hardware path supports Rev5 PACMANs only")

    controller = larpix.Controller()
    controller.io = larpix.io.PACMAN_IO(
        relaxed=True, config_filepath=pacman_config,
        asic_version=packet_family,
    )
    io = controller.io
    if verbose:
        print(f"IOG {io_group}: ASIC {asic_family!r}, packet family {packet_family}, tiles {tiles}")

    # Reset/power changes invalidate current hardware state even if a later
    # register operation fails. Saved default selections/files are retained.
    utility_base.update_json(RUN.asic_config_paths_file_, io_group, None)
    utility_base.update_json(RUN.network_config_paths_file_, io_group, None)
    pacman_base.disable_all_pacman_uart(io, io_group)
    io.set_reg(0x10, 0, io_group=io_group)
    io.set_reg(0x14, 0, io_group=io_group)
    io.set_reg(0x101C, 4, io_group=io_group)
    io.set_reg(0x14, 1, io_group=io_group)

    tile_mask = 0
    for tile in tiles:
        offset = tile - 1
        io.set_reg(0x24020 + offset, 0, io_group=io_group)
        io.set_reg(0x24010 + offset, 0, io_group=io_group)
        time.sleep(0.1)
        io.set_reg(0x24020 + offset, vddd[offset], io_group=io_group)
        io.set_reg(0x24010 + offset, vdda[offset], io_group=io_group)
        tile_mask |= 1 << offset

    io.set_reg(0x10, tile_mask, io_group=io_group)
    # Rev5 telemetry does not settle immediately after connecting a tile.
    # Reading it too soon produced tens of mV despite correctly programmed DACs.
    time.sleep(settle)
    io.reset_larpix(length=RESET_CYCLES, io_group=io_group)
    io.set_reg(0x2014, 0xFFFFFFFF, io_group=io_group)
    logical_channels = [
        channel for tile in tiles for channel in range(4 * tile - 3, 4 * tile + 1)
    ]
    pacman_base.configure_hijinks_uart_infrastructure(
        io, io_group, asic_family, logical_channels
    )

    for tile in tiles:
        offset = tile - 1
        values = (
            io.get_reg(0x24030 + offset, io_group=io_group),
            io.get_reg(0x24050 + offset, io_group=io_group) / 4,
            io.get_reg(0x24040 + offset, io_group=io_group),
            io.get_reg(0x24060 + offset, io_group=io_group) / 4,
        )
        print(f"tile {tile}: VDDA={values[0]}mV IDDA={values[1]:.2f}mA "
              f"VDDD={values[2]}mV IDDD={values[3]:.2f}mA")

    return controller


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pacman_config", required=True,
                        help="One-PACMAN JSON; aggregate configs are raw-only")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--settle", type=float, default=0.5,
                        help="seconds to wait before Rev5 power readback")
    main(**vars(parser.parse_args()))
