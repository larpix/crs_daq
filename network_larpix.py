import warnings
warnings.filterwarnings("ignore")
import larpix
import time
import larpix.io
from runenv import runenv as RUN
import argparse
from base import config_loader
from base import network_base_FSD
from base import network_base_FSD_v3
from tqdm import tqdm
from base import pacman_base
from base import utility_base
from base import enforce_parallel
from base.asic_family import control_io_settings
from base.network_config import validate_control_network
import json
from base.utility_base import now
import logging
import sys
import os

module = sys.modules[__name__]
for var in RUN.config.keys():
    setattr(module, var, getattr(RUN, var))

_default_verbose = False
_default_controller_config = None
_update_default=False

def enforce_iterative(nc, all_network_keys, n=1, configs=None, pbar_desc='p',
                      pbar_position=0, pacman_config=None):
    """Retry donor replay on failed tiles, preserving the desired controller.

    A tile reset also resets previously successful chips on that tile. Include
    those chips in the next enforcement pass, as well as unattempted work.
    """
    pending = all_network_keys
    for attempt in range(n + 1):
        result = enforce_parallel.enforce_parallel(
            nc, pending, pbar_desc=pbar_desc, pbar_position=pbar_position)
        ok, diff, unconfigured = result
        if ok or attempt == n:
            return result
        io_group, asic_family, packet_family = control_io_settings(pacman_config)
        failed = {larpix.Key(str(key)) for key in diff}
        if not failed:
            failed = {key for net in pending for key in net}
        outstanding = {key for net in unconfigured for key in net}
        if any(key.io_group != io_group for key in failed | outstanding):
            raise ValueError('Retry contains chips outside the selected PACMAN/IOG')
        tiles = sorted({utility_base.io_channel_to_tile(key.io_channel) for key in failed})
        topology = configs[str(io_group)]
        validate_control_network(topology, io_group, asic_family)
        coverage = enforce_parallel.get_chips_by_io_group_io_channel(topology)
        enforce_parallel.validate_configurations(
            nc, coverage, io_group, asic_family, 'in-memory desired configs', topology)
        if not (failed | outstanding).issubset(nc.chips):
            raise ValueError('Retry requested ASICs absent from the desired configuration')
        affected = {key for key in nc.chips
                    if key.io_group == io_group
                    and utility_base.io_channel_to_tile(key.io_channel) in tiles}
        retry_keys = failed | outstanding | affected
        pending = [[key for key in net if key in retry_keys] for net in coverage]
        replay = (network_base_FSD.network_v2b if asic_family == '2d'
                  else network_base_FSD_v3.network_v3)
        print(f'IOG {io_group}: reconstructing tiles {tiles} before retry {attempt + 1}', flush=True)
        recovered = replay(topology, tiles=tiles, io_group=io_group,
                           pacman_config=pacman_config, packet_family=packet_family)
        if recovered.io is not None:
            recovered.io.cleanup()
        pacman_base.enable_pacman_uart_from_io_channel(
            nc.io, io_group, sorted({key.io_channel for net in pending for key in net}))

def main(verbose,
         controller_config,
         io_group_tiles=None,
         pacman_config=None,
         config_path=None,
         pid_logged=False,
         update_default=_update_default):
    io_group, asic_family, packet_family = control_io_settings(pacman_config)
    with open(controller_config) as infile:
        configs = json.load(infile)
    topology = configs[str(io_group)]
    if not os.path.dirname(topology):
        topology = os.path.join('configs', topology)
    configs[str(io_group)] = topology
    validate_control_network(topology, io_group, asic_family)
    tiles = None if io_group_tiles is None else io_group_tiles.get(io_group)
    if io_group_tiles is not None and io_group not in io_group_tiles:
        raise ValueError(f'No tiles selected for IOG {io_group}')
    all_network_keys = enforce_parallel.get_chips_by_io_group_io_channel(topology, tiles)
    coverage_keys = enforce_parallel.get_chips_by_io_group_io_channel(topology)

    selected = utility_base.get_from_json(default_asic_config_paths_file_, io_group)
    generate = update_default or selected is None
    nc = larpix.Controller()
    if generate:
        config_path = config_path or os.path.join(asic_config_dir, 'asic_configs_' + now())
        # Reserve a fresh destination before hardware access. Never overwrite
        # an earlier operating configuration, even on an explicit refresh.
        os.makedirs(config_path, exist_ok=False)
        selected = config_path
        print(f'IOG {io_group}: generating defaults in {selected}', flush=True)
    else:
        config_loader.load_config_from_directory(nc, selected)
        enforce_parallel.validate_configurations(
            nc, coverage_keys, io_group, asic_family, selected, topology)
        print(f'IOG {io_group}: reusing defaults from {selected}', flush=True)

    # A replay resets hardware. Current state becomes valid again only after
    # successful enforcement; saved defaults remain available on failure.
    utility_base.update_json(asic_config_paths_file_, io_group, None)
    utility_base.update_json(network_config_paths_file_, io_group, None)
    replay = (network_base_FSD.network_v2b if asic_family == '2d'
              else network_base_FSD_v3.network_v3)
    c = replay(topology, tiles=tiles, io_group=io_group,
               pacman_config=pacman_config, packet_family=packet_family)
    if generate:
        enforce_parallel.validate_configurations(
            c, coverage_keys, io_group, asic_family, selected, topology)
        config_loader.write_config_to_file(c, selected)
        config_loader.load_config_from_directory(nc, selected)
        enforce_parallel.validate_configurations(
            nc, coverage_keys, io_group, asic_family, selected, topology)

    nc.io = larpix.io.PACMAN_IO(
        relaxed=True, config_filepath=pacman_config, asic_version=packet_family)
    pacman_base.enable_pacman_uart_from_io_channel(
        nc.io, io_group, sorted({chip.io_channel for chip in nc.chips}))
    pos, tag = 0, 'networking...'
    if pid_logged:
        tag = utility_base.get_from_process_log(os.getpid()) or f'IOG{io_group}'
        pos = enforce_parallel.tag_to_config_map.get(tag, io_group - 1)
    ok, diff, unconfigured = enforce_iterative(
        nc, all_network_keys, configs=configs, pbar_desc=tag, pbar_position=pos,
        pacman_config=pacman_config)
    if not ok:
        raise RuntimeError('Unconfigured chips!', diff)
    if generate:
        utility_base.update_json(default_asic_config_paths_file_, io_group, selected)
    utility_base.update_json(network_config_paths_file_, io_group, topology)
    utility_base.update_json(asic_config_paths_file_, io_group, selected)
    print(f'IOG {io_group}: networked successfully; ASIC configs: {selected}', flush=True)
    return c

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', '-v', action='store_true',  default=_default_verbose)
    parser.add_argument('--config_path', default=None, \
                        type=str, help='''Path to save configuration''')
    parser.add_argument('--controller_config', default=_default_controller_config, \
                        type=str, help='''Controller config specifying hydra network''')                  
    parser.add_argument('--pacman_config', default="io/pacman.json", \
                        type=str, help='''Config specifying PACMANs''')
    parser.add_argument('--pid_logged', action='store_true', default=False)
    parser.add_argument('--update_default', action='store_true', default=False)
    args=parser.parse_args()
    c = main(**vars(args))
