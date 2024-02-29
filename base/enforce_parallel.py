import larpix
from tqdm import tqdm
from base import pacman_base
from base import utility_base
import numpy as np
from copy import deepcopy


def get_chips_by_io_group_io_channel(network_config, tiles=None, use_keys=None):
    
    dc = larpix.Controller()
    dc.load(network_config)
    all_keys = []
    for io_group, io_channels in dc.network.items():
        for io_channel in io_channels:
            if not tiles is None:
                if not utility_base.io_channel_to_tile(io_channel) in tiles: continue
            keys = dc.get_network_keys(io_group, io_channel, root_first_traversal=True)
            
            if not use_keys is None:
                remove = []
                for key in keys:
                    if not key in use_keys: remove.append(key)
                for key in remove:
                    keys.remove(key)

            all_keys.append(keys)

    return all_keys

def enforce_parallel(c, network_keys, unmask_last=True):
    
    ichip = -1  
    nchip = sum([len(net) for net in network_keys])
    p_bar = tqdm(range(nchip))
    p_bar.refresh()
    ok, diff = False, {}
    masks={}
   
    unconfigured = deepcopy(network_keys)

    while True:
        current_chips = []
        ichip += 1
        working=False
        for inet, net in enumerate(network_keys):
            if ichip>=len(net): continue
            working=True
            current_chips.append(net[ichip])
            unconfigured[inet].remove(net[ichip])

        if unmask_last:
            for chip in current_chips:
                masks[str(chip)] = c[chip].config.channel_mask
                c[chip].config.channel_mask = [1]*64

        if not working: break

        ok, diff = c.enforce_configuration(current_chips, timeout=0.01, connection_delay=0.001, n=10, n_verify=3)
        
        if not ok: 
            p_bar.update(len(current_chips) - len(diff.keys()))
            p_bar.refresh() 
            return ok, diff, unconfigured
        
        p_bar.update(len(current_chips))
        p_bar.refresh()

    p_bar.close()

    N_WRITE_UNMASK=10 

    send = False
    if unmask_last:
        for chip in reversed(list(masks.keys())):
            c[chip].config.channel_mask = masks[chip]
            if send==False:
                if np.any( np.logical_not(masks[chip])): send=True
        
        if send:
            for __ in range(N_WRITE_UNMASK):
                c.multi_write_configuration( [ (chip, range(131, 139)) for chip in c.chips ], connection_delay=0.001 )

    return ok, diff, unconfigured


