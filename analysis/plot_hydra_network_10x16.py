# Adapted from: https://github.com/larpix/larpix-10x10-scripts/blob/main/plot_hydra_network_v2a.py

import matplotlib.pyplot as plt
import yaml
import numpy as np
import argparse
import json
from matplotlib.patches import Rectangle
from matplotlib.collections import PatchCollection

_default_controller_config = None
_default_io_group = 1
_default_geometry_yaml = 'analysis/layout-3.0.0.yaml'


colors = ['c', 'm', 'y', 'g']


def parse_hydra_network(network_json, iog):
    chipID_uart, missingIO = [{} for i in range(2)]
    ioc_chip = dict()
    root_chips = []
    with open(network_json, 'r') as f:
        data = json.load(f)
        if 'bad_uart_links' in missingIO:
            missingIO = data['bad_uart_links']
        mapping = data['network']['miso_us_uart_map']
        hydra = data['network'][str(iog)]
        for ioc in hydra:
            ioc_chip[ioc] = []
            for node in hydra[ioc]["nodes"]:
                if node['chip_id'] != 'ext' and (int(node['chip_id']) < 11 or int(node['chip_id']) > 170):
                    continue
                ioc_chip[ioc].append(node['chip_id'])
                if node['chip_id'] not in chipID_uart:
                    chipID_uart[node['chip_id']] = []
                if 'root' in node:
                    if node['root']:
                        l = node['miso_us']
                        for e in l:
                            if e != None:
                                root_chips.append(e)
                                break

                for i in range(len(node['miso_us'])):
                    if node['miso_us'][i] != None:
                        chipID_uart[node['chip_id']].append(mapping[i])

    return chipID_uart, missingIO, root_chips, ioc_chip


def start_end(chipID, uart, chipid_pos):
    dX = chipid_pos[chipID]['maxX']-chipid_pos[chipID]['avgX']
    dY = chipid_pos[chipID]['maxY']-chipid_pos[chipID]['avgY']

    if uart == 1:
        start = (chipid_pos[chipID]['avgX']+(dX/2), chipid_pos[chipID]['avgY'])
        end = (dX, 0)
    if uart == 3:
        start = (chipid_pos[chipID]['avgX']-(dX/2), chipid_pos[chipID]['avgY'])
        end = (dX*-1, 0)
    if uart == 2:
        start = (chipid_pos[chipID]['avgX'], chipid_pos[chipID]['avgY']+(dY/2))
        end = (0, dY)
    if uart == 0:
        start = (chipid_pos[chipID]['avgX'], chipid_pos[chipID]['avgY']-(dY/2))
        end = (0, dY*-1)
    return start, end


def chip_to_ioc(chipID, ioc_chip):
    for ioc in ioc_chip.keys():
        if chipID in ioc_chip[ioc]:
            return int(ioc)
    return


