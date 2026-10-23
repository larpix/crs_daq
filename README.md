# Mixed v2d/v3a CRS DAQ

Control LArPix v2d (`"2d"`) and v3a (`3`) ASICs with Rev5/Hijinks PACMANs.
Each control process uses one PACMAN and one ASIC family. Mixed raw recording
uses the aggregate `io/pacman.json`.

Use Python 3.9 or later with the existing DAQ dependencies: larpix-control,
NumPy, h5py, pyzmq, bitarray, and tqdm. This implementation uses larpix-control
commit `5a69050422e82356c8faf9ad0ea3168c322d63e8`. Keep that working library
version when preparing a deployment; this cleanup does not change it.

Run commands from the repository root. Before any hardware operation, copy and
review the example deployment values:

- `RUN_CONFIG.json` defines connected tiles, ASIC family, PACMAN hardware
  version, tile-indexed power DAC values, and per-tile chip/link exclusions.
- `io/pacman_ioN.json` must contain exactly one endpoint for IO group N.
  The committed `192.0.2.1` and `192.0.2.2` addresses are documentation examples;
  replace them with the deployment's PACMAN addresses. Keep the aggregate
  `io/pacman.json` consistent with the individual files.
- [configs/controller_config.json](configs/controller_config.json) selects
  reviewed Hydra topologies. Everything in `configs/` is example configuration.

The two example groups map IOG1/v2d to tile 7 and IOG2/v3a to tile 10. For
additional PACMANs, add `io/pacman_ioN.json` and matching entries in every IOG
mapping in `RUN_CONFIG.json`, including DAC arrays and exclusions. The wrappers
accept any configured positive IO group; they do not define a six-PACMAN CERN
deployment or supply operating voltages for new tiles.

Control wrappers require an explicit group or comma-separated list. They
validate all selected deployment entries before launching control processes.
There is no implicit selection or `all` option. For example, after configuring
the corresponding deployment entries:

```bash
bash run/power_up.sh 3
bash run/power_down.sh 6
bash run/network.sh 2 --update_default
bash run/network.sh 3,4
bash run/configure.sh 5
bash run/configure.sh 5 asic_configs/REVIEWED_PARENT
bash run/iog/kill.sh 3,4
```

These are hardware/process-control commands. Networking and configuration run
one process per selected group, write per-group logs in `log/`, and propagate
failures. Power operations run sequentially. The optional configuration parent
contains `iogN` subdirectories. Without a path, configuration uses the current
local ASIC selection. `.envrc` is an ignored local PID file created as needed;
kill exits cleanly when it is absent.

Generate and select networks as described in [configs/README.md](configs/README.md).
For a tile with missing ASICs, v3 discovery supports:

```bash
python hydra_v3.py --io_group 2 --pacman_tile 10 \
  --pacman_config io/pacman_io2.json --allow-incomplete
```

Replay and configuration enforce the ASICs in the saved graph, including a
partial graph. Existing defaults with a different ASIC set are rejected;
`--update_default` explicitly creates fresh defaults without overwriting saved
ones. Local selections, logs, data, generated ASIC configurations and generated
topologies are runtime artifacts, separate from these source examples.

The pinned library builds the `reset_tiles()` exclusion mask using tiles 1–8.
Tile 10 has been reset in operation, but isolation between tiles 9 and 10 is
not guaranteed. The reset implementation and hardware settings are unchanged.
