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
import tqdm
from statistics import mean, mode, stdev

_default_filename = None

_default_geometry_yaml = 'analysis/layout-3.0.0.yaml'

_default_metric = ''

pitch = 3.8  # mm
periodic_trigger_cycles = 0.067 # MHz

verbose = True

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

def parse_file(filename, max_entries=-1):
    d = dict()
    f = h5py.File(filename, 'r')
    unixtime = f['packets'][:]['timestamp'][f['packets']
                                            [:]['packet_type'] == 4]
    livetime = np.max(unixtime)-np.min(unixtime)
    #data_mask = f['packets'][:]['packet_type'] == 1
    #valid_parity_mask = f['packets'][:]['valid_parity'] == 1
    #mask = np.logical_and(data_mask, valid_parity_mask)
    mask = f['packets'][:]['packet_type'] == 1
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

            # remove zero datawords
            non_zero_masked_adc = [x for x in masked_adc if x != 0]        

            d[i] = dict(
                mean=np.mean(non_zero_masked_adc),
                std=np.std(non_zero_masked_adc),
                rate=len(non_zero_masked_adc) / (livetime + 1e-9))

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
            if metric == 'rate':
                n_bins = 100

            # remove zero datawords before calculating the stdev
            non_zero_a = [x for x in a if x != 0]
            std_metric = stdev(non_zero_a)             
        
            if metric == 'mean':
                metric_name = 'adc_mean_per_channel'            
            elif metric == 'std':
                metric_name = 'adc_std_per_channel'               
            else:
                metric_name = 'data_rate_per_channel'
                
            mode_metric = mode(a)
            mean_metric = mean(a)            
            std_metric  = stdev(a) 
                    
            print(f"\n{metric_name }: mode ({mode_metric:.2f}), stdev ({std_metric:.2f}), mean ({mean_metric:.2f})")

            ax.hist(a, bins=np.linspace(min_bin, max_bin, n_bins))
            ax.grid(True)
            ax.set_ylabel('Channel Count')
            #ax.set_title('Tile ID '+str(tile_id))
            ax.set_title(f'Tile ID {tile_id} (mode = {mode_metric:.1f})')
            ax.set_yscale('log')
            plt.text(0.95, 1.01, 'LArPix', ha='center',
                         va='center', transform=ax.transAxes)        
            
            if metric == 'mean':
                ax.set_xlabel('ADC Mean')
                plt.savefig('tile-id-'+str(tile_id)+'-1d-mean.png')

                channel_count = 0
                mode_plus_3sd = mode_metric + 3 * std_metric
                mode_minus_3sd = mode_metric - 3 * std_metric
                for i in range(len(a)):
                    if a[i] > mode_metric + 50:
                        channel_count +=1
                    elif a[i] < mode_metric -50:
                        channel_count +=1
                            
                min_adc = min(num for num in a if num != 0)
                adc_range = max(a)-min_adc
                
                print(f"Non-Zero ADC means fall within range [{min_adc:.0f}, {max(a):.0f}]")
                if adc_range > 100:
                    print(f"3 stdev range = [{mode_minus_3sd:.0f}, {mode_plus_3sd:.0f}]")
                    print(f"####### Consider disabling {channel_count} channels w mean(adc) outside of range {mode_metric:.0f} +/- 50\n\n")
 
            if metric == 'std':
                ax.set_xlabel('ADC RMS')
                plt.savefig('tile-id-'+str(tile_id)+'-1d-std.png')
                plt.close()
            if metric == 'rate':             
                ax.set_xlabel('Trigger Rate [Hz]')
                plt.savefig('tile-id-'+str(tile_id)+'-1d-rate.png')
                plt.close()

                min_rate = min(num for num in a if num != 0)
                rate_range = max(a)-min_rate
                
                channel_count = 0
                for i in range(len(a)):
                    if a[i] > periodic_trigger_cycles:
                        channel_count +=1

                print(f"Non-Zero data rates fall within range [{min_rate:.2f}MHz, {max(a):.2f}MHz]")
                if channel_count > 0:
                    
                    print(f"####### Consider disabling {channel_count} channels w data rate > periodic_trigger_cycles ({periodic_trigger_cycles}MHz)\n")

