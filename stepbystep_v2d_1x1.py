import larpix
import larpix.io
from base import utility_base
from base import pacman_base
import time

default_v_cm_lvds_tx = 5

def read(c,key,param):
    c.reads = []
    c.read_configuration(key,param,timeout=0.01)
    message = c.reads[-1]
    for msg in message:
        #if not isinstance(msg,larpix.packet.packet_v2d.Packet_v2d):
        #    continue
        #if msg.packet_type not in [larpix.packet.packet_v2d.Packet_v2d.CONFIG_READ_PACKET]:
        #    continue
        print(msg)

def conf_east(c,cm,ck,cadd,iog,iochan):
    HI_TX_DIFF=0
    HTX_SLICE=15
    HR_TERM=2
    HI_RX=8
# add second chip
    #set mother transceivers
    c.add_chip(ck,version='2d')
    c[cm].config.i_rx3=HI_RX
    c.write_configuration(cm, 'i_rx3')
    c[cm].config.r_term3=HR_TERM
    c.write_configuration(cm, 'r_term3')
    c[cm].config.i_tx_diff2=HI_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff2')
    c[cm].config.tx_slices2=HTX_SLICE
    c.write_configuration(cm, 'tx_slices2')
    c[cm].config.enable_piso_upstream[2]=1  #[0,0,1,0]
    m_piso=c[cm].config.enable_piso_upstream
    c[cm].config.enable_piso_upstream=[0,0,1,0] # turn only one upstream port on during config
    c.write_configuration(cm, 'enable_piso_upstream')
    #add new chip to network
    default_key = larpix.key.Key(iog, iochan, 1) # '1-5-1'
    c.add_chip(default_key,version='2d') # TODO, create v2c class
    #  - - rename to chip_id = 12
    c[default_key].config.chip_id = cadd
    c.write_configuration(default_key,'chip_id')
    #  - - remove default chip id from the controller
    c.remove_chip(default_key)
    #  - - and add the new chip id
    print(ck)
    c[ck].config.chip_id=cadd
    c[ck].config.i_rx1=HI_RX
    c.write_configuration(ck, 'i_rx1')
    c[ck].config.r_term1=HR_TERM
    c.write_configuration(ck, 'r_term1')
    c[ck].config.enable_posi=[0,1,0,0]
    c.write_configuration(ck, 'enable_posi')
    c[ck].config.enable_piso_upstream=[0,0,0,0]
    c.write_configuration(ck, 'enable_piso_upstream')
    c[ck].config.i_tx_diff0=HI_TX_DIFF
    c.write_configuration(ck, 'i_tx_diff0')
    c[ck].config.tx_slices0=HTX_SLICE
    c.write_configuration(ck, 'tx_slices0')
    c[ck].config.enable_piso_downstream=[1,1,1,1] # krw adding May 8, 2023
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)
    c[ck].config.enable_piso_downstream=[1,0,0,0] # only one downstream port
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)
    #enable mother rx
    c[cm].config.enable_piso_upstream=m_piso
    c.write_configuration(cm, 'enable_piso_upstream') #allow multi-upstream
    c[cm].config.enable_posi[3]= 1  #[0,1,0,1]
    c.write_configuration(cm, 'enable_posi')

def conf_north(c,cm,ck,cadd,iog,iochan):
    HI_TX_DIFF=0
    HTX_SLICE=15
    HR_TERM=2
    HI_RX=8

# add second chip
    #set mother transceivers
    c.add_chip(ck,version='2d')
    c[cm].config.i_rx0=HI_RX
    c.write_configuration(cm, 'i_rx0')
    c[cm].config.r_term0=HR_TERM
    c.write_configuration(cm, 'r_term0')
    c[cm].config.i_tx_diff3=HI_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff3')
    c[cm].config.tx_slices3=HTX_SLICE
    c.write_configuration(cm, 'tx_slices3')
    c[cm].config.enable_piso_upstream[3]=1  #[0,0,0,1]
    c.write_configuration(cm, 'enable_piso_upstream')

    #add new chip to network
    default_key = larpix.key.Key(iog, iochan, 1) # '1-5-1'
    c.add_chip(default_key,version='2d') # TODO, create v2c class
    c[default_key].config.chip_id = cadd
    c.write_configuration(default_key,'chip_id')
    c.remove_chip(default_key)
    print("adding " ,ck)
    c[ck].config.chip_id=cadd
    c[ck].config.i_rx2=HI_RX
    c.write_configuration(ck, 'i_rx2')
    c[ck].config.r_term2=HR_TERM
    c.write_configuration(ck, 'r_term2')
    c[ck].config.enable_posi=[0,0,1,0]
    c.write_configuration(ck, 'enable_posi')
    c[ck].config.enable_piso_upstream=[0,0,0,0]
    c.write_configuration(ck, 'enable_piso_upstream')
    c[ck].config.i_tx_diff1=HI_TX_DIFF
    c.write_configuration(ck, 'i_tx_diff1')
    c[ck].config.tx_slices1=HTX_SLICE
    c.write_configuration(ck, 'tx_slices1')
    #enable mother rx
    c[cm].config.enable_posi[0]= 1  #[0,1,0,1]
    c.write_configuration(cm, 'enable_posi')
    c[ck].config.enable_piso_downstream=[1,1,1,1] # krw adding May 8, 2023
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)
    c[ck].config.enable_piso_downstream=[0,1,0,0] 
    c.write_configuration(ck, 'enable_piso_downstream')
    
