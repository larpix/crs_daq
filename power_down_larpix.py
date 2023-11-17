from RUNENV import *
import larpix
import argparse
import larpix.io
import time
from base import pacman_base

import os

_default_verbose=False

def main(verbose):
     

    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True)

    for io_group in io_group_pacman_tile_.keys():

        # disable tile power, LARPIX clock
        c.io.set_reg(0x00000010, 0, io_group=io_group)
        c.io.set_reg(0x00000014, 0, io_group=io_group)
   
    try:     
        os.remove(env_db)
    except:
        pass

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', '-v', action='store_true', \
                        default=_default_verbose)
    args=parser.parse_args()
    c = main(**vars(args))
