import warnings
warnings.filterwarnings("ignore")

import argparse
import os
import json


hydra_registers = [
    'enable_piso_downstream',
    'enable_piso_upstream',
    'enable_posi',
    'enable_miso_downstream',
    'enable_miso_upstream',
    'enable_mosi',
]


def get_meta(config):
    """
    Return CHIP_KEY, ASIC_ID, ASIC_VERSION from either the newer
    config format with a 'meta' block or the older top-level format.
    """
    if 'meta' in config:
        return (
            config['meta']['CHIP_KEY'],
            config['meta']['ASIC_ID'],
            config['meta']['ASIC_VERSION'],
        )

    return (
        config['CHIP_KEY'],
        config['ASIC_ID'],
        config['ASIC_VERSION'],
    )


def set_chip_key(config, chip_key):
    """Set CHIP_KEY while respecting old/new config formats."""
    if 'meta' in config:
        config['meta']['CHIP_KEY'] = chip_key
    else:
        config['CHIP_KEY'] = chip_key


def main(input_files, **kwargs):

    # ------------------------------------------------------------------
    # Inventory the CURRENT working configs from .default_asic_configs_.json
    # ------------------------------------------------------------------

    default_configs = '.default_asic_configs_.json'

    with open(default_configs, 'r') as f:
        default_paths = json.load(f)

    registers = {}
    chip_keys = {}

    for io_group, config_dir in default_paths.items():

        print(f'Loading current Hydra configs from: {config_dir}')

        for filename in os.listdir(config_dir):

            if not filename.endswith('.json'):
                continue

            path = os.path.join(config_dir, filename)

            if not os.path.isfile(path):
                continue

            try:
                with open(path, 'r') as f:
                    asic_config = json.load(f)
            except Exception as exc:
                print(f'WARNING: could not read {path}: {exc}')
                continue

            try:
                chip_key, asic_id, version = get_meta(asic_config)
            except KeyError as exc:
                print(f'WARNING: malformed ASIC config {path}: missing {exc}')
                continue

            chip_keys[asic_id] = chip_key

            registers[asic_id] = {}

            for register in hydra_registers:
                if register in asic_config:
                    registers[asic_id][register] = asic_config[register]

    print(f'Current Hydra contains {len(chip_keys)} ASICs')

    # ------------------------------------------------------------------
    # Shift input configs onto the current Hydra
    # ------------------------------------------------------------------

    shifted = []
    removed = []
    unchanged = []

    for file in input_files:

        if not os.path.isfile(file):
            print(f'WARNING: input file disappeared / does not exist: {file}')
            continue

        try:
            with open(file, 'r') as f:
                asic_config = json.load(f)
        except Exception as exc:
            print(f'WARNING: could not read {file}: {exc}')
            continue

        try:
            old_chip_key, asic_id, version = get_meta(asic_config)
        except KeyError as exc:
            print(f'WARNING: malformed input config {file}: missing {exc}')
            continue

        # --------------------------------------------------------------
        # ASIC no longer exists in the current Hydra.
        #
        # This is intentional: if it isn't in the default config set,
        # it should not be part of the shifted threshold config either.
        # --------------------------------------------------------------

        if asic_id not in chip_keys:
            print(
                f'REMOVE: ASIC_ID {asic_id} is not present in the '
                f'current Hydra: {file}'
            )

            os.remove(file)
            removed.append(asic_id)
            continue

        # --------------------------------------------------------------
        # ASIC exists: transplant ONLY the Hydra routing registers
        # --------------------------------------------------------------

        new_chip_key = chip_keys[asic_id]

        for register, value in registers[asic_id].items():
            asic_config[register] = value

        set_chip_key(asic_config, new_chip_key)

        # Write updated contents before rename.
        with open(file, 'w') as f:
            json.dump(asic_config, f, indent=4)

        # --------------------------------------------------------------
        # Rename config if CHIP_KEY changed
        # --------------------------------------------------------------

        if old_chip_key != new_chip_key:

            directory = os.path.dirname(file)
            basename = os.path.basename(file)

            if old_chip_key not in basename:
                print(
                    f'WARNING: CHIP_KEY changed {old_chip_key} -> '
                    f'{new_chip_key}, but old key is not in filename: {file}'
                )
                unchanged.append(asic_id)
                continue

            new_basename = basename.replace(
                old_chip_key,
                new_chip_key,
                1
            )

            new_file = os.path.join(directory, new_basename)

            if os.path.exists(new_file):
                raise RuntimeError(
                    f'Refusing to overwrite existing config:\n'
                    f'  source: {file}\n'
                    f'  target: {new_file}'
                )

            print(
                f'SHIFT: {asic_id}: '
                f'{old_chip_key} -> {new_chip_key}'
            )

            os.rename(file, new_file)
            shifted.append(asic_id)

        else:
            unchanged.append(asic_id)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    input_asic_ids = set(shifted) | set(unchanged)

    missing_thresholds = set(chip_keys.keys()) - input_asic_ids

    print()
    print('============================================================')
    print('Hydra shift complete')
    print('============================================================')
    print(f'Shifted CHIP_KEY:       {len(shifted)}')
    print(f'Already correct:        {len(unchanged)}')
    print(f'Removed from old config:{len(removed)}')
    print()

    if removed:
        print('ASICs removed because they are absent from current Hydra:')
        for asic_id in sorted(set(removed)):
            print(f'  {asic_id}')
        print()

    if missing_thresholds:
        print('Current-Hydra ASICs with no supplied threshold config:')
        for asic_id in sorted(missing_thresholds):
            print(f'  {asic_id}')
        print()


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description='''
        Shift an existing ASIC configuration set onto the Hydra routing
        defined by .default_asic_configs_.json.

        ASICs absent from the current default configuration are removed.
        Non-Hydra settings such as threshold_global and pixel_trim_dac
        are preserved.
        '''
    )

    parser.add_argument(
        'input_files',
        nargs='+',
        help='ASIC config files to modify in place'
    )

    # Kept for command-line compatibility with the old script.
    # The actual Hydra source remains .default_asic_configs_.json.
    parser.add_argument(
        '--controller_config',
        default='configs/controller_config.json',
        type=str,
        help='Unused; retained for backwards compatibility'
    )

    args = parser.parse_args()

    main(
        args.input_files,
        controller_config=args.controller_config,
    )
