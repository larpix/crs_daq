# Example controller configurations

All files here are examples of deployment structure, not authoritative detector
operating state. `controller_config.json` maps each IO group to a reviewed Hydra
topology. The two example paths are populated only after discovery:

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

Select the reviewed topology path in `controller_config.json` before running
`bash run/network.sh N`. Bare entries in this index mean `configs/FILENAME`;
paths containing a directory are used as supplied, relative to the repository
root unless absolute. Successful replay records that path in the local
`.network_configs_.json`. Configuration then uses exactly the discovered ASICs.
Previously saved defaults must match that topology; an explicit
`bash run/network.sh N --update_default` generates fresh defaults when needed.
Partial discovery does not relax replay or configuration readback enforcement.

Generated topologies and runtime indexes are ignored by Git. Preserve operating
files separately and use fresh output names when changing a deployment.
