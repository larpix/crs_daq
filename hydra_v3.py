"""Full v3 discovery from CRS v3_10x16 (4a8b40f), with explicit CERN IO.

Root setup, initial traversal and waitlist recovery use the donor implementation.
A fresh output name is required so commissioning cannot replace saved networks.
"""
import argparse
import os
import time

import larpix
import larpix.io
from base import network_base_FSD_v3 as network_base
from base import pacman_base, utility_base
from base.asic_family import control_io_settings
from base.network_config import network_output_prefix
from runenv import runenv as RUN

v3_10x16_root_ids = [151, 111, 61, 21]


def main(io_group=None, file_prefix=None, disable_logger=True, verbose=False,
         v_cm_lvds_tx=5, tx_diff=7, tx_slice=15, r_term=7, i_rx=0,
         pacman_tile=None, pacman_config=None, allow_incomplete=False):
    selected_iog, family, packet_family = control_io_settings(pacman_config)
    if io_group is None:
        io_group = selected_iog
    if io_group != selected_iog or family != 3:
        raise ValueError('v3 discovery requires a matching single-IOG v3 PACMAN configuration')
    tiles = [pacman_tile] if pacman_tile is not None else RUN.io_group_pacman_tile_[io_group]
    prefixes = {}
    for tile in tiles:
        if tile not in RUN.io_group_pacman_tile_[io_group]:
            raise ValueError(f'IOG {io_group}: tile {tile} is not configured')
        prefix = file_prefix or f'iog_{io_group}-tile_{tile}'
        if file_prefix and len(tiles) > 1:
            prefix = f'{file_prefix}-iog_{io_group}-tile_{tile}'
        prefix = network_output_prefix(prefix)
        if os.path.exists(prefix + '-hydra-network.json'):
            raise FileExistsError(f'{prefix}-hydra-network.json already exists; use a fresh --file_prefix')
        prefixes[tile] = prefix

    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True, config_filepath=pacman_config,
                              asic_version=packet_family)
    utility_base.update_json(RUN.asic_config_paths_file_, io_group, None)
    utility_base.update_json(RUN.network_config_paths_file_, io_group, None)
    channels = utility_base.tile_to_io_channel(tiles)
    pacman_base.configure_hijinks_uart_infrastructure(c.io, io_group, family, channels)
    for _ in range(2):
        c.io.reset_tiles(tiles, length=4096 * 4, io_group=io_group)
        time.sleep(4096 * 4 * 1e-6)

    no_root_tiles = []
    for tile in tiles:
        root_keys = []
        excluded = RUN.iog_exclude[io_group]
        excluded_links = RUN.iog_exclude_links[io_group]
        channels = utility_base.tile_to_io_channel([tile])
        live_channels = [ch for ch in channels
                         if pacman_base.convert_io_channel_to_uart(ch) is not None]
        for channel in live_channels:
            chip_id = v3_10x16_root_ids[(channel - 1) % 4]
            if chip_id in excluded[str(tile)]:
                print(f'IOG {io_group} tile {tile}: excluding root {chip_id} on channel {channel}', flush=True)
                continue
            network_base.network_ext_node_from_tuple(c, io_group, channel, chip_id)
            root = network_base.setup_root(
                c, c.io, io_group, channel, chip_id, verbose, family,
                v_cm_lvds_tx, tx_diff, tx_slice, r_term, i_rx)
            if root is None:
                print(f'IOG {io_group} tile {tile}: root {io_group}-{channel}-{chip_id} did not configure; continuing with other roots', flush=True)
                # setup_root removes the failed chip; remove its external link
                # placeholders as well, so no dangling root reaches the export.
                del c.network[io_group][channel]
                continue
            root_keys.append(root)
        print(f'Tile {tile} ROOT KEYS: {root_keys}', flush=True)
        if not root_keys:
            print(f'IOG {io_group} tile {tile}: no usable roots; no topology exported', flush=True)
            no_root_tiles.append(tile)
            continue
        network_base.initial_network(
            c, c.io, io_group, root_keys, verbose, family,
            v_cm_lvds_tx, tx_diff, tx_slice, r_term, i_rx,
            exclude=excluded, exclude_links=excluded_links)
        unconfigured = network_base.iterate_waitlist(
            c, c.io, io_group, live_channels, verbose, family,
            v_cm_lvds_tx, tx_diff, tx_slice, r_term, i_rx,
            exclude=excluded, exclude_links=excluded_links)
        # The donor's returned outstanding list can include earlier failures
        # subsequently recovered; inspect the final controller's actual coverage.
        missing, _ = network_base.find_waitlist(c, io_group, live_channels)
        missing = sorted(set(missing) - set(excluded[str(tile)]))
        if missing:
            print(f'IOG {io_group} tile {tile}: {len(missing)} missing ASICs: {missing}', flush=True)
            if not allow_incomplete:
                raise RuntimeError('Incomplete discovery; no topology exported (use --allow-incomplete to save a valid partial network)')
        unconfigured = [pair for pair in unconfigured if pair[0].chip_id in missing]
        # Retain UART failure details and include chips with no attempted link.
        # Channel 0 denotes unknown routing, never a channel to replay.
        reported = {pair[0].chip_id for pair in unconfigured}
        unconfigured.extend((larpix.Key(io_group, 0, chip_id), None)
                            for chip_id in missing if chip_id not in reported)
        network_file = network_base.write_network_to_file(
            c, prefixes[tile], {io_group: [tile]}, unconfigured, asic_version=family)
        status = f'INCOMPLETE ({len(missing)} missing ASICs)' if missing else 'complete'
        print(f'Discovery {status}; topology exported to {network_file}', flush=True)
    if no_root_tiles:
        raise RuntimeError(f'IOG {io_group}: no usable roots on tiles {no_root_tiles}; those topologies were not exported')
    return c, c.io


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pacman_config', '--config', required=True)
    parser.add_argument('--io_group', '--io-group', type=int)
    parser.add_argument('--pacman_tile', '--tile', type=int)
    parser.add_argument('--file_prefix', '--file-prefix')
    parser.add_argument('--allow-incomplete', action='store_true',
                        help='save a validated partial topology and report missing ASICs')
    parser.add_argument('--disable_logger', action='store_true', default=True)
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--v_cm_lvds_tx', type=int, default=5)
    parser.add_argument('--tx_diff', type=int, default=7)
    parser.add_argument('--tx_slice', type=int, default=15)
    parser.add_argument('--r_term', type=int, default=7)
    parser.add_argument('--i_rx', type=int, default=0)
    main(**vars(parser.parse_args()))
