import larpix
import argparse
import pickledb
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
     
    rundb = pickledb.load(run_db, True)
    
    #check if light system is running, and if so, stop it
    
    LRS = rundb.get('CURRENT_LRS_RUNNING')
    if LRS: subprocess.call(["echo 0 > ~/.adc_watchdog_file"],shell=True)

    filename = rundb.get('CURRENT_DATA_FILE')

    run_start = rundb.get('CURRENT_RUN_START')
    message = rundb.get('CURRENT_MESSAGE')
    path = rundb.get('CURRENT_ASIC_PATH')
    runID = rundb.get('CURRENT_RUN_ID')
    
    metadata = {
                'filename'   : filename,
                'run_start'  : run_start,
                'run_end'    : datetime_now(),
                'message'    : message,
                'asic_config': path,
                'LRS'        : LRS
                }
    
    rundb.set('RUN_{}'.format(runID), metadata )
    rundb.set('RUN_COUNT', rundb.get('RUN_COUNT') + 1)
    
    rundb.set('CURRENT_DATA_FILE', None)
    rundb.set('CURRENT_RUN_START', None )
    rundb.set('CURRENT_LRS_RUNNING', None)
    rundb.set('CURRENT_MESSAGE', None)
    rundb.set('CURRENT_ASIC_PATH', None)
    rundb.set('CURRENT_RUN_ID', None)    
    rundb.set('LAST_UPDATED', now())
    
    print('CTRL-C detected. Exiting.')
    exit(0)

def datetime_now():
	''' Return string with year, month, day, hour, minute '''
	return time.strftime("%Y_%m_%d_%H_%M_%Z_%S")

def main(file_count, runtime, message, packet, LRS, filename, **args):

    rundb = pickledb.load(run_db, True)
    envdb = pickledb.load(env_db, True)
    runID = int(rundb.get('RUN_COUNT'))
    rundb.set('RUN_COUNT', runID)
    rundb.set('LAST_UPDATED', now())

    if not filename is None and file_count > 1:
        raise RuntimeError('All files will have same filename and will be overwritten')

    #copy current ASIC config 
    path='{}/asic_configs_{}'.format(asic_config_dir, datetime_now())
   
    if os.path.isdir(path):
        print('Error recording configs--timestamped config files already exist')
        return
    os.mkdir(path)

    for io_group in io_group_pacman_tile_.keys():
        CONFIG = envdb.get('IO_GROUP_{}_ASIC_CONFIGS'.format(io_group))
        if not CONFIG:
            print('Unable to load current ASIC config')
            return
        for file in os.listdir(CONFIG):
            if not file.endswith('json'): continue
            shutil.copy('{}/{}'.format(CONFIG, file),'{}/{}'.format(path, file))
    

    c = larpix.Controller()
    c.io = larpix.io.PACMAN_IO(relaxed=True)

    #data taking loop
    ctr=0
    while ctr<file_count or file_count < 0:
        
        runID = rundb.get('RUN_COUNT')+1

        run_start = datetime_now()

        filename = utility_base.data_filename(c, packet) 
        
        rundb.set('CURRENT_DATA_FILE', filename)
        rundb.set('CURRENT_RUN_START', run_start )
        rundb.set('CURRENT_LRS_RUNNING', LRS)
        rundb.set('CURRENT_MESSAGE', message)
        rundb.set('CURRENT_ASIC_PATH', path)
        rundb.set('CURRENT_RUN_ID', runID)
        rundb.set('LAST_UPDATED', now())
        print('Run ID {}: {}'.format(runID, filename))

        utility_base.data(c, runtime, packet, LRS, filename, writedir=destination_dir_)
        metadata = {
                'filename'   : filename,
                'run_start'  : run_start,
                'run_end'    : datetime_now(),
                'message'    : message,
                'asic_config': path,
                'LRS'        : LRS,
                }
        rundb.set('RUN_{}'.format(runID), metadata )
        rundb.set('RUN_COUNT', rundb.get('RUN_COUNT') + 1)
        rundb.set('LAST_UPDATED', now())
        
        ctr+=1
    
    rundb.set('CURRENT_DATA_FILE', None)
    rundb.set('CURRENT_RUN_START', None )
    rundb.set('CURRENT_LRS_RUNNING', None)
    rundb.set('CURRENT_MESSAGE', None)
    rundb.set('CURRENT_ASIC_PATH', None)
    rundb.set('CURRENT_RUN_ID', None)
    rundb.set('LAST_UPDATED', now())

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
