from base import utility_base
from base import network_base_FSD
from base import uart_base
from base import asic_base
from base import pacman_base
import stepbystep_v2d_5x5
# from timebudget import timebudget

# @timebudget


def reset_uarts(c, chip_key, verbose):
    c[chip_key].config.enable_piso_downstream = [0]*4
    c.write_configuration(chip_key, 'enable_piso_downstream')
    if verbose:
        print(chip_key, ' PISO DS ', c[chip_key].config.enable_piso_downstream)
    c[chip_key].config.enable_piso_upstream = [0]*4
    c.write_configuration(chip_key, 'enable_piso_upstream')
    if verbose:
        print(chip_key, ' PISO US ', c[chip_key].config.enable_piso_upstream)
    c[chip_key].config.enable_posi = [1]*4
    c.write_configuration(chip_key, 'enable_posi')
    if verbose:
        print(chip_key, ' POSI ', c[chip_key].config.enable_posi)
    return


# @timebudget
def setup_parent_piso(c, io, parent, daughter, verbose, tx_diff, tx_slice):
    enable_parent_piso_us(c,  parent, daughter, verbose, tx_diff, tx_slice)
    pacman_base.enable_pacman_uart_from_io_channel(io, parent.io_group, parent.io_channel)
    # ok, diff = c.enforce_configuration(parent, timeout=0.001, connection_delay=0.005, n=10, n_verify=3)
    ok, diff = utility_base.reconcile_configuration(
        c, parent, verbose, timeout=0.02, connection_delay=0.01, n=3, n_verify=2)
    # ok, diff = utility_base.reconcile_configuration(
    #     c, parent, verbose)
    #ok, diff = utility_base.simple_reconcile_configuration(
    #    c, parent)

    return ok, diff


# @timebudget
def setup_daughter(c, io, parent, daughter, verbose, asic_version,
                   ref_current_trim, tx_diff, tx_slice, r_term, i_rx):
    daughter = network_base_FSD.configure_chip_id(c, parent.io_group,
                                                  parent.io_channel,
                                                  daughter.chip_id, asic_version)
    uart_base.enable_daughter_posi(c, parent, daughter, verbose, r_term, i_rx)
    asic_base.set_ref_current_trim(c, daughter, ref_current_trim)
    piso = uart_base.enable_daughter_piso(c, parent, daughter, verbose,
                                          tx_diff, tx_slice)
    asic_base.disable_chip_csa_trigger(c, daughter)
    uart_base.enable_parent_posi(c, parent, daughter, verbose, r_term, i_rx)
    # print('CHECK')
    # stepbystep_v2d_5x5.read(c, parent, 'chip_id')
    # stepbystep_v2d_5x5.read(c, daughter, 'chip_id')
    # ok, diff = c.enforce_configuration(daughter, timeout=0.001, connection_delay=0.005, n=10, n_verify=3)
    ok, diff = utility_base.reconcile_configuration(
        c, daughter, verbose, timeout=0.02, connection_delay=0.01, n=3, n_verify=2)
    #ok, diff = utility_base.simple_reconcile_configuration(
    #    c, daughter)
    # print(diff)
    return ok, diff, piso


# @timebudget
def enable_parent_piso_us(c, parent, daughter, verbose, tx_diff, tx_slice):
    if parent.chip_id - daughter.chip_id == 10:
        piso = 3
    if parent.chip_id - daughter.chip_id == -10:
        piso = 1
    if parent.chip_id - daughter.chip_id == -1:
        piso = 0
    if parent.chip_id - daughter.chip_id == 1:
        piso = 2
    if verbose:
        print('PARENT ', parent, '\tDAUGHTER ', daughter,
              '==>\t enable PISO US ', piso)
    registers_to_write = []
    setattr(c[parent].config, f'i_tx_diff{piso}', tx_diff)
    registers_to_write.append(
        c[parent].config.register_map[f'i_tx_diff{piso}'])
    setattr(c[parent].config, f'tx_slices{piso}', tx_slice)
    registers_to_write.append(
        c[parent].config.register_map[f'tx_slices{piso}'])
    for reg in registers_to_write:
        c.write_configuration(parent, reg)
    c[parent].config.enable_piso_upstream[piso] = 1
    c.write_configuration(parent, 'enable_piso_upstream')
    if verbose:
        print(c[parent].config.enable_piso_upstream)
    return


# @timebudget
def disable_parent_piso_us(c, parent, daughter, verbose, tx_diff, tx_slice):
    if parent.chip_id - daughter.chip_id == 10:
        piso = 3
    if parent.chip_id - daughter.chip_id == -10:
        piso = 1
    if parent.chip_id - daughter.chip_id == -1:
        piso = 0
    if parent.chip_id - daughter.chip_id == 1:
        piso = 2
    if verbose:
        print('PARENT ', parent, '\tDAUGHTER ', daughter,
              '==>\t disable PISO US ', piso)
    c[parent].config.enable_piso_upstream[piso] = 0
    c.write_configuration(parent, 'enable_piso_upstream')
    if verbose:
        print(c[parent].config.enable_piso_upstream)
    registers_to_write = []
    setattr(c[parent].config, f'i_tx_diff{piso}', tx_diff)
    registers_to_write.append(
        c[parent].config.register_map[f'i_tx_diff{piso}'])
    setattr(c[parent].config, f'tx_slices{piso}', tx_slice)
    registers_to_write.append(
        c[parent].config.register_map[f'tx_slices{piso}'])
    for reg in registers_to_write:
        c.write_configuration(parent, reg)
    return