def plot_hydra_network(geometry_yaml, chipID_uart, missingIO, root_chips, tile_id, pacman_tile, io_group, ioc_chip):
    with open(geometry_yaml) as fi:
        geo = yaml.full_load(fi)
    chip_pix = dict([(chip_id+1, pix) for chip_id, pix in geo['chips']])
    vertical_lines = np.linspace(-1*(geo['width']/2), geo['width']/2, 17)
    horizontal_lines = np.linspace(-1*(geo['height']/2), geo['height']/2, 11)

    fig, ax = plt.subplots(figsize=(16, 10))
    ax.set_xlabel('X Position [mm]')
    ax.set_ylabel('Y Position [mm]')
    ax.set_xticks(vertical_lines)
    ax.set_yticks(horizontal_lines)
    ax.set_xlim(vertical_lines[0]*1.1, vertical_lines[-1]*1.1)
    ax.set_ylim(horizontal_lines[0]*1.1, horizontal_lines[-1]*1.1)
    for vl in vertical_lines:
        ax.vlines(
            x=vl, ymin=horizontal_lines[0], ymax=horizontal_lines[-1], colors=['k'], linestyle='dotted')
    for hl in horizontal_lines:
        ax.hlines(
            y=hl, xmin=vertical_lines[0], xmax=vertical_lines[-1], colors=['k'], linestyle='dotted')

    chipid_pos = dict()
    for chipid in chip_pix.keys():
        x, y = [[] for i in range(2)]
        for channelid in range(64):
            x.append(geo['pixels'][chip_pix[chipid][channelid]][1])
            y.append(geo['pixels'][chip_pix[chipid][channelid]][2])
        avgX = (max(x)+min(x))/2.
        avgY = (max(y)+min(y))/2.
        chipid_pos[chipid] = dict(minX=min(x), maxX=max(
            x), avgX=avgX, minY=min(y), maxY=max(y), avgY=avgY)
        plt.annotate(str(chipid), [avgX, avgY], ha='center', va='center')

    for chipID in chipID_uart.keys():
        if chipID == 'ext':
            continue
        for uart in chipID_uart[chipID]:
            start, end = start_end(int(chipID), uart, chipid_pos)
            plt.arrow(start[0], start[1], end[0], end[1], width=1.0,
                      color=colors[chip_to_ioc(chipID, ioc_chip)-1], alpha=0.5)

    for i in range(len(missingIO)):
        chipIDpair = missingIO[i]
        A = chipIDpair[0]
        B = chipIDpair[1]
        if A > B:
            A = chipIDpair[1]
            B = chipIDpair[0]

        r = Rectangle((chipid_pos[A]['maxX'],
                       chipid_pos[A]['minY']),
                      abs(chipid_pos[A]['maxX']-chipid_pos[B]['minX']),
                      abs(chipid_pos[A]['maxY']-chipid_pos[A]['minY']),
                      color='r', alpha=0.8)

        if abs(A-B) == 10:
            r = Rectangle((chipid_pos[A]['minX'], chipid_pos[A]['minY']),
                          abs(chipid_pos[A]['maxX']-chipid_pos[A]['minX']),
                          abs(chipid_pos[A]['minY']-chipid_pos[B]['maxY'])*-1,
                          color='r', alpha=0.8)

        plt.gca().add_patch(r)

    for chipID in chipid_pos.keys():
        if chipID not in chipID_uart.keys():
            r = Rectangle((chipid_pos[chipID]['minX'], chipid_pos[chipID]['minY']),
                          abs(chipid_pos[chipID]['maxX'] -
                              chipid_pos[chipID]['minX']),
                          abs(chipid_pos[chipID]['maxY'] -
                              chipid_pos[chipID]['minY']),
                          color='r', alpha=0.2)
            plt.gca().add_patch(r)
        if chipID in root_chips:
            r = Rectangle((chipid_pos[chipID]['minX'], chipid_pos[chipID]['minY']),
                          abs(chipid_pos[chipID]['maxX'] -
                              chipid_pos[chipID]['minX']),
                          abs(chipid_pos[chipID]['maxY'] -
                              chipid_pos[chipID]['minY']),
                          color='b', alpha=0.2)
            plt.gca().add_patch(r)

    plt.title('Tile ID '+str(tile_id)+'\n (PACMAN tile ' +
              str(pacman_tile)+', IO group '+str(io_group)+')')
    plt.savefig('hydra-network-tile-id-'+str(tile_id)+'.png')
    print('Saved to: ', 'hydra-network-tile-id-'+str(tile_id)+'.png')


def main(controller_config=_default_controller_config, geometry_yaml=_default_geometry_yaml, io_group=_default_io_group, **kwargs):
    if controller_config == None:
        print('Hydra network JSON configuration file missing.\n',
              '==> Specify with --controller_config <filename> commandline argument')
        return
    tile_id = controller_config.split('-')[2]
    pacman_tile = controller_config.split('-')[5]

    chipID_uart, missingIO, root_chips, ioc_chip = parse_hydra_network(
        controller_config, io_group)

    if len(missingIO) > 0:
        if missingIO[0] == "no test performed":
            missingIO = {}

    plot_hydra_network(geometry_yaml, chipID_uart, missingIO, root_chips,
                       tile_id, pacman_tile, io_group, ioc_chip)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--controller_config', default=_default_controller_config,
                        type=str, help='''Hydra network json configuration file''')
    parser.add_argument('--geometry_yaml', default=_default_geometry_yaml, type=str,
                        help='''geometry yaml (layout 2.4.0 for LArPix-v2a 10x10 tile)''')
    parser.add_argument('--io_group', default=_default_io_group,
                        type=int, help='''PACMAN IO group''')
    args = parser.parse_args()
    main(**vars(args))
