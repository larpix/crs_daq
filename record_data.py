import larpix
import argparse
from RUNENV import *
from base import config_loader
import shutil
import time
import os
from base import utility_base
from signal import signal, SIGINT
import subprocess
from base.utility_base import now

_default_file_count=-1
_default_runtime=300
_default_message='collecting data...'
_default_packet=False
_default_LRS = False

def ctrlc_handler(signal_received, frame):
     
    #check if light system is running, and if so, stop it
    
    if LRS: subprocess.call(["echo 0 > ~/.adc_watchdog_file"],shell=True)

    print('CTRL-C detected. Exiting.')
    exit(0)

def datetime_now():
	''' Return string with year, month, day, hour, minute '''
	return time.strftime("%Y_%m_%d_%H_%M_%Z_%S")

def main(file_count, runtime, message, packet, LRS, filename, **args):

    if not filename is None and file_count > 1:
        raise RuntimeError('All files will have same filename and will be overwritten')

    #copy current ASIC config 
    path='{}/asic_configs_{}'.format(asic_config_dir, datetime_now())
   
    if os.path.isdir(path):
        print('Error recording configs--timestamped config files already exist')
        return
    os.mkdir(path)

    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True)

    #data taking loop
    ctr=0
    while ctr<file_count or file_count < 0:
        
        run_start = datetime_now()

        filename = utility_base.data_filename(c, packet) 
        
        utility_base.data(c, runtime, packet, LRS, filename, writedir=destination_dir_)
        metadata = {
                'filename'   : filename,
                'run_start'  : run_start,
                'run_end'    : datetime_now(),
                'message'    : message,
                'asic_config': path,
                'LRS'        : LRS,
                }
        ctr+=1
    

    return  

if __name__=='__main__':
    signal(SIGINT, ctrlc_handler)
    parser = argparse.ArgumentParser()
    parser.add_argument('--LRS', default=_default_LRS, \
                        action='store_true', help='''True to run LRS''')

    parser.add_argument('--message', '-m', default=_default_message, \
                        type=str,  help='''Message logged with file''')
    parser.add_argument('--runtime', default=_default_runtime, \
                        type=int, help='''Runtime duration, default 5 minutes''')
    parser.add_argument('--file_count', default=_default_file_count, \
                        type=int, help='''Number of output files to create, default inf''')
    parser.add_argument('--packet', action='store_true', default=False,\
                        help='''Generate packet file (default binary)''')

    parser.add_argument('--filename', '-f', default=None, \
                        type=str,  help='''Specifiy a file name''')
    args=parser.parse_args()
    c = main(**vars(args))
