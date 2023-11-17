import larpix
from tqdm import tqdm

def get_chips_by_io_group_io_channel(network_config):
    
    dc = larpix.Controller()
    dc.load(network_config)
    all_keys = []
    for io_group, io_channels in dc.network.items():
        for io_channel in io_channels:
            keys = dc.get_network_keys(io_group, io_channel, root_first_traversal=True)
            all_keys.append(keys)

    return all_keys

def enforce_parallel(c, network_keys, unmask_last=True):
    
    ichip = -1 
    
    p_bar = tqdm(range(len(c.chips)))
    p_bar.refresh()
    ok, diff = False, {}
    masks={}
    while True:
        current_chips = []
        ichip += 1
        working=False
        for net in network_keys:
            if ichip>=len(net): continue
            working=True
            current_chips.append(net[ichip])
    
        if unmask_last:
            for chip in current_chips:
                masks[str(chip)] = c[chip].config.channel_mask
                c[chip].config.channel_mask = [1]*64

        if not working: break

        ok, diff = c.enforce_configuration(current_chips, timeout=0.01, connection_delay=0.1, n=20, n_verify=5)
        
        if not ok: 
            raise RuntimeError('Enforcing failed', diff)
            return ok, diff
        p_bar.update(len(current_chips))
        p_bar.refresh()

    p_bar.close()

    N_WRITE_UNMASK=5    

    if unmask_last:
        for chip in masks.keys():
            c[chip].config.channel_mask = masks[chip]
        c.enforce_registers( [ (chip, range(131, 139)) for chip in c.chips ], n_verify=1, n=N_WRITE_UNMASK, timeout=0.001 )

    return ok, diff