def conf_south(c,cm,ck,cadd,iog,iochan):
    HI_TX_DIFF=0
    HTX_SLICE=15
    HR_TERM=2
    HI_RX=8

# add second chip
    #set mother transceivers rx2, tx1
    c.add_chip(ck,version='2d')
    c[cm].config.i_rx2=HI_RX
    c.write_configuration(cm, 'i_rx2')
    c[cm].config.r_term2=HR_TERM
    c.write_configuration(cm, 'r_term2')
    c[cm].config.i_tx_diff1=HI_TX_DIFF
    c.write_configuration(cm, 'i_tx_diff1')
    c[cm].config.tx_slices1=HTX_SLICE
    c.write_configuration(cm, 'tx_slices1')
    c[cm].config.enable_piso_upstream[1]= 1  
    m_piso=c[cm].config.enable_piso_upstream
    c[cm].config.enable_piso_upstream=[0,1,0,0] # turn only one upstream port on during config
    c.write_configuration(cm, 'enable_piso_upstream')

    #add new chip to network
    default_key = larpix.key.Key(iog, iochan, 1) # '1-5-1'
    c.add_chip(default_key,version='2d') # TODO, create v2c class
    #  - - rename to chip_id = 12
    c[default_key].config.chip_id = cadd
    c.write_configuration(default_key,'chip_id')
    #  - - remove default chip id from the controller
    c.remove_chip(default_key)
    #  - - and add the new chip id
    print(ck)
    c[ck].config.chip_id=cadd
    c[ck].config.i_rx0=HI_RX  #rx0,tx3
    c.write_configuration(ck, 'i_rx0')
    c[ck].config.r_term0=HR_TERM
    c.write_configuration(ck, 'r_term0')
    c[ck].config.enable_posi=[1,0,0,0]
    c.write_configuration(ck, 'enable_posi')
    c[ck].config.enable_piso_upstream=[0,0,0,0]
    c.write_configuration(ck, 'enable_piso_upstream')
    c[ck].config.i_tx_diff3=HI_TX_DIFF
    c.write_configuration(ck, 'i_tx_diff3')
    c[ck].config.tx_slices3=HTX_SLICE
    c.write_configuration(ck, 'tx_slices3')
    c[ck].config.enable_piso_downstream=[1,1,1,1] # krw adding May 8, 2023
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)    
    c[ck].config.enable_piso_downstream=[0,0,0,1] 
    c.write_configuration(ck, 'enable_piso_downstream')
    time.sleep(0.1)    
    #enable mother rx
    c[cm].config.enable_piso_upstream=m_piso
    c.write_configuration(cm, 'enable_piso_upstream') #allow multi-upstream
    c[cm].config.enable_posi[2]=1  #[0,1,0,1]
    c.write_configuration(cm, 'enable_posi')
        
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
    c[cm].config.enable_posi = [1,1,1,1] #[0, 1, 0, 0]
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

    # c.io.set_reg(0x18, 1, io_group=1)
    c[cm].config.enable_piso_downstream = [
        1, 1, 1, 1]  # krw adding May 8, 2023
    c.write_configuration(cm, 'enable_piso_downstream')
    time.sleep(0.1)
    c[cm].config.enable_piso_upstream = [0, 0, 0, 0]
    c.write_configuration(cm, 'enable_piso_upstream')
    c[cm].config.enable_piso_downstream = [1,1,1,1] #[1, 0, 0, 0]  # piso0
    c.write_configuration(cm, 'enable_piso_downstream')
    time.sleep(0.1)
    # enable pacman uart receiver
    rx_en = c.io.get_reg(0x18, iog)
    ch_set = pow(2, iochan-1)
    print('enable pacman uart receiver', rx_en, ch_set, rx_en | ch_set)
    c.io.set_reg(0x18, rx_en | ch_set, iog)

    
