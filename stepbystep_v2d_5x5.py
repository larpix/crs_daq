import larpix
import larpix.io
# from base import utility_base
from base import pacman_base
import time


def read(c, key, param):
    c.reads = []
    c.read_configuration(key, param, timeout=0.1)
    message = c.reads[-1]
    for msg in message:
        if not isinstance(msg, larpix.packet.packet_v2.Packet_v2):
            continue
        if msg.packet_type not in [larpix.packet.packet_v2.Packet_v2.CONFIG_READ_PACKET]:
            continue
        print(msg)
        print(msg.register_data)
        # return msg.chip_id
    return 0


def conf_east(c, cm, ck, cadd, iog, iochan):
    HI_TX_DIFF = 0
    HTX_SLICE = 15
    HR_TERM = 2
    HI_RX = 8

# add second chip
    # set mother transceivers
    c.add_chip(ck, version='2d')
    c[cm].config.i_rx3 = HI_RX
    c.write_configuration(cm, 'i_rx3')
    c[cm].config.r_term3 = HR_TERM
    c.write_configuration(cm, 'r_term3')
    c[cm].config.i_tx_diff2 = HI_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff2')
    c[cm].config.tx_slices2 = HTX_SLICE
    c.write_configuration(cm, 'tx_slices2')
    c[cm].config.enable_piso_upstream[0] = 1  # [0,0,1,0]
    m_piso = c[cm].config.enable_piso_upstream
    # turn only one upstream port on during config
    c[cm].config.enable_piso_upstream = [1, 0, 0, 0]
    c.write_configuration(cm, 'enable_piso_upstream')
    # add new chip to network
    default_key = larpix.key.Key(iog, iochan, 1)  # '1-5-1'
    c.add_chip(default_key, version='2d')  # TODO, create v2c class
    #  - - rename to chip_id = 12
    c[default_key].config.chip_id = cadd
    c.write_configuration(default_key, 'chip_id')
    #  - - remove default chip id from the controller
    c.remove_chip(default_key)
    #  - - and add the new chip id
    print(ck)
    c[ck].config.chip_id = cadd
    c[ck].config.i_rx1 = HI_RX
    c.write_configuration(ck, 'i_rx1')
    c[ck].config.r_term1 = HR_TERM
    c.write_configuration(ck, 'r_term1')
    c[ck].config.enable_posi = [0, 0, 0, 1]  # ok
    c.write_configuration(ck, 'enable_posi')
    c[ck].config.enable_piso_upstream = [0, 0, 0, 0]
    c.write_configuration(ck, 'enable_piso_upstream')
    c[ck].config.i_tx_diff0 = HI_TX_DIFF
    c.write_configuration(ck, 'i_tx_diff0')
    c[ck].config.tx_slices0 = HTX_SLICE
    c.write_configuration(ck, 'tx_slices0')
    c[ck].config.enable_piso_downstream = [
        1, 1, 1, 1]  # krw adding May 8, 2023
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)
    c[ck].config.enable_piso_downstream = [
        0, 0, 1, 0]  # only one downstream port
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)
    # enable mother rx
    c[cm].config.enable_piso_upstream = m_piso
    c.write_configuration(cm, 'enable_piso_upstream')  # allow multi-upstream
    c[cm].config.enable_posi[1] = 1  # [0,1,0,1]
    c.write_configuration(cm, 'enable_posi')
    # c[cm].config.v_cm_lvds_tx0 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx0')
    # c[cm].config.v_cm_lvds_tx1 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx1')
    # c[cm].config.v_cm_lvds_tx2 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx2')
    # c[cm].config.v_cm_lvds_tx3 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx3')


def conf_north(c, cm, ck, cadd, iog, iochan):
    HI_TX_DIFF = 0
    HTX_SLICE = 15
    HR_TERM = 2
    HI_RX = 8