# @timebudget
def enable_parent_posi(c, parent, daughter, verbose, r_term, i_rx):
    if parent.chip_id - daughter.chip_id == 10:
        posi = 0
    if parent.chip_id - daughter.chip_id == -10:
        posi = 2
    if parent.chip_id - daughter.chip_id == -1:
        posi = 1
    if parent.chip_id - daughter.chip_id == 1:
        posi = 3
    if verbose:
        print('PARENT ', parent, '\tdaughter ',
              daughter, '==>\t enable POSI ', posi)
    if verbose:
        print(c[parent].config.enable_posi)
    registers_to_write = []
    setattr(c[parent].config, f'r_term{posi}', r_term)
    registers_to_write.append(c[parent].config.register_map[f'r_term{posi}'])
    setattr(c[parent].config, f'i_rx{posi}', i_rx)
    registers_to_write.append(c[parent].config.register_map[f'i_rx{posi}'])
    for reg in registers_to_write:
        c.write_configuration(parent, reg)
    c[parent].config.enable_posi[posi] = 1
    c.write_configuration(parent, 'enable_posi')
    if verbose:
        print(c[parent].config.enable_posi)
    return


# @timebudget
def enable_daughter_posi(c, parent, daughter, verbose, r_term, i_rx):
    if parent.chip_id - daughter.chip_id == 10:
        posi = 2
    if parent.chip_id - daughter.chip_id == -10:
        posi = 0
    if parent.chip_id - daughter.chip_id == -1:
        posi = 3
    if parent.chip_id - daughter.chip_id == 1:
        posi = 1
    if verbose:
        print('PARENT ', parent, '\tDAUGHTER ', daughter,
              '==>\t enable POSI ', posi)
    registers_to_write = []
    setattr(c[daughter].config, f'r_term{posi}', r_term)
    registers_to_write.append(c[daughter].config.register_map[f'r_term{posi}'])
    setattr(c[daughter].config, f'i_rx{posi}', i_rx)
    registers_to_write.append(c[daughter].config.register_map[f'i_rx{posi}'])
    for reg in registers_to_write:
        c.write_configuration(parent, reg)
    c[daughter].config.enable_posi = [0]*4
    c[daughter].config.enable_posi[posi] = 1
    c.write_configuration(daughter, 'enable_posi')
    if verbose:
        print(c[daughter].config.enable_posi)

    return


# @timebudget
def disable_parent_posi(c, parent, daughter, verbose):
    if parent.chip_id - daughter.chip_id == 10:
        posi = 0
    if parent.chip_id - daughter.chip_id == -10:
        posi = 2
    if parent.chip_id - daughter.chip_id == -1:
        posi = 1
    if parent.chip_id - daughter.chip_id == 1:
        posi = 3
    if verbose:
        print('PARENT ', parent, '\tdaughter ',
              daughter, '==>\t disable POSI ', posi)
    posi_list = c[parent].config.enable_posi  # !!!!
    if posi_list.count(1) == 1:  # !!!!
        c[parent].config.enable_posi = [1]*4  # !!!!
        c[parent].config.enable_posi[posi] = 0  # !!!!
    else:
        c[parent].config.enable_posi[posi] = 0
    c.write_configuration(parent, 'enable_posi')
    if verbose:
        print(c[parent].config.enable_posi)
    return


# @timebudget
def enable_daughter_piso(c, parent, daughter, verbose, tx_diff, tx_slice):
    c[daughter].config.enable_piso_upstream = [0]*4
    c.write_configuration(daughter, 'enable_piso_upstream')
    if parent.chip_id - daughter.chip_id == 10:
        piso = 1
    if parent.chip_id - daughter.chip_id == -10:
        piso = 3
    if parent.chip_id - daughter.chip_id == -1:
        piso = 2
    if parent.chip_id - daughter.chip_id == 1:
        piso = 0
    if verbose:
        print('parent ', parent, '\tDAUGHTER ', daughter,
              '==>\t PISO DS ', piso)
    registers_to_write = []
    setattr(c[daughter].config, f'i_tx_diff{piso}', tx_diff)
    registers_to_write.append(
        c[daughter].config.register_map[f'i_tx_diff{piso}'])
    setattr(c[daughter].config, f'tx_slices{piso}', tx_slice)
    registers_to_write.append(
        c[daughter].config.register_map[f'tx_slices{piso}'])
    for reg in registers_to_write:
        c.write_configuration(parent, reg)

    c[daughter].config.enable_piso_downstream = [0]*4
    c[daughter].config.enable_piso_downstream[piso] = 1
    c.write_configuration(daughter, 'enable_piso_downstream')
    if verbose:
        print(c[daughter].config.enable_piso_downstream)
    return piso
