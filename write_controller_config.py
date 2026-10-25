import json
from pathlib import Path

# Example hierarchy; keep existing deployment selections when run again.
config = {1: "configs/config-io_group-1.json",
          2: "configs/config-io_group-2.json"}
includes = {1: ["configs/iog_1-tile_7-hydra-network.json"],
            2: ["configs/iog_2-tile_10-hydra-network.json"]}


def main():
    Path('configs').mkdir(exist_ok=True)
    examples = {'configs/controller_config.json': config}
    examples.update({config[iog]: {'_config_type': 'controller', '_include': paths}
                     for iog, paths in includes.items()})
    for filename, payload in examples.items():
        try:
            with open(filename, 'x') as output:
                json.dump(payload, output, indent=4)
                output.write('\n')
        except FileExistsError:
            print(f'Keeping existing {filename}')


if __name__ == '__main__':
    main()