# add second chip
    # set mother transceivers
    c.add_chip(ck, version='2d')
    c[cm].config.i_rx0 = HI_RX
    c.write_configuration(cm, 'i_rx0')
    c[cm].config.r_term0 = HR_TERM
    c.write_configuration(cm, 'r_term0')
    c[cm].config.i_tx_diff3 = HI_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff3')
    c[cm].config.tx_slices3 = HTX_SLICE
    c.write_configuration(cm, 'tx_slices3')
    c[cm].config.enable_piso_upstream[3] = 1  # add new upstream port
    m_piso = c[cm].config.enable_piso_upstream  # remember upstream ports
    # turn only one upstream port on during config
    c[cm].config.enable_piso_upstream = [0, 0, 0, 1]
    c.write_configuration(cm, 'enable_piso_upstream')

    # add new chip to network
    default_key = larpix.key.Key(iog, iochan, 1)  # '1-5-1'
    c.add_chip(default_key, version='2d')  # TODO, create v2c class
    c[default_key].config.chip_id = cadd
    c.write_configuration(default_key, 'chip_id')
    c.remove_chip(default_key)
    print("adding ", ck)
    c[ck].config.chip_id = cadd
    c[ck].config.i_rx2 = HI_RX
    c.write_configuration(ck, 'i_rx2')
    c[ck].config.r_term2 = HR_TERM
    c.write_configuration(ck, 'r_term2')
    c[ck].config.enable_posi = [0, 0, 1, 0]
    c.write_configuration(ck, 'enable_posi')
    c[ck].config.enable_piso_upstream = [0, 0, 0, 0]
    c.write_configuration(ck, 'enable_piso_upstream')
    c[ck].config.i_tx_diff1 = HI_TX_DIFF
    c.write_configuration(ck, 'i_tx_diff1')
    c[ck].config.tx_slices1 = HTX_SLICE
    c.write_configuration(ck, 'tx_slices1')
    c[ck].config.enable_piso_downstream = [
        1, 1, 1, 1]  # krw adding May 8, 2023
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)
    c[ck].config.enable_piso_downstream = [0, 1, 0, 0]
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)
    # enable mother rx
    c[cm].config.enable_piso_upstream = m_piso
    c.write_configuration(cm, 'enable_piso_upstream')  # allow multi-upstream
    c[cm].config.enable_posi[0] = 1  # [0,1,0,1]
    c.write_configuration(cm, 'enable_posi')
    # c[cm].config.v_cm_lvds_tx0 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx0')
    # c[cm].config.v_cm_lvds_tx1 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx1')
    # c[cm].config.v_cm_lvds_tx2 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx2')
    # c[cm].config.v_cm_lvds_tx3 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx3')


def conf_south(c, cm, ck, cadd, iog, iochan):
    HI_TX_DIFF = 0
    HTX_SLICE = 15
    HR_TERM = 2
    HI_RX = 8

# add second chip
    # set mother transceivers rx2, tx1
    c.add_chip(ck, version='2d')
    c[cm].config.i_rx2 = HI_RX
    c.write_configuration(cm, 'i_rx2')
    c[cm].config.r_term2 = HR_TERM
    c.write_configuration(cm, 'r_term2')
    c[cm].config.i_tx_diff1 = HI_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff1')
    c[cm].config.tx_slices1 = HTX_SLICE
    c.write_configuration(cm, 'tx_slices1')
    c[cm].config.enable_piso_upstream[1] = 1
    m_piso = c[cm].config.enable_piso_upstream
    # turn only one upstream port on during config
    c[cm].config.enable_piso_upstream = [0, 1, 0, 0]
    c.write_configuration(cm, 'enable_piso_upstream')

    # add new chip to network
    default_key = larpix.key.Key(iog, iochan, 1)  # '1-5-1'
    c.add_chip(default_key, version='2d')  # TODO, create v2c class
    #  - - rename to chip_id = 12
    c[default_key].config.chip_id = cadd
    c.write_configuration(default_key, 'chip_id')
    #  - - remove default chip id from the controller
    c.remove_chip(default_key)
    #  - - and add the new chip id
    print(ck)
    c[ck].config.chip_id = cadd
    c[ck].config.i_rx0 = HI_RX  # rx0,tx3
    c.write_configuration(ck, 'i_rx0')
    c[ck].config.r_term0 = HR_TERM
    c.write_configuration(ck, 'r_term0')
    c[ck].config.enable_posi = [1, 0, 0, 0]
    c.write_configuration(ck, 'enable_posi')
    c[ck].config.enable_piso_upstream = [0, 0, 0, 0]
    c.write_configuration(ck, 'enable_piso_upstream')
    c[ck].config.i_tx_diff3 = HI_TX_DIFF
    c.write_configuration(ck, 'i_tx_diff3')
    c[ck].config.tx_slices3 = HTX_SLICE
    c.write_configuration(ck, 'tx_slices3')
    c[ck].config.enable_piso_downstream = [
        1, 1, 1, 1]  # krw adding May 8, 2023
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)
    c[ck].config.enable_piso_downstream = [0, 0, 0, 1]
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)
    # enable mother rx
    c[cm].config.enable_piso_upstream = m_piso
    c.write_configuration(cm, 'enable_piso_upstream')  # allow multi-upstream
    c[cm].config.enable_posi[2] = 1  # [0,1,0,1]
    c.write_configuration(cm, 'enable_posi')
    # c[cm].config.v_cm_lvds_tx0 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx0')
    # c[cm].config.v_cm_lvds_tx1 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx1')
    # c[cm].config.v_cm_lvds_tx2 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx2')
    # c[cm].config.v_cm_lvds_tx3 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx3')


