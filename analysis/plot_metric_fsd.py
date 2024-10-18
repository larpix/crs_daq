import h5py
import matplotlib.pyplot as plt
import yaml
import numpy as np
import argparse
import json
from matplotlib.patches import Rectangle
from matplotlib.collections import PatchCollection
from matplotlib import cm
from matplotlib.colors import Normalize
from collections import defaultdict
import tqdm

plt.rcParams.update({'font.size': 15})

_default_filename = None

# _default_geometry_yaml = 'layout-2.4.0.yaml'
_default_geometry_yaml = 'analysis/multi_tile_layout-3.0.40.yaml'

_default_metric = 'mean'

_default_pixel_pitch = 4.4  # mm

_default_max_entries = -1


def _default_pxy():
    return (0., 0.)


def _rotate_pixel(pixel_pos, tile_orientation):
    return pixel_pos[0]*tile_orientation[2], pixel_pos[1]*tile_orientation[1]


def unique_channel_id(d):
    return ((d['io_group'].astype(int)*1000+d['io_channel'].astype(int))*1000
            + d['chip_id'].astype(int))*100 + d['channel_id'].astype(int)


def unique_to_channel_id(unique):
    return unique % 100


def unique_to_chip_id(unique):
    return (unique // 100) % 1000


def unique_to_io_channel(unique):
    return (unique//(100*1000)) % 1000


def unique_to_tiles(unique):
    return ((unique_to_io_channel(unique)-1) // 4) + 1


def unique_to_io_group(unique):
    return (unique // (100*1000*1000)) % 1000


def parse_file(filename, max_entries):
    d = dict()
    f = h5py.File(filename, 'r')
    unixtime = f['packets'][:]['timestamp'][f['packets']
                                            [:]['packet_type'] == 4]
    livetime = np.max(unixtime)-np.min(unixtime)
    data_mask = f['packets'][:]['packet_type'] == 0
    valid_parity_mask = f['packets'][:]['valid_parity'] == 1
    mask = np.logical_and(data_mask, valid_parity_mask)
    adc = f['packets']['dataword'][mask][:max_entries]
    unique_id = unique_channel_id(f['packets'][mask][:max_entries])
    unique_id_set = np.unique(unique_id)
    chips = f['packets']['chip_id'][mask][:max_entries]

    print("Number of packets in parsed files =", len(unique_id))
    for chip in tqdm.tqdm(range(11, 171), desc='looping over chip_id'):
        _iomask = chips==chip
        _adc = adc[_iomask]
        _unique_id = unique_id[_iomask]
        for i in set(_unique_id):
            id_mask = _unique_id == i
            masked_adc = _adc[id_mask]
            d[i] = dict(
                mean=np.mean(masked_adc),
                std=np.std(masked_adc),
                rate=len(masked_adc) / (livetime + 1e-9))
    return d


def plot_1d(d, metric):

    io_groups = set(unique_to_io_group(np.array(list(d.keys()))))
    tiles = set(unique_to_tiles(np.array(list(d.keys()))))

    for io_group in io_groups:
        for tile in tiles:
            tile_id = '{}-{}'.format(io_group, tile)

            mask = unique_to_io_group(np.array(list(d.keys()))) == io_group
            mask = np.logical_and(mask, unique_to_tiles(
                np.array(list(d.keys()))) == tile)

            if not np.any(mask):
                continue

            fig, ax = plt.subplots(figsize=(8, 8))
            d_keys = np.array(list(d.keys()))[mask]
            a = [d[key][metric] for key in d_keys]

            min_bin = int(min(a))  # -1
            max_bin = int(max(a))  # +1
            n_bins = max_bin-min_bin

            ax.hist(a, bins=np.linspace(min_bin, max_bin, n_bins))
            ax.grid(True)
            ax.set_ylabel('Channel Count')
            ax.set_title('Tile ID '+str(tile_id))
            ax.set_yscale('log')
            plt.text(0.95, 1.01, 'LArPix', ha='center',
                     va='center', transform=ax.transAxes)

            if metric == 'mean':
                ax.set_xlabel('ADC Mean')
                plt.savefig('tile-id-'+str(tile_id)+'-1d-mean.png')
            if metric == 'std':
                ax.set_xlabel('ADC RMS')
                plt.savefig('tile-id-'+str(tile_id)+'-1d-std.png')
            if metric == 'rate':
                ax.set_xlabel('Trigger Rate [Hz]')
                plt.savefig('tile-id-'+str(tile_id)+'-1d-rate.png')


def plot_xy(d, metric, geometry_yaml, normalization):

    cmap = cm.hot_r
    pixel_pitch = _default_pixel_pitch

    with open(geometry_yaml) as fi:
        geo = yaml.full_load(fi)

    if 'multitile_layout_version' in geo.keys():
        # Adapted from: https://github.com/larpix/larpix-v2-testing-scripts/blob/master/event-display/evd_lib.py

        pixel_pitch = geo['pixel_pitch']

        chip_channel_to_position = geo['chip_channel_to_position']
        tile_orientations = geo['tile_orientations']
        tile_positions = geo['tile_positions']
        tile_indeces = geo['tile_indeces']
        xs = np.array(list(chip_channel_to_position.values()))[
            :, 0] * pixel_pitch
        ys = np.array(list(chip_channel_to_position.values()))[
            :, 1] * pixel_pitch
        x_size = max(xs)-min(xs)+pixel_pitch
        y_size = max(ys)-min(ys)+pixel_pitch

        tile_geometry = defaultdict(int)
        io_group_io_channel_to_tile = {}
        geometry = defaultdict(_default_pxy)

        for tile in geo['tile_chip_to_io']:
            tile_orientation = tile_orientations[tile]
            tile_geometry[tile] = tile_positions[tile], tile_orientations[tile]
            for chip in geo['tile_chip_to_io'][tile]:
                io_group_io_channel = geo['tile_chip_to_io'][tile][chip]
                io_group = io_group_io_channel//1000
                io_channel = io_group_io_channel % 1000
                io_group_io_channel_to_tile[(
                    io_group, io_channel)] = tile

            for chip_channel in geo['chip_channel_to_position']:
                chip = chip_channel // 1000
                channel = chip_channel % 1000
                try:
                    io_group_io_channel = geo['tile_chip_to_io'][tile][chip]
                except KeyError:
                    print("Chip %i on tile %i not present in network" %
                          (chip, tile))
                    continue

                io_group = io_group_io_channel // 1000
                io_channel = io_group_io_channel % 1000
                x = chip_channel_to_position[chip_channel][0] * \
                    pixel_pitch + pixel_pitch / 2 - x_size / 2
                y = chip_channel_to_position[chip_channel][1] * \
                    pixel_pitch + pixel_pitch / 2 - y_size / 2

                x, y = _rotate_pixel((x, y), tile_orientation)
                x += tile_positions[tile][2] 
                y += tile_positions[tile][1] 

                geometry[(io_group, io_group_io_channel_to_tile[(
                    io_group, io_channel)], chip, channel)] = x, y

        xmin = min(np.array(list(geometry.values()))[:, 0])-pixel_pitch/2
        xmax = max(np.array(list(geometry.values()))[:, 0])+pixel_pitch/2
        ymin = min(np.array(list(geometry.values()))[:, 1])-pixel_pitch/2
        ymax = max(np.array(list(geometry.values()))[:, 1])+pixel_pitch/2

        tile_vertical_lines = np.linspace(xmin, xmax, 3)
        tile_horizontal_lines = np.linspace(ymin, ymax, 11)
        chip_vertical_lines = np.linspace(xmin, xmax, 33)
        chip_horizontal_lines = np.linspace(ymin, ymax, 101)


        # Plot metrics

        fig, ax = plt.subplots(2, 2, figsize=(30, 40))

        uniques = np.array(list(d.keys()))
        for io_group in range(1, 5):

            mask = unique_to_io_group(uniques) == io_group

            print('Getting {} for io_group {}'.format(metric, io_group))
            d_keys = uniques[mask]
            print('\tNumber of channels: ', len(d_keys))

            ax[(io_group-1) % 2, (io_group-1)//2].set_xlabel('X Position [mm]')
            ax[(io_group-1) % 2, (io_group-1)//2].set_ylabel('Y Position [mm]')

            ax[(io_group-1) % 2, (io_group-1) //
                2].set_xlim(xmin*1.05, xmax*1.05)
            ax[(io_group-1) % 2, (io_group-1) //
                2].set_ylim(ymin*1.05, ymax*1.05)

            for vl in tile_vertical_lines:
                ax[(io_group-1) % 2, (io_group-1)//2].vlines(x=vl, ymin=ymin, ymax=ymax,
                                                                colors=['k'], linestyle='dashed')
            for hl in tile_horizontal_lines:
                ax[(io_group-1) % 2, (io_group-1)//2].hlines(y=hl, xmin=xmin, xmax=xmax,
                                                                colors=['k'], linestyle='dashed')
            for vl in chip_vertical_lines:
                ax[(io_group-1) % 2, (io_group-1)//2].vlines(x=vl, ymin=ymin, ymax=ymax,
                                                                colors=['k'], linestyle='dotted')
            for hl in chip_horizontal_lines:
                ax[(io_group-1) % 2, (io_group-1)//2].hlines(y=hl, xmin=xmin, xmax=xmax,
                                                                colors=['k'], linestyle='dotted')

            ax[(io_group-1) % 2, (io_group-1)//2].set_aspect('equal')

            plt.text(0.95, 1.01, 'LArPix', ha='center',
                     va='center', transform=ax[(io_group-1) % 2, (io_group-1)//2].transAxes)

            for key in d_keys:
                channel_id = unique_to_channel_id(key)
                chip_id = unique_to_chip_id(key)
                tile = unique_to_tiles(key) + 10 * (io_group - 1))

                if chip_id not in range(11, 171):
                    continue
                if channel_id not in range(64):
                    continue

                x, y = geometry[(io_group, tile, chip_id, channel_id)]                
                pitch = pixel_pitch

                weight = d[key][metric]/normalization

                if weight > 1.0:
                    weight = 1.0

                r = Rectangle((x-(pitch/2.), y-(pitch/2.)),
                              pitch, pitch, color=cmap(weight))
                ax[(io_group-1) % 2, (io_group-1)//2].add_patch(r)

            colorbar = fig.colorbar(cm.ScalarMappable(norm=Normalize(
                vmin=0, vmax=normalization), cmap=cmap), ax=ax[(io_group-1) % 2, (io_group-1)//2])
            ax[(io_group-1) % 2, (io_group-1) //
                2].set_title('io_group = ' + str(io_group))
            if metric == 'mean':
                colorbar.set_label('[ADC]')
            if metric == 'std':
                colorbar.set_label('[ADC]')
            if metric == 'rate':
                colorbar.set_label('[Hz]')
        print('Saving...')
        plt.savefig('fsd-xy-'+metric+'.png')
        plt.close()
        print('Saved to: fsd-xy-'+metric+'.png')


def main(filename=_default_filename,
         geometry_yaml=_default_geometry_yaml,
         metric=_default_metric,
         max=_default_max_entries,
         ** kwargs):

    d = parse_file(filename, max)

    if "mean" in metric:
        normalization = 50
        plot_xy(d, "mean", geometry_yaml, normalization)
        # plot_1d(d, "mean")

    if "std" in metric:
        normalization = 5
        plot_xy(d, "std", geometry_yaml, normalization)
        # plot_1d(d, "std")

    if "rate" in metric:
        normalization = 10
        plot_xy(d, "rate", geometry_yaml, normalization)
        # plot_1d(d, "rate")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--filename', default=_default_filename,
                        type=str, help='''HDF5 fielname''')
    parser.add_argument('--geometry_yaml', default=_default_geometry_yaml, type=str,
                        help='''geometry yaml file (layout 2.4.0 for LArPix-v2a 10x10 tile)''')
    parser.add_argument('--metric', default=_default_metric, type=str,
                        help='''metric to plot; options: 'mean', 'std', 'rate', or any combination e.g. 'mean,std' ''')
    parser.add_argument('--max', default=_default_max_entries, type=int,
                        help='''max entries to process (default: -1)''')
    args = parser.parse_args()

    main(**vars(args))
