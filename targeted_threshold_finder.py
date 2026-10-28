import os
import numpy as np
from matplotlib import pyplot as plt
import h5py
import matplotlib as mpl
import scipy.stats
from tqdm import tqdm
from cycler import cycler
import json
import time
import argparse
import pickle

color_list = ['#000000', '#D55E00', '#56B4E9', '#E69F00', '#009E73', '#CC79A7', '#0072B2', '#F0E442']
mpl.rcParams['axes.prop_cycle'] = cycler('color', color_list)

def datetime_now():
	''' Return string with year, month, day, hour, minute '''
	return time.strftime("%Y_%m_%d_%H_%M_%Z")

def unique_channel_id(d):
    return ((d['io_group'].astype(int)*1000+d['io_channel'].astype(int))*1000
            + d['chip_id'].astype(int))*100 + d['channel_id'].astype(int)

def unique_channel_id_from_int(io_group, io_channel, chip_id, channel_id):
    return ((io_group*1000+io_channel)*1000
            + chip_id)*100 + channel_id

def unique_to_channel_id(unique):
    return unique % 100

def unique_to_chip_id(unique):
    return (unique // 100) % 1000

def unique_to_io_channel(unique):
    return (unique//(100*1000)) % 1000

def unique_to_tiles(unique):
    return ((unique_to_io_channel(unique)-1) // 4) + 1

def unique_to_io_group(unique):
    return (unique // (100*1000*1000)) % 1000

def convert_to_mv(d, asic_version, VDDA, vref_dac, vcm_dac):
    adc_lsb_mv = None #(mV/ADC bin)
    if asic_version == '2d': adc_lsb_mv = VDDA * (vref_dac - vcm_dac)/256 /256
    elif asic_version == '3': adc_lsb_mv = VDDA * (vref_dac / 256) * (2 / 1023)
    if not adc_lsb_mv: raise RuntimeError('Unrecognized ASIC version, examples "2d" or "3"')
    
    return d*adc_lsb_mv

def convert_to_ke(d, asic_version, VDDA, vref_dac, vcm_dac):
    if asic_version == '2d': gain = 2.46 #(mV/ke-)
    elif asic_version == '3': gain = 3.4

    return convert_to_mv(d, asic_version, VDDA, vref_dac, vcm_dac)/gain #(ke-/ADC bin)



def main(tf_config):

    config = {}
    with open(tf_config, 'r') as f: config = json.load(f)

    #Power information for ADC scale
    asic_version = str(config['asic_version'])
    VDDA = config['VDDA']
    vref_dac = config['vref_dac']
    vcm_dac = config['vcm_dac']
    if (asic_version != '3') and not vcm_dac: raise RuntimeError('vcm_dac cannot be None for v2d ASIC')

    #Threshold target(s) and packet files
    target_threshold_list = config['target_thresholds_ke']
    pedestal_file = config['pedestal_file']
    ptd_leakage_dict = { int(key):fname for key, fname in config['ptd_leakage_dict'].items() }

    #Output directory
    timestamp = datetime_now()
    output_dir = f'threshold_finder-{timestamp}/'
    print(f'Writing output to directory {output_dir}')
    os.mkdir(output_dir)

    #What is my LSB?
    print(f'Using LSB = { convert_to_mv(1,asic_version,VDDA,vref_dac,vcm_dac) :.3}mV, { convert_to_ke(1,asic_version,VDDA,vref_dac,vcm_dac) :.3}ke-')

    # Parse the pedestal data
    data_packect_type = 1 if asic_version=='3' else 0
    ped_data = h5py.File(pedestal_file)
    ped_packets = ped_data['packets'][ ped_data['packets']['packet_type']==data_packect_type ]
    ped_data.close()
    ped_uids = unique_channel_id(ped_packets) #UID for each packet
    ped_datawords = ped_packets['dataword']
    chans = list(set(ped_uids)) #List of UIDs without repeats
    n_chans = len(chans)

    pedestal_means = {}
    for chan in tqdm(chans, desc='Parsing pedestals...'):
        mask = (ped_uids==chan)
        if asic_version=='3': mask = np.logical_and(mask, ped_datawords!=0)
        pedestal_means[chan] = ( scipy.stats.trim_mean( ped_datawords[mask], 0.3 ) )

    # Parse the leakage data
    threshold_scan = {} #Indexed as { pixel_trim_dac : [channel thresholds] }
    for ptd, file in ptd_leakage_dict.items():

        data = h5py.File(file)
        packets = data['packets'][ data['packets']['packet_type']==data_packect_type ]
        data.close()
        uids = unique_channel_id(packets) #UID for each packet
        datawords = packets['dataword']

        thresholds = np.ones( n_chans )*np.nan
        missing_lkg_data = 0
        for ichan in tqdm(range(n_chans), desc=f'Parsing leakage data for pixel_trim_dac={ptd}...'):
            chan = chans[ichan]
            if  chan not in uids:
                missing_lkg_data += 1
                continue

            # Get data associated to this channel and extract threshold
            mask = (uids==chan)
            if asic_version=='3': mask = np.logical_and(mask, datawords!=0)
            threshold_word = np.percentile( datawords[mask], 5 )
            thresholds[ichan] = ( convert_to_ke((threshold_word - pedestal_means[chan]),asic_version,VDDA,vref_dac,vcm_dac) )

        print(f'No leakage data from {missing_lkg_data} channels')
        threshold_scan[ptd] = thresholds

    fig, ax = plt.subplots()

    for ptd, thresholds in threshold_scan.items():
        ax.hist(thresholds, histtype='step', linewidth=2, label=f'pixel_trim_dac={ptd}')
    ax.set_xlabel('Threshold (ke$^-$)')
    ax.set_ylabel('Number of Channels')
    ax.legend()
    plt.savefig(f'{output_dir}/threshold_scan.png', bbox_inches='tight')


    #Per-channel linear fits to trim response
    ptd_list = list(ptd_leakage_dict.keys())

    lin_fits = []
    for ichan in range(n_chans):
        chan_thresholds = [threshold_scan[ptd][ichan] for ptd in ptd_list]
        fit = scipy.stats.linregress(ptd_list, chan_thresholds)
        lin_fits.append(fit)

    fig, axs = plt.subplots(2,2, figsize=(12,9))

    #Plot the WORST fit
    r_vals = [fit[2] for fit in lin_fits]
    min_r = min(r_vals)
    for sample_idx, r_val in enumerate(r_vals): 
        if r_val == min_r: break

    sample_io_group = unique_to_io_group(chans[sample_idx])
    sample_io_channel = unique_to_io_channel(chans[sample_idx])
    sample_chip_id = unique_to_chip_id(chans[sample_idx])
    sample_channel_id = unique_to_channel_id(chans[sample_idx])
    sample_fit = lin_fits[sample_idx]

    axs[0,0].scatter(ptd_list, [threshold_scan[ptd][sample_idx] for ptd in ptd_list], label=f'Chip Key {sample_io_group}-{sample_io_channel}-{sample_chip_id}, Channel {sample_channel_id}')
    axs[0,0].plot([0,31], [sample_fit[0]*ptd+sample_fit[1] for ptd in [0,31]], linestyle='--', color=color_list[1], label=f'Fit y={sample_fit[0]:.2}*x+{sample_fit[0]:.2}, r={min_r:.3}')
    axs[0,0].set_xlabel('Pixel Trim DAC')
    axs[0,0].set_ylabel('Threshold (ke$^-$)')
    axs[0,0].legend(title=f'Worst Fit')

    #Slope histogram
    axs[0,1].hist([fit[0] for fit in lin_fits], histtype='step', linewidth=2)
    axs[0,1].set_xlabel('Fit Slope (ke$^-$/ trim DAC)')
    axs[0,1].set_ylabel('Number of Channels')

    #Intercept histogram
    axs[1,0].hist([fit[1] for fit in lin_fits], histtype='step', linewidth=2)
    axs[1,0].set_xlabel('Fit Intercept (ke$^-$)')
    axs[1,0].set_ylabel('Number of Channels')

    #r-value histogram
    axs[1,1].hist([fit[2] for fit in lin_fits], histtype='step', linewidth=2)
    axs[1,1].set_xlabel('Fit r Value')
    axs[1,1].set_ylabel('Number of Channels')

    plt.savefig(f'{output_dir}/fit_summary.png', bbox_inches='tight')

    fig, ax = plt.subplots()
    ax.set_xlabel('Target Trim DAC')
    ax.set_ylabel('Number of Channels')
    ax.set_yscale('log')

    # Now set targeted thresholds
    for target_threshold in target_threshold_list:
        output_json = f'trim_values_{target_threshold}ke_target-{timestamp}.json'

        nan_thresholds = []
        target_trims = np.ones( n_chans )*np.nan
        target_json = {} #Dictionary for writing to json

        for ichan, chan in enumerate(chans):
            fit = lin_fits[ichan]

            #If valid fit
            if not(np.isnan(fit[0]) and fit[0]>0 and fit[2]>.9):
                solution = (target_threshold - fit[1])/fit[0]

                #If threshold possible
                if solution < 31.5:
                    target_trims[ichan] = max(np.round(solution), 0)
                else: nan_thresholds.append(chan)

            else: nan_thresholds.append(chan)

            io_group = unique_to_io_group(chan)
            io_channel = unique_to_io_channel(chan)
            chip_id = unique_to_chip_id(chan)
            channel_id = int(unique_to_channel_id(chan))
            chip_key = f'{io_group}-{io_channel}-{chip_id}'

            if not chip_key in target_json.keys(): target_json[chip_key] = []
            target_json[chip_key].append( [channel_id, target_trims[ichan]]  )

        #Write json output
        for chip_key in target_json.keys(): target_json[chip_key] = sorted(target_json[chip_key])
        meta_dict = {}
        meta_dict['meta'] = { output_json : {
                'target_threshold_ke' : target_threshold,
                'created'          : timestamp,
                'pedestal_file'    : pedestal_file,
                'leakage_files'    : list(ptd_leakage_dict.values()),
                'leakage_trims'    : list(ptd_leakage_dict.keys()),
                }
            }
        target_json = dict(meta_dict, **target_json) #Append metadata to the top
        with open(f'{output_dir}/{output_json}', 'w') as outfile: json.dump(target_json, outfile, indent=4)

        #Plot the trim settings
        ax.hist(target_trims, bins=np.linspace(0,31,32), histtype='step', linewidth=2, label = f'Target = {target_threshold}ke$^-$')


    ax.legend()
    plt.savefig(f'{output_dir}/targeted_trims_histogram.png', bbox_inches='tight')

    #Save the fits for later to avoid tens of minutes of data parsing
    with open(f'{output_dir}/pickled_fits.pkl', 'wb') as file:
        pickle.dump(threshold_scan, file)
        pickle.dump(lin_fits, file)
        pickle.dump(chans, file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('tf_config', nargs='+', help='''Threshold finder config, points to data and leakage files''')
    args = parser.parse_args()

    main(
        *args.tf_config
    )