def conf_root(c, cm, cadd, iog, iochan):
    I_TX_DIFF = 0
    TX_SLICE = 15
    R_TERM = 2
    I_RX = 8
    c.add_chip(cm, version='2d')
    #  - - default larpix chip_id is '1'
    default_key = larpix.key.Key(iog, iochan, 1)  # '1-5-1'
    c.add_chip(default_key, version='2d')  # TODO, create v2c class
    #  - - rename to chip_id = cm
    c[default_key].config.chip_id = cadd
    c.write_configuration(default_key, 'chip_id')
    #  - - remove default chip id from the controller
    c.remove_chip(default_key)
    #  - - and add the new chip id
    print(cm)
    c[cm].config.chip_id = cadd
    c[cm].config.i_rx1 = I_RX
    c.write_configuration(cm, 'i_rx1')
    c[cm].config.r_term1 = R_TERM
    c.write_configuration(cm, 'r_term1')
    c[cm].config.enable_posi = [0, 0, 0, 1]
    c.write_configuration(cm, 'enable_posi')
    time.sleep(2)
    c[cm].config.i_tx_diff0 = I_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff0')
    c[cm].config.tx_slices0 = TX_SLICE
    c.write_configuration(cm, 'tx_slices0')
    c[cm].config.i_tx_diff3 = I_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff3')
    c[cm].config.tx_slices3 = TX_SLICE
    c.write_configuration(cm, 'tx_slices3')
    c[cm].config.i_tx_diff1 = I_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff1')
    c[cm].config.tx_slices1 = TX_SLICE
    c.write_configuration(cm, 'tx_slices1')
  #  print(c[cm].config)
    # c[cm].config.v_cm_lvds_tx0 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx0')
    # c[cm].config.v_cm_lvds_tx1 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx1')
    # c[cm].config.v_cm_lvds_tx2 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx2')
    # c[cm].config.v_cm_lvds_tx3 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx3')

    # c.io.set_reg(0x18, 1, io_group=1)
    c[cm].config.enable_piso_downstream = [
        1, 1, 1, 1]  # krw adding May 8, 2023
    c.write_configuration(cm, 'enable_piso_downstream')
    time.sleep(0.1)
    c[cm].config.enable_piso_upstream = [0, 0, 0, 0]
    c.write_configuration(cm, 'enable_piso_upstream')
    c[cm].config.enable_piso_downstream = [0, 0, 1, 0]  # piso0
    c.write_configuration(cm, 'enable_piso_downstream')
    time.sleep(0.1)
    # enable pacman uart receiver
    rx_en = c.io.get_reg(0x18, iog)
    ch_set = pow(2, iochan-1)
    print('enable pacman uart receiver', rx_en, ch_set, rx_en | ch_set)
    c.io.set_reg(0x18, rx_en | ch_set, iog)


