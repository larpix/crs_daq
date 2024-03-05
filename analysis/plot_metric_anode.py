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
_default_geometry_yaml = 'analysis/multi_tile_layout-2.3.16.yaml'

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
    print("number of packets in parsed files =", len(unique_id))
    for i in tqdm.tqdm(unique_id_set):
        id_mask = unique_id == i
        masked_adc = adc[id_mask]
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
        tpc_centers = geo['tpc_centers']
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
                x += tile_positions[tile][2] + \
                    tpc_centers[tile_indeces[tile][0]][0]
                y += tile_positions[tile][1] + \
                    tpc_centers[tile_indeces[tile][0]][1]

                geometry[(io_group, io_group_io_channel_to_tile[(
                    io_group, io_channel)], chip, channel)] = x, y

        xmin = min(np.array(list(geometry.values()))[:, 0])-pixel_pitch/2
        xmax = max(np.array(list(geometry.values()))[:, 0])+pixel_pitch/2
        ymin = min(np.array(list(geometry.values()))[:, 1])-pixel_pitch/2
        ymax = max(np.array(list(geometry.values()))[:, 1])+pixel_pitch/2

        tile_vertical_lines = np.linspace(xmin, xmax, 3)
        tile_horizontal_lines = np.linspace(ymin, ymax, 5)
        chip_vertical_lines = np.linspace(xmin, xmax, 21)
        chip_horizontal_lines = np.linspace(ymin, ymax, 41)

        nonrouted_v2a_channels = [6, 7, 8, 9, 22,
                                  23, 24, 25, 38, 39, 40, 54, 55, 56, 57]
        routed_v2a_channels = [i for i in range(
            64) if i not in nonrouted_v2a_channels]

        io_groups = set(unique_to_io_group(np.array(list(d.keys()))))
        tiles = set(unique_to_tiles(np.array(list(d.keys()))))

        fig, ax = plt.subplots(2, 4, figsize=(40, 30))

        for io_group in range(1, 9):

            mask = unique_to_io_group(np.array(list(d.keys()))) == io_group

            print('Getting {} for io_group {}'.format(metric, io_group))
            d_keys = np.array(list(d.keys()))[mask]
            print(len(d_keys))

            ax[(io_group-1) % 2, (io_group-1)//2].set_xlabel('X Position [mm]')
            ax[(io_group-1) % 2, (io_group-1)//2].set_ylabel('Y Position [mm]')

            ax[(io_group-1) % 2, (io_group-1)//2].set_xlim(xmin*1.05, xmax*1.05)
            ax[(io_group-1) % 2, (io_group-1)//2].set_ylim(ymin*1.05, ymax*1.05)
            ax[(io_group-1) % 2, (io_group-1)//2].set_aspect('equal')

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
            plt.text(0.95, 1.01, 'LArPix', ha='center',
                     va='center', transform=ax[(io_group-1) % 2, (io_group-1)//2].transAxes)

            for key in d_keys:
                channel_id = unique_to_channel_id(key)
                chip_id = unique_to_chip_id(key)
                tile = unique_to_tiles(key)
                io_group = unique_to_io_group(key)
                if chip_id not in range(11, 111):
                    continue
                if channel_id in nonrouted_v2a_channels:
                    continue
                if channel_id not in range(64):
                    continue

                x, y = geometry[(io_group, (io_group-1)*8 +
                                 tile, chip_id, channel_id)]
                weight = d[key][metric]/normalization
                if weight > 1.0:
                    weight = 1.0
                r = Rectangle((x-(pixel_pitch/2.), y-(pixel_pitch/2.)),
                              pixel_pitch, pixel_pitch, color=cmap(weight))
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

        plt.savefig('2x2-xy-'+metric+'.png')
        plt.close()
    else:
        chip_pix = dict([(chip_id, pix) for chip_id, pix in geo['chips']])
        vertical_lines = np.linspace(-1*(geo['width']/2), geo['width']/2, 11)
        horizontal_lines = np.linspace(-1 *
                                       (geo['height']/2), geo['height']/2, 11)

        nonrouted_v2a_channels = [6, 7, 8, 9, 22,
                                  23, 24, 25, 38, 39, 40, 54, 55, 56, 57]
        routed_v2a_channels = [i for i in range(
            64) if i not in nonrouted_v2a_channels]

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

                print('Getting {} for tile {}'.format(metric, tile_id))
                d_keys = np.array(list(d.keys()))[mask]
                print(len(d_keys))

                fig, ax = plt.subplots(figsize=(10, 8))
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
                plt.text(0.95, 1.01, 'LArPix', ha='center',
                         va='center', transform=ax.transAxes)

                chipid_pos = dict()
                for chipid in chip_pix.keys():
                    x, y = [[] for i in range(2)]
                    for channelid in routed_v2a_channels:
                        x.append(geo['pixels'][chip_pix[chipid][channelid]][1])
                        y.append(geo['pixels'][chip_pix[chipid][channelid]][2])
                    avgX = (max(x)+min(x))/2.
                    avgY = (max(y)+min(y))/2.
                    chipid_pos[chipid] = dict(minX=min(x), maxX=max(
                        x), avgX=avgX, minY=min(y), maxY=max(y), avgY=avgY)
                    plt.annotate(
                        str(chipid), [avgX, avgY], ha='center', va='center')

                for key in d_keys:
                    channel_id = unique_to_channel_id(key)
                    chip_id = unique_to_chip_id(key)
                    if chip_id not in range(11, 111):
                        continue
                    if channel_id in nonrouted_v2a_channels:
                        continue
                    if channel_id not in range(64):
                        continue
                    x = geo['pixels'][chip_pix[chip_id][channel_id]][1]
                    y = geo['pixels'][chip_pix[chip_id][channel_id]][2]
                    weight = d[key][metric]/normalization
                    if weight > 1.0:
                        weight = 1.0
                    r = Rectangle((x-(pixel_pitch/2.), y-(pixel_pitch/2.)),
                                  pixel_pitch, pixel_pitch, color='k', alpha=weight)
                    plt.gca().add_patch(r)

                colorbar = fig.colorbar(cm.ScalarMappable(norm=Normalize(
                    vmin=0, vmax=normalization), cmap='Greys'), ax=ax)

                if metric == 'mean':
                    ax.set_title('Tile ID '+tile_id+'\nADC Mean')
                    colorbar.set_label('[ADC]')
                    plt.tight_layout()
                    plt.savefig('tile-id-'+str(tile_id)+'-xy-mean.png')
                    plt.close()
                if metric == 'std':
                    ax.set_title('Tile ID '+tile_id+'\nADC RMS')
                    colorbar.set_label('[ADC]')
                    plt.tight_layout()
                    plt.savefig('tile-id-'+str(tile_id)+'-xy-std.png')
                    plt.close()
                if metric == 'rate':
                    ax.set_title('Tile ID '+tile_id+'\nTrigger Rate')
                    colorbar.set_label('[Hz]')
                    plt.tight_layout()
                    plt.savefig('tile-id-'+str(tile_id)+'-xy-rate.png')
                    plt.close()


def main(filename=_default_filename,
         geometry_yaml=_default_geometry_yaml,
         metric=_default_metric,
         max=_default_max_entries,
         ** kwargs):

    d = parse_file(filename, max)

    if "mean" in metric:
        normalization = 50
        plot_xy(d, "mean", geometry_yaml, normalization)
        plot_1d(d, "mean")

    if "std" in metric:
        normalization = 5
        plot_xy(d, "std", geometry_yaml, normalization)
        plot_1d(d, "std")

    if "rate" in metric:
        normalization = 10
        plot_xy(d, "rate", geometry_yaml, normalization)
        plot_1d(d, "rate")


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
