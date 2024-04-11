import larpix
from tqdm import tqdm
from base import pacman_base
from base import utility_base
import numpy as np
from copy import deepcopy

tag_to_config_map = {

        'IOG1' : 0,
        'IOG2' : 1,
        'IOG3' : 2,
        'IOG4' : 3,
        'IOG5' : 4,
        'IOG6' : 5,
        'IOG7' : 6,
        'IOG8' : 7,
        'MOD0' : 0,
        'MOD1' : 1,
        'MOD2' : 2,
        'MOD3' : 3,
         None  : 0

}

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
                    if not key in use_keys : remove.append(key)
                for key in remove:
                    keys.remove(key)

            all_keys.append(keys)

    return all_keys

def enforce_parallel(c, network_keys, unmask_last=True, pbar_position=0, pbar_desc='configuring...'):
    
    ichip = -1  
    nchip = sum([len(net) for net in network_keys])
    p_bar = tqdm(range(nchip), position=pbar_position, desc=pbar_desc, maxinterval=0.25, leave=False)
    p_bar.refresh()
    ok, diff = False, {}
    masks={}
    csas={}
    ptmasks={}
   
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
               
                ptmasks[str(chip)] = c[chip].config.periodic_trigger_mask
                c[chip].config.periodic_trigger_mask = [1]*64

                csas[str(chip)] = c[chip].config.csa_enable
                c[chip].config.csa_enable = [0]*64

        if not working: break

        ok, diff = c.enforce_configuration(current_chips, timeout=0.018, connection_delay=0.01, n=80, n_verify=4)
        
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
            c[chip].config.csa_enable = csas[chip]
            c[chip].config.periodic_trigger_mask = ptmasks[chip]
            if send==False:
                if np.any( np.logical_not(masks[chip])): send=True
        
        if send:
            for __ in range(N_WRITE_UNMASK):
                c.multi_write_configuration( [ (chip, list(range(131, 139))+list(range(66, 74))+list(range(155,163))) for chip in c.chips ], connection_delay=0.001 )

    return ok, diff, unconfigured