def conf_single(c, cm, cadd, iog, iochan):
    I_TX_DIFF = 0
    TX_SLICE = 15
    R_TERM = 2
    I_RX = 8
    c.add_chip(cm, version='2d')
    #  - - default larpix chip_id is '1'
    default_key = larpix.key.Key(iog, iochan, 1)  # '1-5-1'
    c.add_chip(default_key, version='2d')  # TODO, create v2c class
    #  - - rename to chip_id = cm
    c[default_key].config.chip_id = cadd
    c.write_configuration(default_key, 'chip_id')
    #  - - remove default chip id from the controller
    c.remove_chip(default_key)
    #  - - and add the new chip id
    print(cm)
    c[cm].config.chip_id = cadd
    c[cm].config.i_rx1 = I_RX
    c.write_configuration(cm, 'i_rx1')
    c[cm].config.r_term1 = R_TERM
    c.write_configuration(cm, 'r_term1')
    c[cm].config.enable_posi = [1, 0, 0, 0]  # use root posi0
    c.write_configuration(cm, 'enable_posi')
    c[cm].config.enable_piso_upstream = [0, 0, 0, 0]
    c.write_configuration(cm, 'enable_piso_upstream')
    c[cm].config.i_tx_diff0 = I_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff0')
    c[cm].config.tx_slices0 = TX_SLICE
    c.write_configuration(cm, 'tx_slices0')
    c[cm].config.i_tx_diff1 = I_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff1')
    c[cm].config.tx_slices1 = TX_SLICE
    c.write_configuration(cm, 'tx_slices1')
    c[cm].config.i_tx_diff2 = I_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff2')
    c[cm].config.tx_slices2 = TX_SLICE
    c.write_configuration(cm, 'tx_slices2')
    c[cm].config.i_tx_diff3 = I_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff3')
    c[cm].config.tx_slices3 = TX_SLICE
    c.write_configuration(cm, 'tx_slices3')
    # c.io.set_reg(0x18, 1, io_group=1)
    # keep all piso active on single chip board
    c[cm].config.enable_piso_downstream = [1, 1, 1, 1]
    c.write_configuration(cm, 'enable_piso_downstream')
    time.sleep(0.1)
    # enable pacman uart receiver
    rx_en = c.io.get_reg(0x18, iog)
    ch_set = pow(2, iochan-1)
    print('enable pacman uart receiver', rx_en, ch_set, rx_en | ch_set)
    c.io.set_reg(0x18, rx_en | ch_set, iog)
    # c[cm].config.v_cm_lvds_tx0 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx0')
    # c[cm].config.v_cm_lvds_tx1 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx1')
    # c[cm].config.v_cm_lvds_tx2 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx2')
    # c[cm].config.v_cm_lvds_tx3 = 2
    # c.write_configuration(cm, 'v_cm_lvds_tx3')


def main():

    ###########################################
    IO_GROUP = 1
    PACMAN_TILE = 1  # 1
    # IO_CHAN = (1+(PACMAN_TILE-1)*4)
    IO_CHAN = 1  # 1
    VDDA_DAC = 46500  # std supply 38000 #41000# boosted supply
    VDDD_DAC = 28500  # 32000# 28500 # ~1.1 V
    RESET_CYCLES = 300000  # 5000000

    REF_CURRENT_TRIM = 0
    ###########################################

    # create a larpix controller
    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True)
    io_group = IO_GROUP
    pacman_version = 'v1rev4'
    pacman_tile = [PACMAN_TILE]
    chip = 11
    cadd = 12
    # disable pacman rx uarts on tile 1
#    bitstring = list('00000000000000000000000000001111')
    bitstring = list('00000000000000000000000000000000')
    rx_en = c.io.get_reg(0x18, io_group)