def plot_xy(d, metric, geometry_yaml, normalization, filename):

    cmap = cm.viridis

    with open(geometry_yaml) as fi:
        geo = yaml.full_load(fi)
    chip_pix = dict([(chip_id+1, pix) for chip_id, pix in geo['chips']])
    vertical_lines = np.linspace(-1*(geo['width']/2), geo['width']/2, 17)
    horizontal_lines = np.linspace(-1*(geo['height']/2), geo['height']/2, 11)

    nonrouted_v2a_channels = []
    # nonrouted_v2a_channels = [6, 7, 8, 9, 22,
    #                           23, 24, 25, 38, 39, 40, 54, 55, 56, 57]
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

            #print('studying tile {}'.format(tile_id))
            d_keys = np.array(list(d.keys()))[mask]
            #print(len(d_keys))

            fig, ax = plt.subplots(figsize=(16, 10))
            ax.set_aspect('equal')
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
                plt.annotate(str(chipid), [avgX, avgY],
                             ha='center', va='center')

            for key in d_keys:
                channel_id = unique_to_channel_id(key)
                chip_id = unique_to_chip_id(key)
                if chip_id not in range(11, 171):
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
                r = Rectangle((x-(pitch/2.), y-(pitch/2.)),
                              pitch, pitch, color=cmap(weight))
                plt.gca().add_patch(r)

            colorbar = fig.colorbar(cm.ScalarMappable(norm=Normalize(
                vmin=0, vmax=normalization), cmap=cmap), ax=ax)

            if metric == 'mean':
                ax.set_title(filename+'\nTile ID '+tile_id+'\nADC Mean')
                colorbar.set_label('[ADC]')
                plt.savefig('tile-id-'+str(tile_id)+'-xy-mean.png')
                plt.close()
            if metric == 'std':
                ax.set_title(filename+'\nTile ID '+tile_id+'\nADC RMS')
                colorbar.set_label('[ADC]')
                plt.savefig('tile-id-'+str(tile_id)+'-xy-std.png')
                plt.close()
            if metric == 'rate':
                ax.set_title(filename+'\nTile ID '+tile_id+'\nTrigger Rate')
                colorbar.set_label('[Hz]')
                plt.savefig('tile-id-'+str(tile_id)+'-xy-rate.png')
                plt.close()

def main(filename=_default_filename,
         geometry_yaml=_default_geometry_yaml,
         metric=_default_metric,
         **kwargs):

    d = parse_file(filename)
    normalization = 50
    if metric == 'mean':
        normalization = 800
    if metric == 'std':
        normalization = 5
    if metric == 'rate':
        normalization = 4

    if metric == '':
        # plot all
        
        # mean
        normalization = 800
        metric = 'mean'
        plot_xy(d, metric, geometry_yaml, normalization, filename)
        #plot_1d(d, metric)
        
        # std
        normalization = 5
        metric = 'std'
        plot_xy(d, metric, geometry_yaml, normalization, filename)
        #plot_1d(d, metric)

        # rate
        normalization = 4
        metric = 'rate'
        plot_xy(d, metric, geometry_yaml, normalization, filename)
        #plot_1d(d, metric)

        metric = ''
        return

    plot_xy(d, metric, geometry_yaml, normalization, filename)

    plot_1d(d, metric)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--filename', default=_default_filename,
                        type=str, help='''HDF5 fielname''')
    parser.add_argument('--geometry_yaml', default=_default_geometry_yaml, type=str,
                        help='''geometry yaml file (layout 2.4.0 for LArPix-v2a 10x10 tile)''')
    parser.add_argument('--metric', default=_default_metric, type=str,
                        help='''metric to plot; options: 'mean', 'std', 'rate' ''')
    args = parser.parse_args()
    main(**vars(args))
