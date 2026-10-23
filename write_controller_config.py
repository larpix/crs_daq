import json

# Example IOG-to-topology index; edit for the deployment before replay.
config = {1: "configs/iog_1-tile_7-hydra-network.json",
          2: "configs/iog_2-tile_10-hydra-network.json"}

with open('configs/controller_config.json', 'w') as f:
    json.dump(config, f)