#    c.io.set_reg(0x18, rx_en & 0xf, io_group)
    c.io.set_reg(0x18, int("".join(bitstring), 2), io_group)
    if False:  # inversion register for V2B tile error
        c.io.set_reg(0x1c01c,  0x3, io_group)
        c.io.set_reg(0x1d01c,  0x3, io_group)
        c.io.set_reg(0x1e01c,  0x3, io_group)
        c.io.set_reg(0x1f01c,  0x3, io_group)
    if True:
        print('enable pacman power')
        # disable tile power, LARPIX clock
        c.io.set_reg(0x00000010, 0, io_group)
        c.io.set_reg(0x00000014, 0, io_group)
        # set up mclk in pacman
        c.io.set_reg(0x101c, 0x4, io_group)
        time.sleep(1)

        # enable pacman power
        c.io.set_reg(0x00000014, 1, io_group)
        # set voltage dacs to 0V
        # c.io.set_reg(0x24010+(PACMAN_TILE-1), 0, io_group)
        # c.io.set_reg(0x24020+(PACMAN_TILE-1), 0, io_group)
        # time.sleep(0.1)
        # set voltage dacs  VDDD first
        c.io.set_reg(0x24020+(PACMAN_TILE-1), VDDD_DAC, io_group)
        c.io.set_reg(0x24010+(PACMAN_TILE-1), VDDA_DAC, io_group)
        # c.io.set_reg(0x24020+(PACMAN_TILE), VDDD_DAC, io_group)
        # c.io.set_reg(0x24010+(PACMAN_TILE), VDDA_DAC, io_group)

        print('reset the larpix for n cycles', RESET_CYCLES)
        #   - set reset cycles
        c.io.set_reg(0x1014, RESET_CYCLES, io_group=IO_GROUP)
        #   - toggle reset bit
        clk_ctrl = c.io.get_reg(0x1010, io_group=IO_GROUP)
        c.io.set_reg(0x1010, clk_ctrl | 4, io_group=IO_GROUP)
        c.io.set_reg(0x1010, clk_ctrl, io_group=IO_GROUP)

        # enable tile power
        tile_enable_val = pow(2, PACMAN_TILE-1) + \
            0x0200  # enable one tile at a time
  #      tile_enable_val=pow(2,PACMAN_TILE-1)+pow(2,PACMAN_TILE)+0x0200  #enable one tile at a time
        c.io.set_reg(0x00000010, tile_enable_val, io_group)
        time.sleep(0.03)
        print('enable tilereg 0x10 , ', tile_enable_val)
        readback = pacman_base.power_readback(
            c.io, io_group, pacman_version, pacman_tile)

    if True:
        #   - toggle reset bit
        RESET_CYCLES = 50000
        c.io.set_reg(0x1014, RESET_CYCLES, io_group=IO_GROUP)
        clk_ctrl = c.io.get_reg(0x1010, io_group=IO_GROUP)
        c.io.set_reg(0x1010, clk_ctrl | 4, io_group=IO_GROUP)
        c.io.set_reg(0x1010, clk_ctrl, io_group=IO_GROUP)
        time.sleep(0.5)

    # c.io.set_reg(0x24010+(PACMAN_TILE-1), 44500, io_group)
    readback = pacman_base.power_readback(
        c.io, io_group, pacman_version, [PACMAN_TILE])

    chip11_key = larpix.key.Key(IO_GROUP, IO_CHAN, 11)
    conf_root(c, chip11_key, 11, IO_GROUP, IO_CHAN)
    read(c, chip11_key, 'enable_piso_downstream')
    read(c, chip11_key, 'enable_piso_upstream')

    # add second chip
    chip12_key = larpix.key.Key(IO_GROUP, IO_CHAN, 12)
    conf_east(c, chip11_key, chip12_key, 12, IO_GROUP, IO_CHAN)
# # add third chip
    chip13_key = larpix.key.Key(IO_GROUP, IO_CHAN, 13)
    conf_east(c, chip12_key, chip13_key, 13, IO_GROUP, IO_CHAN)
# # add fourth chip
    chip14_key = larpix.key.Key(IO_GROUP, IO_CHAN, 14)
    conf_east(c, chip13_key, chip14_key, 14, IO_GROUP, IO_CHAN)
# # add fifth chip
    chip15_key = larpix.key.Key(IO_GROUP, IO_CHAN, 15)
    conf_east(c, chip14_key, chip15_key, 15, IO_GROUP, IO_CHAN)

    chip16_key = larpix.key.Key(IO_GROUP, IO_CHAN, 16)
    conf_east(c, chip15_key, chip16_key, 16, IO_GROUP, IO_CHAN)

