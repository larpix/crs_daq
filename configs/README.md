# Example controller configurations

All files here are examples of deployment structure, not authoritative detector
operating state. The controller hierarchy matches the donor DAQ:

`controller_config.json` → `config-io_group-N.json` → tile Hydra JSON files.

`controller_config.json` maps each IO group to its controller configuration.
Select the reviewed tiles for that PACMAN by editing the `_include` list in
`config-io_group-N.json`. For example, two v2d tiles on IOG 1 could use:

```json
{
    "_config_type": "controller",
    "_include": [
        "configs/iog_1-tile_7-hydra-network.json",
        "configs/iog_1-tile_8-hydra-network.json"
    ]
}
```

Use the actual deployment filenames. Include paths follow native
`larpix.configs.load` rules: relative to the working directory (normally the
repository root), with the LArPix package configuration directory as fallback;
they are not relative to the including file. Every tile must use the selected
IO group and ASIC family, and IO channels must be unique across included files.
Each PACMAN can select up to ten tiles according to its actual configuration;
the loader has no single-tile limit. Different IO groups can use different ASIC
families, with an independent control process for each IO group and family.

The checked-in examples retain IOG 1 tile 7 and IOG 2 tile 10. Their Hydra paths
are populated only after discovery:

```bash
python hydra_v2d.py --io_group 1 --pacman_tile 7 --pacman_config io/pacman_io1.json
python hydra_v3.py --io_group 2 --pacman_tile 10 --pacman_config io/pacman_io2.json
```

These commands operate hardware. Adapt and review the deployment files first.
Normal outputs are `configs/iog_1-tile_7-hydra-network.json` and
`configs/iog_2-tile_10-hydra-network.json`. A bare `--file_prefix fresh-name`
also writes under `configs/`; an explicit directory such as `./elsewhere/name`
or an absolute prefix is retained. V3 discovery refuses existing output names.

V3 tries every available, non-excluded root. Failed roots are reported and their
channel graphs are omitted; discovery continues through working roots. A tile
with no working roots produces no topology, other selected tiles are attempted,
and the command ultimately exits unsuccessfully. Missing non-excluded ASICs
cause an error by default. `--allow-incomplete` permits a validated partial
network, reports the missing chip IDs and labels the export as incomplete.
The existing `missing` JSON dictionary retains failed UART attempts; IDs with
no attempted route appear under channel `0` with a `null` detail. This is
metadata only; no channel 0 is added to the replay graph.

Select the IO-group configuration path in `controller_config.json` before running
`bash run/network.sh N`. Bare entries in this index mean `configs/FILENAME`;
paths containing a directory are used as supplied, relative to the repository
root unless absolute. Successful replay records that path in the local
`.network_configs_.json`, so configuration and tile retries load the same full
topology. Configuration uses all discovered ASICs across the included tiles.
Direct single-tile Hydra paths remain supported for existing deployments.
Previously saved defaults must match that topology; an explicit
`bash run/network.sh N --update_default` generates fresh defaults when needed.
Partial discovery does not relax replay or configuration readback enforcement.

Generated topologies and runtime indexes are ignored by Git. Preserve operating
files separately and use fresh output names when changing a deployment.
`python write_controller_config.py` creates missing example index/IO-group files
and keeps existing files, including operator-edited `_include` selections.
Keep the local `RUN_CONFIG.json`, PACMAN addresses, generated Hydra/ASIC files,
and hidden runtime indexes when applying this repair to an operating checkout.
