import larpix
from tqdm import tqdm
from base import utility_base
from copy import deepcopy
import numpy as np

tag_to_config_map = {

        'MOD1' : 0,
        'MOD2' : 1,
        'MOD3' : 2,
        'MOD4' : 3,
         None  : 0

}

def get_chips_by_io_group_io_channel(network_config, tiles=None, use_keys=None):

    dc = larpix.Controller()
    dc.load(network_config)
    all_keys = []
    for io_group, io_channels in dc.network.items():
        for io_channel in io_channels:
            if not tiles is None:
                if not utility_base.io_channel_to_tile(io_channel) in tiles:
                    continue
            keys = dc.get_network_keys(
                io_group, io_channel, root_first_traversal=True)

            if not use_keys is None:
                remove = []
                for key in keys:
                    if not key in use_keys:
                        remove.append(key)
                for key in remove:
                    keys.remove(key)

            all_keys.append(keys)

    return all_keys


def enforce_parallel(c, network_keys, unmask_last=True, pbar_desc='p', pbar_position=0):

    ichip = -1

    chips = [network_keys[net][inet]
             for net in range(len(network_keys)) for inet in range(len(network_keys[net]))]

    nchip = sum([len(net) for net in network_keys])
    p_bar = tqdm(range(nchip))
    p_bar.refresh()
    ok, diff = False, {}
    masks = {}
    csas={}
    ptmasks={}
    
    unconfigured = deepcopy(network_keys)

    while True:
        current_chips = []
        ichip += 1
        working = False
        for inet, net in enumerate(network_keys):
            if ichip >= len(net):
                continue
            working = True
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

        if not working:
            break

        ok, diff = c.enforce_configuration(
            current_chips, timeout=0.1, connection_delay=0.1, n=5, n_verify=3)

        # ok, diff = c.verify_configuration(
        #     current_chips, timeout=0.02, connection_delay=0.01, n=5)#, msg_length=len(current_chips))

        if not ok:
            print('Re-configuring: ', diff)
            ok, diff = c.enforce_configuration(
                diff.keys(), timeout=0.2, connection_delay=0.1, n=15, n_verify=3)
            if not ok:
                print('Could not configure: ', diff)
                break

                # ok, diff = c.enforce_configuration(
                #     diff.keys(), timeout=0.02, connection_delay=0.01, n=15, n_verify=5, msg_length=1)
        # ok, diff = c.enforce_configuration(
        #     current_chips, timeout=0.01, connection_delay=0.001, n=12, n_verify=4)

        # ok, diff = utility_base.simple_reconcile_configuration(
        #     c, current_chips)

        # for cc in current_chips:
        #     print(cc)
        #     for reg in range(c[current_chips[0]].config.num_registers):
        #         print(reg)

        #         ok, diff = c.enforce_registers(
        #             [(cc, reg)], timeout=0.01, connection_delay=0.001, n=15, n_verify=15)
        #         if not ok:
        #             break
        # if not ok:
            # raise RuntimeError('Enforcing failed', diff)
            # print(diff)
            # return ok, diff, unconfigured
        p_bar.update(len(current_chips))
        p_bar.refresh()

    p_bar.close()

    N_WRITE_UNMASK = 10

    send = False
    if unmask_last:
        for chip in reversed(list(masks.keys())):
            c[chip].config.channel_mask = masks[chip]
            c[chip].config.csa_enable = csas[chip]
            c[chip].config.periodic_trigger_mask = ptmasks[chip]
            if send==False:
                if np.any( np.logical_not(masks[chip])): send=True
        
        if send:
            if True:
                for __ in range(N_WRITE_UNMASK):
                    for chip in c.chips:
                        if chip.io_group in c.io._io_group_table.keys():
                            c.multi_write_configuration( [ (chip, list(range(131, 139))+list(range(66, 74))+list(range(155,163)))])

            else:
                pass
    return ok, diff, unconfigured