def main():

    ###########################################
    IO_GROUP = 1
    PACMAN_TILE = 1
    #IO_CHAN = (1+(PACMAN_TILE-1)*4)
    IO_CHAN = 4
    #IO_CHAN = 2
    VDDA_DAC= 46500 # ~1.8 V
    # VDDA_DAC= 5000 # ~1.8 V
    VDDD_DAC = 28500 # ~1.1 V
    # VDDD_DAC = 30000 # ~1.1 V
    RESET_CYCLES = 300000 #5000000

    REF_CURRENT_TRIM=0
    ###########################################

    # create a larpix controller
    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True)
    io_group=IO_GROUP
    pacman_version='v1rev4'
    pacman_tile=[PACMAN_TILE]
    chip=11
    cadd=12

    do_power_cycle = True

    if do_power_cycle:
        #disable pacman rx uarts
        print('enable pacman power')
        bitstring = list('00000000000000000000000000000000')
        print(int("".join(bitstring),2))
        c.io.set_reg(0x18, int("".join(bitstring),2), io_group)
        # disable tile power, LARPIX clock
        c.io.set_reg(0x00000010, 0, io_group)
        # set up mclk in pacman
        c.io.set_reg(0x101c, 0x4, io_group)
        
        # enable pacman power
        c.io.set_reg(0x00000014, 1, io_group)
        #set voltage dacs to 0V  
        c.io.set_reg(0x24010+(PACMAN_TILE-1), 0, io_group)
        c.io.set_reg(0x24020+(PACMAN_TILE-1), 0, io_group)
        #time.sleep(0.1)
        time.sleep(1)
        #set voltage dacs  VDDD first 
        c.io.set_reg(0x24020+(PACMAN_TILE-1), VDDD_DAC, io_group)
        c.io.set_reg(0x24010+(PACMAN_TILE-1), VDDA_DAC, io_group)
        

        print('reset the larpix for n cycles',RESET_CYCLES)
        #   - set reset cycles
        c.io.set_reg(0x1014,RESET_CYCLES,io_group=IO_GROUP)
        #   - toggle reset bit
        clk_ctrl = c.io.get_reg(0x1010, io_group=IO_GROUP)
        c.io.set_reg(0x1010, clk_ctrl|4, io_group=IO_GROUP)
        c.io.set_reg(0x1010, clk_ctrl, io_group=IO_GROUP)
        
        #enable tile power
        tile_enable_val=pow(2,PACMAN_TILE-1)+0x0200  #enable one tile at a time    
        c.io.set_reg(0x00000010,tile_enable_val,io_group)
        time.sleep(0.03)
        print('enable tilereg 0x10 , ', tile_enable_val)
        readback=pacman_base.power_readback(c.io, io_group, pacman_version,pacman_tile)

        #   - toggle reset bit
        RESET_CYCLES = 50000
        c.io.set_reg(0x1014,RESET_CYCLES,io_group=IO_GROUP)
        clk_ctrl = c.io.get_reg(0x1010, io_group=IO_GROUP)
        c.io.set_reg(0x1010, clk_ctrl|4, io_group=IO_GROUP)
        c.io.set_reg(0x1010, clk_ctrl, io_group=IO_GROUP)
        time.sleep(0.01)


    # chip11_key=larpix.key.Key(IO_GROUP,IO_CHAN,11)
    # conf_root(c,chip11_key,11,IO_GROUP,IO_CHAN)    

    chip41_key=larpix.key.Key(IO_GROUP,IO_CHAN,41)
    conf_root(c,chip41_key,41,IO_GROUP,IO_CHAN)    

#    bitstring = list('00000000000000000000000000010000')
#    print(int("".join(bitstring),2))
#    c.io.set_reg(0x18, int("".join(bitstring),2), io_group)

    read(c,chip41_key,'enable_piso_downstream')
   
    #ok, diff = utility_base.reconcile_configuration(c, chip11_key, verbose=True,n=5,n_verify=5)

    # optinally, take a look at the
    #message = c.reads[-1]
    #for msg in message:
    #    if not isinstance(msg,larpix.packet.packet_v2.Packet_v2):
    #        continue
    #    if msg.packet_type not in [larpix.packet.packet_v2.Packet_v2.CONFIG_WRITE_PACKET]:
    #        continue
    #    print(msg)

    # every second, read the chip ID on chip11 and print to screen
    while True:
        print('loop')
        c.reads = []
        c.read_configuration(chip41_key,'chip_id')
        message = c.reads[-1]
        for msg in message:
            if not isinstance(msg,larpix.packet.packet_v2.Packet_v2):
                continue
            if msg.packet_type not in [larpix.packet.packet_v2.Packet_v2.CONFIG_READ_PACKET]:
                continue
            if msg.chip_id <255:
                print(msg.chip_id)
        time.sleep(0.1)
    return c, c.io


if __name__=='__main__':
    main()