# # add second root chain
#     IO_CHAN = IO_CHAN + 1
#     chip21_key = larpix.key.Key(IO_GROUP, IO_CHAN, 21)
#     conf_root(c, chip21_key, 21, IO_GROUP, IO_CHAN)
# # add second chip
#     chip22_key = larpix.key.Key(IO_GROUP, IO_CHAN, 22)
#     conf_east(c, chip21_key, chip22_key, 22, IO_GROUP, IO_CHAN)
# # # add third chip
#     chip23_key = larpix.key.Key(IO_GROUP, IO_CHAN, 23)
#     conf_east(c, chip22_key, chip23_key, 23, IO_GROUP, IO_CHAN)
# # add fourth chip
#     chip24_key = larpix.key.Key(IO_GROUP, IO_CHAN, 24)
#     conf_east(c, chip23_key, chip24_key, 24, IO_GROUP, IO_CHAN)
# # add fifth chip
#     chip25_key = larpix.key.Key(IO_GROUP, IO_CHAN, 25)
#     conf_east(c, chip24_key, chip25_key, 25, IO_GROUP, IO_CHAN)
# # add third root chain
#     IO_CHAN = IO_CHAN + 1
#     chip31_key = larpix.key.Key(IO_GROUP, IO_CHAN, 31)
#     conf_root(c, chip31_key, 31, IO_GROUP, IO_CHAN)
# # add second chip
#     chip32_key = larpix.key.Key(IO_GROUP, IO_CHAN, 32)
#     conf_east(c, chip31_key, chip32_key, 32, IO_GROUP, IO_CHAN)
# # add third chip
#     chip33_key = larpix.key.Key(IO_GROUP, IO_CHAN, 33)
#     conf_east(c, chip32_key, chip33_key, 33, IO_GROUP, IO_CHAN)
# # add fourth chip
#     chip34_key = larpix.key.Key(IO_GROUP, IO_CHAN, 34)
#     conf_east(c, chip33_key, chip34_key, 34, IO_GROUP, IO_CHAN)
# # add fifth chip
#     chip35_key = larpix.key.Key(IO_GROUP, IO_CHAN, 35)
#     conf_east(c, chip34_key, chip35_key, 35, IO_GROUP, IO_CHAN)
# # add 44 south
#     # chip44_key=larpix.key.Key(IO_GROUP,IO_CHAN,44)
#     # conf_south(c,chip34_key,chip44_key,44,IO_GROUP,IO_CHAN)
# # add 54 south
#     # chip54_key=larpix.key.Key(IO_GROUP,IO_CHAN,54)
#     # conf_south(c,chip44_key,chip54_key,54,IO_GROUP,IO_CHAN)
# # add 45 south
# #    chip45_key=larpix.key.Key(IO_GROUP,IO_CHAN,45)
# #    conf_south(c,chip35_key,chip45_key,45,IO_GROUP,IO_CHAN)
# # add 55 south
# #    chip55_key=larpix.key.Key(IO_GROUP,IO_CHAN,55)
# #    conf_south(c,chip45_key,chip55_key,55,IO_GROUP,IO_CHAN)

# # add fourth root chain
#     IO_CHAN = IO_CHAN + 1
#     chip41_key = larpix.key.Key(IO_GROUP, IO_CHAN, 41)
#     conf_root(c, chip41_key, 41, IO_GROUP, IO_CHAN)

# # add second chip
#     chip42_key = larpix.key.Key(IO_GROUP, IO_CHAN, 42)
#     conf_east(c, chip41_key, chip42_key, 42, IO_GROUP, IO_CHAN)
# # add third chip
#     chip43_key = larpix.key.Key(IO_GROUP, IO_CHAN, 43)
#     conf_east(c, chip42_key, chip43_key, 43, IO_GROUP, IO_CHAN)
# # add fourth chip
#     chip44_key = larpix.key.Key(IO_GROUP, IO_CHAN, 44)
#     conf_east(c, chip43_key, chip44_key, 44, IO_GROUP, IO_CHAN)
# # add fifth chip
#     chip45_key = larpix.key.Key(IO_GROUP, IO_CHAN, 45)
#     conf_east(c, chip44_key, chip45_key, 45, IO_GROUP, IO_CHAN)

# # add south row 5
#     chip51_key = larpix.key.Key(IO_GROUP, IO_CHAN, 51)
#     conf_south(c, chip41_key, chip51_key, 51, IO_GROUP, IO_CHAN)
# # add east row 5
#     chip52_key = larpix.key.Key(IO_GROUP, IO_CHAN, 52)
#     conf_east(c, chip51_key, chip52_key, 52, IO_GROUP, IO_CHAN)
#     chip53_key = larpix.key.Key(IO_GROUP, IO_CHAN, 53)
#     conf_east(c, chip52_key, chip53_key, 53, IO_GROUP, IO_CHAN)
#     chip54_key = larpix.key.Key(IO_GROUP, IO_CHAN, 54)
#     conf_east(c, chip53_key, chip54_key, 54, IO_GROUP, IO_CHAN)
#     chip55_key = larpix.key.Key(IO_GROUP, IO_CHAN, 55)
#     conf_east(c, chip54_key, chip55_key, 55, IO_GROUP, IO_CHAN)
#    read(c,chip52_key,'enable_piso_downstream')
# add second chip
# ex   chip42_key=larpix.key.Key(IO_GROUP,IO_CHAN,42)
#    conf_(c,chip41_key,chip42_key,42,IO_GROUP,IO_CHAN)

# add second chip
#    chip42_key=larpix.key.Key(IO_GROUP,IO_CHAN,42)
 #   conf_north(c,chip52_key,chip42_key,42,IO_GROUP,IO_CHAN)
 #   read(c,chip42_key,'enable_piso_downstream')
