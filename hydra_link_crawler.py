#!/usr/bin/env python3
"""Incrementally test one LArPix-v2b Hydra network, one link at a time.

Power the tile before running this script. It does not change VDDA/VDDD.
After each accepted chip it saves a controller-network JSON, ASIC configs,
and a machine-readable crawl_state.json. An optional hook can run pedestal,
configuration, or data-taking commands after every accepted chip.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from collections import deque

import larpix
import larpix.io

from base import config_loader, network_base, pacman_base, uart_base, utility_base
from runenv import runenv as RUN


def now():
    return time.strftime('%Y_%m_%d_%H_%M_%S_%Z')


def csv_ints(text, preserve=False):
    values = []
    for item in str(text or '').split(','):
        item = item.strip()
        if item and int(item) not in values:
            values.append(int(item))
    return values if preserve else sorted(values)


def safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(safe(k)): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [safe(v) for v in value]
    if all(hasattr(value, x) for x in ('io_group', 'io_channel', 'chip_id')):
        return {'io_group': value.io_group, 'io_channel': value.io_channel, 'chip_id': value.chip_id}
    return str(value)


def write_json(path, payload):
    tmp = path + '.tmp'
    with open(tmp, 'w') as out:
        json.dump(payload, out, indent=2, sort_keys=True, default=safe)
        out.write('\n')
    os.replace(tmp, path)


def chip_row(chip_id):
    return (chip_id - 11) // 10


def neighbor(chip_id, delta):
    candidate = chip_id + delta
    if not 11 <= candidate <= 110:
        return None
    if abs(delta) == 1 and chip_row(candidate) != chip_row(chip_id):
        return None
    return candidate


def uart_index(parent, daughter):
    return {-10: 0, -1: 1, 10: 2, 1: 3}[daughter - parent]


def network_payload(args, accepted, parents):
    children = {}
    for daughter, parent in parents.items():
        children.setdefault(parent, []).append(daughter)
    nodes = [{'chip_id': 'ext', 'miso_us': [None, None, None, args.root_chip], 'root': True}]
    for chip_id in accepted:
        links = [None] * 4
        for daughter in children.get(chip_id, []):
            links[uart_index(chip_id, daughter)] = daughter
        nodes.append({'chip_id': chip_id, 'miso_us': links})
    return {
        '_config_type': 'controller', 'name': 'incremental-hydra-crawl',
        'asic_version': '2b', 'layout': '2.5.1',
        'network': {
            str(args.io_group): {str(args.io_channel): {'nodes': nodes}},
            'miso_us_uart_map': [3, 0, 1, 2],
            'miso_ds_uart_map': [1, 2, 3, 0],
            'mosi_uart_map': [2, 3, 0, 1],
        },
    }


def save_stage(controller, args, accepted, parents):
    stage = len(accepted)
    chip_id = accepted[-1]
    stage_dir = os.path.join(args.output_dir, 'stage-{:03d}-chip-{:03d}'.format(stage, chip_id))
    os.makedirs(stage_dir, exist_ok=True)
    network = os.path.join(stage_dir, 'network.json')
    payload = network_payload(args, accepted, parents)
    write_json(network, payload)
    write_json(os.path.join(args.output_dir, 'network-latest.json'), payload)
    asic_dir = os.path.join(stage_dir, 'asic_configs')
    os.makedirs(asic_dir, exist_ok=True)
    config_loader.write_config_to_file(controller, path=asic_dir,
                                       description='incremental Hydra crawl stage {}'.format(stage))
    return stage_dir, network, asic_dir


def reset_tile(io, args):
    for _ in range(2):
        io.reset_tiles(tiles=[args.pacman_tile], length=4096, io_group=args.io_group)
        time.sleep(4096e-6)


def cleanup(controller, parent, daughter, args):
    errors = []
    for label, fn in (
        ('reset daughter', lambda: uart_base.reset_uarts(controller, daughter, args.verbose)),
        ('disable parent piso', lambda: uart_base.disable_parent_piso_us(
            controller, parent, daughter, args.verbose, args.tx_diff, args.tx_slice)),
        ('disable parent posi', lambda: uart_base.disable_parent_posi(
            controller, parent, daughter, args.verbose)),
    ):
        try:
            if label != 'reset daughter' or daughter in controller.chips:
                fn()
        except Exception as exc:
            errors.append('{}: {}: {}'.format(label, exc.__class__.__name__, exc))
    try:
        if daughter in controller.chips:
            controller.remove_chip(daughter)
    except Exception as exc:
        errors.append('remove daughter: {}: {}'.format(exc.__class__.__name__, exc))
    return errors


def enqueue(frontier, queued, parent, accepted, excluded, failed, order):
    for delta in order:
        daughter = neighbor(parent, delta)
        edge = (parent, daughter)
        if daughter is None or daughter in accepted or daughter in excluded:
            continue
        if edge not in queued and edge not in failed:
            frontier.append(edge)
            queued.add(edge)


def state(args, status, accepted, parents, failed, attempts, frontier):
    write_json(os.path.join(args.output_dir, 'crawl_state.json'), {
        'status': status, 'updated': now(),
        'parameters': {
            'io_group': args.io_group, 'pacman_tile': args.pacman_tile,
            'io_channel': args.io_channel, 'root_chip': args.root_chip,
            'max_link_failures': args.max_link_failures,
            'excluded_chips': sorted(args.excluded_chips),
            'neighbor_order': args.neighbor_order,
            'verify_network_after_add': args.verify_network_after_add,
        },
        'accepted_order': accepted,
        'parent_by_chip': {str(k): v for k, v in parents.items()},
        'failed_edges': sorted([list(x) for x in failed]),
        'attempts': attempts,
        'frontier': [list(x) for x in frontier],
    })


def run_hook(args, stage_dir, network, asic_dir, chip_id, parent):
    if not args.after_success_command:
        return 0
    context = {
        'stage': len(os.path.basename(stage_dir).split('-')),
        'chip_id': chip_id, 'parent_chip': parent,
        'io_group': args.io_group, 'io_channel': args.io_channel,
        'pacman_tile': args.pacman_tile,
        'network': shlex.quote(network), 'asic_dir': shlex.quote(asic_dir),
        'stage_dir': shlex.quote(stage_dir), 'output_dir': shlex.quote(args.output_dir),
    }
    command = args.after_success_command.format(**context)
    with open(os.path.join(stage_dir, 'after_success.log'), 'w') as log:
        log.write('$ {}\n\n'.format(command)); log.flush()
        return subprocess.run(command, shell=True, stdout=log, stderr=subprocess.STDOUT).returncode


def crawl(args):
    os.makedirs(args.output_dir, exist_ok=True)
    if args.dry_run:
        print(vars(args)); return 0
    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True, config_filepath=args.pacman_config)
    reset_tile(c.io, args)
    pacman_base.invert_pacman_uart(c.io, args.io_group,
                                   RUN.io_group_asic_version_[args.io_group], [args.pacman_tile])
    c.io.set_uart_clock_ratio(args.io_channel, 10, io_group=args.io_group)
    network_base.network_ext_node_from_tuple(c, args.io_group, args.io_channel, args.root_chip)

    attempts = []
    root = None
    for number in range(1, args.root_attempts + 1):
        try:
            root = network_base.setup_root(
                c, c.io, args.io_group, args.io_channel, args.root_chip, args.verbose,
                RUN.io_group_asic_version_[args.io_group], args.ref_current_trim,
                args.tx_diff, args.tx_slice, args.r_term, args.i_rx)
            error = None
        except Exception as exc:
            root, error = None, '{}: {}'.format(exc.__class__.__name__, exc)
        attempts.append({'kind': 'root', 'attempt': number, 'ok': root is not None, 'error': error})
        if root is not None: break
        if number < args.root_attempts: reset_tile(c.io, args)
    if root is None:
        state(args, 'root_failed', [], {}, set(), attempts, [])
        return 2

    accepted = [args.root_chip]
    accepted_set = set(accepted)
    parents, failed = {}, set()
    frontier, queued = deque(), set()
    enqueue(frontier, queued, args.root_chip, accepted_set, args.excluded_chips,
            failed, args.neighbor_order)
    stage_dir, network, asic_dir = save_stage(c, args, accepted, parents)
    if run_hook(args, stage_dir, network, asic_dir, args.root_chip, 'ext') and args.stop_on_hook_failure:
        state(args, 'hook_failed', accepted, parents, failed, attempts, frontier); return 3

    while frontier and len(accepted) < args.max_chips:
        parent_id, daughter_id = frontier.popleft(); queued.discard((parent_id, daughter_id))
        if daughter_id in accepted_set: continue
        parent = larpix.key.Key(args.io_group, args.io_channel, parent_id)
        daughter = larpix.key.Key(args.io_group, args.io_channel, daughter_id)
        ok = False
        print('\nTesting {} -> {}'.format(parent_id, daughter_id))
        for number in range(1, args.max_link_failures + 1):
            diff, verify_diff, error, cleanup_errors = {}, {}, None, []
            try:
                if daughter in c.chips: c.remove_chip(daughter)
                parent_ok, diff = uart_base.setup_parent_piso(
                    c, c.io, parent, daughter, args.verbose, args.tx_diff, args.tx_slice)
                if not parent_ok: raise RuntimeError('parent did not reconcile')
                daughter_ok, diff, _ = uart_base.setup_daughter(
                    c, c.io, parent, daughter, args.verbose,
                    RUN.io_group_asic_version_[args.io_group], args.ref_current_trim,
                    args.tx_diff, args.tx_slice, args.r_term, args.i_rx)
                if not daughter_ok: raise RuntimeError('daughter did not reconcile')
                if args.verify_network_after_add:
                    keys = [larpix.key.Key(args.io_group, args.io_channel, x)
                            for x in accepted + [daughter_id]]
                    network_ok, verify_diff = utility_base.reconcile_configuration(c, keys, args.verbose)
                    if not network_ok: raise RuntimeError('partial network did not reconcile')
                ok = True
            except Exception as exc:
                error = '{}: {}'.format(exc.__class__.__name__, exc)
                cleanup_errors = cleanup(c, parent, daughter, args)
            attempts.append({'kind': 'link', 'parent_chip': parent_id,
                             'daughter_chip': daughter_id, 'attempt': number, 'ok': ok,
                             'diff': safe(diff), 'verify_diff': safe(verify_diff),
                             'error': error, 'cleanup_errors': cleanup_errors})
            state(args, 'running', accepted, parents, failed, attempts, frontier)
            if ok: break
            print('  failed {}/{}: {}'.format(number, args.max_link_failures, error))
        if not ok:
            failed.add((parent_id, daughter_id))
            print('Giving up on {} -> {}'.format(parent_id, daughter_id))
            if args.stop_on_failed_link: break
            continue
        accepted.append(daughter_id); accepted_set.add(daughter_id); parents[daughter_id] = parent_id
        enqueue(frontier, queued, daughter_id, accepted_set, args.excluded_chips,
                failed, args.neighbor_order)
        stage_dir, network, asic_dir = save_stage(c, args, accepted, parents)
        print('Accepted chip {}; {} total'.format(daughter_id, len(accepted)))
        if run_hook(args, stage_dir, network, asic_dir, daughter_id, parent_id) and args.stop_on_hook_failure:
            state(args, 'hook_failed', accepted, parents, failed, attempts, frontier); return 3

    status = 'max_chips_reached' if len(accepted) >= args.max_chips else 'complete'
    if args.stop_on_failed_link and failed: status = 'stopped_on_failed_link'
    state(args, status, accepted, parents, failed, attempts, frontier)
    print('\n{}: accepted {}; failed links {}'.format(status, accepted, sorted(failed)))
    return 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--io-group', '--io_group', dest='io_group', type=int, required=True)
    p.add_argument('--pacman-tile', '--pacman_tile', dest='pacman_tile', type=int, required=True)
    p.add_argument('--io-channel', '--io_channel', dest='io_channel', type=int, required=True)
    p.add_argument('--root-chip', '--root_chip', dest='root_chip', type=int, required=True)
    p.add_argument('--pacman-config', default=None)
    p.add_argument('--excluded-chips', default='')
    p.add_argument('--max-link-failures', type=int, default=3)
    p.add_argument('--root-attempts', type=int, default=3)
    p.add_argument('--max-chips', type=int, default=100)
    p.add_argument('--neighbor-order', default='1,-1,10,-10')
    p.add_argument('--stop-on-failed-link', action='store_true')
    p.add_argument('--no-verify-network-after-add', dest='verify_network_after_add', action='store_false')
    p.set_defaults(verify_network_after_add=True)
    p.add_argument('--after-success-command', default=None)
    p.add_argument('--continue-on-hook-failure', dest='stop_on_hook_failure', action='store_false')
    p.set_defaults(stop_on_hook_failure=True)
    p.add_argument('--output-dir', default=None)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('--ref-current-trim', type=int, default=0)
    p.add_argument('--tx-diff', type=int, default=0)
    p.add_argument('--tx-slice', type=int, default=15)
    p.add_argument('--r-term', type=int, default=2)
    p.add_argument('--i-rx', type=int, default=8)
    return p


def main():
    p = parser(); args = p.parse_args()
    args.excluded_chips = set(csv_ints(args.excluded_chips))
    args.neighbor_order = csv_ints(args.neighbor_order, preserve=True)
    if sorted(args.neighbor_order) != [-10, -1, 1, 10] or len(args.neighbor_order) != 4:
        p.error('--neighbor-order must contain 1,-1,10,-10 exactly once')
    if args.io_group not in RUN.io_group_asic_version_ or RUN.io_group_asic_version_[args.io_group] != '2b':
        p.error('first pass supports only v2b io groups')
    if ((args.io_channel - 1) // 4) + 1 != args.pacman_tile:
        p.error('io_channel does not belong to pacman_tile')
    if not 11 <= args.root_chip <= 110 or args.root_chip in args.excluded_chips:
        p.error('invalid or excluded root chip')
    if min(args.max_link_failures, args.root_attempts, args.max_chips) < 1:
        p.error('attempt counts and max-chips must be positive')
    args.pacman_config = args.pacman_config or 'io/pacman_io{}.json'.format(args.io_group)
    args.output_dir = os.path.abspath(args.output_dir or os.path.join(
        'diagnostic_results', 'hydra-crawl-iog{}-tile{}-ioc{}-root{}-{}'.format(
            args.io_group, args.pacman_tile, args.io_channel, args.root_chip, now())))
    return crawl(args)


if __name__ == '__main__':
    sys.exit(main())
