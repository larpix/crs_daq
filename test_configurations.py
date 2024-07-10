import larpix
import larpix.io
import time
from base import pacman_base

default_v_cm_lvds_tx = 5


def read(c, key, param):
    c.reads = []
    c.read_configuration(key, param, timeout=0.01)
    message = c.reads[-1]
    for msg in message:
        if not isinstance(msg, larpix.packet.packet_v2d.Packet_v2d):
            continue
        if msg.packet_type not in [larpix.packet.packet_v2d.Packet_v2d.CONFIG_READ_PACKET]:
            continue
        print(msg)


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
    c[cm].config.enable_posi = [1, 1, 1, 1]  # [0, 1, 0, 0]
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
    c[cm].config.v_cm_lvds_tx0 = default_v_cm_lvds_tx
    c.write_configuration(cm, 'v_cm_lvds_tx0')
    # c[cm].config.v_cm_lvds_tx1 = default_v_cm_lvds_tx
    # c.write_configuration(cm, 'v_cm_lvds_tx1')
    # c[cm].config.v_cm_lvds_tx2 = default_v_cm_lvds_tx
    # c.write_configuration(cm, 'v_cm_lvds_tx2')
    # c[cm].config.v_cm_lvds_tx3 = default_v_cm_lvds_tx
    # c.write_configuration(cm, 'v_cm_lvds_tx3')


c = larpix.Controller()
c.io = larpix.io.PACMAN_IO(relaxed=True)


IO_GROUP = 1
PACMAN_TILE = 1
# IO_CHAN = (1+(PACMAN_TILE-1)*4)
IO_CHAN = 1
# IO_CHAN = 2
VDDA_DAC = 46500  # ~1.8 V
# VDDA_DAC= 5000 # ~1.8 V
VDDD_DAC = 28500  # ~1.1 V
# VDDD_DAC = 30000 # ~1.1 V
RESET_CYCLES = 300000  # 5000000

io_group = 1
pacman_version = 'v1rev4'
pacman_tile = [1]
chip = 11
cadd = 12

do_power_cycle = True

if do_power_cycle:
    # disable pacman rx uarts
    print('enable pacman power')
    bitstring = list('00000000000000000000000000000000')
    print(int("".join(bitstring), 2))
    c.io.set_reg(0x18, int("".join(bitstring), 2), io_group)
    # disable tile power, LARPIX clock
    c.io.set_reg(0x00000010, 0, io_group)
    # set up mclk in pacman
    c.io.set_reg(0x101c, 0x4, io_group)

    # enable pacman power
    c.io.set_reg(0x00000014, 1, io_group)
    # set voltage dacs to 0V
    c.io.set_reg(0x24010+(PACMAN_TILE-1), 0, io_group)
    c.io.set_reg(0x24020+(PACMAN_TILE-1), 0, io_group)
    # time.sleep(0.1)
    time.sleep(1)
    # set voltage dacs  VDDD first
    c.io.set_reg(0x24020+(PACMAN_TILE-1), VDDD_DAC, io_group)
    c.io.set_reg(0x24010+(PACMAN_TILE-1), VDDA_DAC, io_group)

    print('reset the larpix for n cycles', RESET_CYCLES)
    #   - set reset cycles
    c.io.set_reg(0x1014, RESET_CYCLES, io_group=IO_GROUP)
    #   - toggle reset bit
    clk_ctrl = c.io.get_reg(0x1010, io_group=IO_GROUP)
    c.io.set_reg(0x1010, clk_ctrl | 4, io_group=IO_GROUP)
    c.io.set_reg(0x1010, clk_ctrl, io_group=IO_GROUP)

    # enable tile power
    tile_enable_val = pow(2, PACMAN_TILE-1)+0x0200  # enable one tile at a time
    c.io.set_reg(0x00000010, tile_enable_val, io_group)
    time.sleep(0.03)
    print('enable tilereg 0x10 , ', tile_enable_val)
    readback = pacman_base.power_readback(
        c.io, io_group, pacman_version, pacman_tile)

    #   - toggle reset bit
    RESET_CYCLES = 50000
    c.io.set_reg(0x1014, RESET_CYCLES, io_group=IO_GROUP)
    clk_ctrl = c.io.get_reg(0x1010, io_group=IO_GROUP)
    c.io.set_reg(0x1010, clk_ctrl | 4, io_group=IO_GROUP)
    c.io.set_reg(0x1010, clk_ctrl, io_group=IO_GROUP)
    time.sleep(0.01)


# chip11_key=larpix.key.Key(IO_GROUP,IO_CHAN,11)
# conf_root(c,chip11_key,11,IO_GROUP,IO_CHAN)

chip11_key = larpix.key.Key(IO_GROUP, IO_CHAN, 11)
conf_root(c, chip11_key, 11, IO_GROUP, IO_CHAN)
read(c, chip11_key, 'chip_id')
c.enforce_configuration([chip11_key], timeout=0.01, connection_delay=0.01)
start = time.time()
for i in range(100):
    ok, diff = c.verify_configuration(
        [chip11_key], timeout=0.01, connection_delay=0.01)
    print(diff)
end = time.time()

print('Time elapsed: ', end-start)
