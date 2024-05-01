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
import merge_disabled_to_config
import time
import tqdm

_default_filename=None
_default_min_rate=0.01
_default_max_rate=1.

def datetime_now():
	''' Return string with year, month, day, hour, minute '''
	return time.strftime("%Y_%m_%d_%H_%M_%Z")

def unique_channel_id(d):
    return ((d['io_group'].astype(int)*1000+d['io_channel'].astype(int))*1000 \
            + d['chip_id'].astype(int))*100 + d['channel_id'].astype(int)

def unique_to_channel_id(unique):
    return unique % 100

def unique_to_chip_id(unique):
    return (unique// 100) % 1000

def unique_to_io_channel(unique):
    return(unique//(100*1000)) % 1000

def unique_to_tiles(unique):
    return ( (unique_to_io_channel(unique)-1) // 4) + 1

def unique_to_io_group(unique):
    return(unique // (100*1000*1000)) % 1000

def parse_file(filename, max_entries=-1):
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
    for chip in tqdm.tqdm(range(11, 111), desc='parsing data...'):
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


def toggle_trims_write_increments(d,min_rate,max_rate,file):
    nonrouted_v2a_channels=[6,7,8,9,22,23,24,25,38,39,40,54,55,56,57]
    routed_v2a_channels=[i for i in range(64) if i not in nonrouted_v2a_channels]
    
    io_groups = set(unique_to_io_group( np.array(list(d.keys())) ))
    tiles = set(unique_to_tiles( np.array(list(d.keys())) ))

    toggle_list = {}
    
    timestamp=datetime_now()
    fname='toggle-'+timestamp+'.json'
    toggle_list['meta']={ \
            fname : {\
            'min_rate'   : min_rate,
            'max_rate'   : max_rate,
            'created': timestamp,
            'datafile' : file
            }
        }

    for io_group in io_groups:
        for tile in tiles:
            nchan=0
            mask = unique_to_io_group( np.array(list(d.keys())) ) == io_group
            mask = np.logical_and(mask, unique_to_tiles( np.array(list(d.keys())) )==tile )
            
            if not np.any(mask): continue
            
            d_keys = np.array(list(d.keys()) )[mask]

            for key in d_keys:
                channel_id = unique_to_channel_id(key)
                chip_id = unique_to_chip_id(key)
                if chip_id not in range(11,111): continue
                if channel_id not in range(64): continue
                weight = d[key]["rate"]
                if weight>max_rate:
                    tog = int(np.log10(weight-max_rate))+1
                    key='{}-{}-{}'.format(io_group, tile, chip_id)
                    if not key in toggle_list.keys(): toggle_list[key]=[]
                    toggle_list[key].append( (int(channel_id), tog) )
                    nchan+=1
                elif weight<min_rate:
                    key='{}-{}-{}'.format(io_group, tile, chip_id)
                    if not key in toggle_list.keys(): toggle_list[key]=[]
                    toggle_list[key].append( (int(channel_id), -1) )
                    nchan+=1

            print('Number of channels toggled on tile {}-{}: {}'.format(io_group, tile, nchan))
    
    with open(fname, 'w') as f:
        json.dump(toggle_list, f, indent=4)

    print('Toggle list written to {}'.format(fname))
    return

def main(filename=_default_filename,
         min_rate=_default_min_rate,
         max_rate=_default_max_rate,
         **kwargs):

    d = parse_file( filename )

    toggle_trims_write_increments( d, min_rate, max_rate, filename)

    
if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--filename', default=_default_filename, type=str, help='''HDF5 fielname''')
    parser.add_argument('--max_rate', default=_default_max_rate, type=float, help='''Maximum pixel target rate [Hz] ''')
    parser.add_argument('--min_rate', default=_default_min_rate, type=float, help='''Minimum pixel target rate [Hz]''')
    
    args = parser.parse_args()
    main(**vars(args))