# add third chip
#    chip43_key=larpix.key.Key(IO_GROUP,IO_CHAN,43)
#    conf_east(c,chip42_key,chip43_key,43,IO_GROUP,IO_CHAN)

# add south row 5
#    chip53_key=larpix.key.Key(IO_GROUP,IO_CHAN,53)
#    conf_east(c,chip52_key,chip53_key,53,IO_GROUP,IO_CHAN)
# add single chip tile2 ch5
#    IO_CHAN = IO_CHAN + 1
#    chip61_key=larpix.key.Key(IO_GROUP,5,61)
#    conf_single(c,chip61_key,61,IO_GROUP,5)
#    c.io.set_reg(0x18, int("".join(bitstring),2), io_group)
    time.sleep(0.5)

#    c.io.set_reg(0x18, int("".join(bitstring),2), io_group)

    # ok, diff = utility_base.reconcile_configuration(c, chip11_key, verbose=True,n=5,n_verify=5)
    # optinally, take a look at the
    # message = c.reads[-1]
    # for msg in message:
    #    if not isinstance(msg,larpix.packet.packet_v2.Packet_v2):
    #        continue
    #    if msg.packet_type not in [larpix.packet.packet_v2.Packet_v2.CONFIG_WRITE_PACKET]:
    #        continue
    #    print(msg)

    # every second, read the chip ID on chip11 and print to screen
    while True:
        print('loop')
        chipcount = 0
        # c.reads = []
        # c.read_configuration(chip21_key,'chip_id')
        # print(c.reads[-1])
        chipcount = chipcount + 1/11 * read(c, chip11_key, 'chip_id')
        chipcount = chipcount + 1/12 * read(c, chip12_key, 'chip_id')
        # read(c, chip11_key, 'enable_posi')
        # read(c, chip11_key, 'enable_piso_upstream')
        # read(c, chip11_key, 'enable_piso_downstream')
        # read(c, chip12_key, 'enable_piso_downstream')
        # read(c, chip12_key, 'enable_piso_upstream')

        chipcount = chipcount + 1/13 * read(c, chip13_key, 'chip_id')
        chipcount = chipcount + 1/14 * read(c, chip14_key, 'chip_id')
        chipcount = chipcount + 1/15 * read(c, chip15_key, 'chip_id')
        chipcount = chipcount + 1/16 * read(c, chip16_key, 'chip_id')
        # chipcount = chipcount + 1/21 * read(c, chip21_key, 'chip_id')
        # chipcount = chipcount + 1/22 * read(c, chip22_key, 'chip_id')
        # chipcount = chipcount + 1/23 * read(c, chip23_key, 'chip_id')
        # chipcount = chipcount + 1/24 * read(c, chip24_key, 'chip_id')
        # chipcount = chipcount + 1/25 * read(c, chip25_key, 'chip_id')
        # chipcount = chipcount + 1/31 * read(c, chip31_key, 'chip_id')
        # chipcount = chipcount + 1/32 * read(c, chip32_key, 'chip_id')
        # chipcount = chipcount + 1/33 * read(c, chip33_key, 'chip_id')
        # chipcount = chipcount + 1/34 * read(c, chip34_key, 'chip_id')
        # chipcount = chipcount + 1/35 * read(c, chip35_key, 'chip_id')
        # chipcount = chipcount + 1/41 * read(c, chip41_key, 'chip_id')
        # chipcount = chipcount + 1/42 * read(c, chip42_key, 'chip_id')
        # chipcount = chipcount + 1/43 * read(c, chip43_key, 'chip_id')
        # chipcount = chipcount + 1/44 * read(c, chip44_key, 'chip_id')
        # chipcount = chipcount + 1/45 * read(c, chip45_key, 'chip_id')
        # chipcount = chipcount + 1/51 * read(c, chip51_key, 'chip_id')
        # chipcount = chipcount + 1/52 * read(c, chip52_key, 'chip_id')
        # chipcount = chipcount + 1/53 * read(c, chip53_key, 'chip_id')
        # chipcount = chipcount + 1/54 * read(c, chip54_key, 'chip_id')
        # chipcount = chipcount + 1/55 * read(c, chip55_key, 'chip_id')
        print('chips', chipcount)
        readback = pacman_base.power_readback(
            c.io, io_group, pacman_version, pacman_tile)
        time.sleep(1)
    return c, c.io


if __name__ == '__main__':
    main()
