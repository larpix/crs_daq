import larpix
import larpix.io
import argparse
import time
import json
import os
from base import utility_base

from runenv import runenv as RUN
asic_config_dir=RUN.asic_config_dir


hydra_registers = ['enable_piso_downstream', 'enable_piso_upstream', 'enable_posi', 'enable_miso_downstream', 'enable_miso_upstream', 'enable_mosi']

def datetime_now():
    return utility_base.now()

def write_config_to_file(c, path=None, chip_key=None, description='default'):

    now = datetime_now()
    if path is None:
        path='{}/asic_configs_{}'.format(asic_config_dir, now)

    if not os.path.isdir(path): os.mkdir(path)

    chips = []

    if chip_key is None: 
        chips=c.chips
    else:
        chips.append(chip_key)

    for chip in chips:
        with open(path+'/config_{}.json'.format(str(chip)), 'w') as f:
            full_dict = c[chip].config.to_dict()
            chip_dict = {key : full_dict[key] for key in c[chip].config.get_nondefault_registers().keys()} 
            chip_dict['meta']={}
            chip_dict['meta']['CHIP_KEY']=str(chip)
            chip_dict['meta']['ASIC_ID']='{}-{}-{}'.format(chip.io_group, utility_base.io_channel_to_tile(chip.io_channel), chip.chip_id)
            chip_dict['meta']['ASIC_VERSION'] = c[chip].asic_version
            chip_dict['meta']['description'] = description
            chip_dict['meta']['created'] = now
            chip_dict['meta']['last_update'] = now
            json.dump( chip_dict , f, indent=4)

    return path

def parse_disabled_dict(disabled_dict):
    channel_masks = {}
    for key in disabled_dict:
        channel_masks[key] = [1 if channel in disabled_dict[key] else 0 for channel in range(64)]
    
    return channel_masks

def parse_disabled_json(disabled_json):

    if not os.path.isfile(disabled_json):
        raise RuntimeError('Disabled list does not exist')

    disabled_list = {}
    with open(disabled_json, 'r') as f: disabled_list=json.load(f)

    channel_masks = {}
    for key in disabled_list:
        channel_masks[key] = [1 if channel in disabled_list[key] else 0 for channel in range(64)]
    
    return channel_masks

def load_config_from_directory(c, directory, verbose=False):
    ''' Load into controller memory all ASIC configuration JSON Files from directory'''
    for file in os.listdir(directory):
        if verbose: print('loading file:', directory+'/'+file)
        if file[-5:]=='.json':
            c = load_config_from_file(c, directory+'/'+file)
   
    return c

def load_config_from_file_existing_network(c, config):
    ''' Load into controller memory an ASIC configuration from JSON File'''

    asic_config={}
    with open(config, 'r') as f: asic_config=json.load(f)

    chip_key = asic_config['meta']['CHIP_KEY']
    key_tile = utility_base.io_channel_to_tile(larpix.key.Key(chip_key).io_channel)
    ids = chip_key.split('-')
    new_key = '{}-{}-{}'.format(ids[0], key_tile, ids[2])

    new_id = None
    for chip in c.chips.keys():
            chip_tile = utility_base.io_channel_to_tile(chip.io_channel)
            new_chip = '{}-{}-{}'.format(chip.io_group, chip_tile, chip.chip_id)
            if new_chip==new_key:
                new_id = chip
                break

    flag = False
    if not new_id==chip_key:
        flag=True

    
    if flag: print('found {} for {}'.format(new_id, chip_key))
    if new_id is None:
        print('unable to find chip:', chip_key)

    for key in asic_config.keys():
        if key in hydra_registers: continue 
        if key=='chip_id': continue
        if key=='meta': continue
        setattr(c[larpix.key.Key(new_id)].config, key, asic_config[key])


    return c

def load_config_from_file(c, config):
    ''' Load into controller memory an ASIC configuration from JSON File'''

    asic_config={}
    with open(config, 'r') as f: asic_config=json.load(f)

    chip_key = asic_config['meta']['CHIP_KEY']
    asic_key = asic_config['meta']['ASIC_ID']
    version = asic_config['meta']['ASIC_VERSION'] 
    key_tile = utility_base.io_channel_to_tile(larpix.key.Key(chip_key).io_channel)   
    
    if not chip_key in c.chips:
        c.add_chip(chip_key, version=version)

        for key in asic_config.keys():
            if key=='meta': continue 
            if key in hydra_registers: continue 
            setattr(c[chip_key].config, key, asic_config[key])

    return c

