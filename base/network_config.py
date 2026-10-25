"""Construction and validation helpers for CERN controller network JSON."""

import json
import os
import tempfile

import larpix.configs

from base.asic_family import normalize_asic_family
from base.hijinks import logical_to_physical_uart

UART_MAPS = {
    "miso_us_uart_map": [3, 0, 1, 2],
    "miso_ds_uart_map": [1, 2, 3, 0],
    "mosi_uart_map": [2, 3, 0, 1],
}


def root_only_network(io_group, io_channel, chip_id, name, asic_version,
                      layout="10x16"):
    """Build a controller-compatible external-root-to-ASIC network."""
    network = {
        str(io_group): {
            str(io_channel): {
                "nodes": [
                    {
                        "chip_id": "ext",
                        "miso_us": [None, None, None, chip_id],
                        "root": True,
                    },
                    {
                        "chip_id": chip_id,
                        "miso_us": [None, None, None, None],
                    },
                ]
            }
        }
    }
    network.update(UART_MAPS)
    return {
        "_config_type": "controller",
        "name": name,
        "asic_version": asic_version,
        "layout": layout,
        "network": network,
        "missing": {},
    }


def validate_external_roots(config_path):
    """Reject malformed root graphs before larpix ``init_network`` runs."""
    payload = larpix.configs.load(config_path, config_type="controller")
    validate_network_payload(payload, source=str(config_path))
    _validate_network_sources(config_path, payload["asic_version"], {})
    return payload


def _validate_network_sources(config_path, asic_version, channel_sources):
    """Audit declarations that native inheritance could silently overwrite.

    Only LArPix expands configurations. Read source JSON here to retain channel
    ownership and the include tree, which are lost after inheritance merges.
    """
    source = os.fspath(config_path)
    if not os.path.isfile(source):
        # Match larpix.configs.load's fallback to its packaged configurations.
        source = os.path.join(os.path.dirname(larpix.configs.__file__), source)
    with open(source, encoding="utf-8") as infile:
        document = json.load(infile)
    for io_group, channels in document.get("network", {}).items():
        if not str(io_group).isdigit():
            continue
        for io_channel in channels:
            key = (int(io_group), int(io_channel))
            if key in channel_sources:
                raise ValueError(
                    f"{config_path}: duplicate IO channel {io_group}-{io_channel}; "
                    f"also defined in {channel_sources[key]}"
                )
            channel_sources[key] = str(config_path)
    for included in document.get("_include", []):
        payload = larpix.configs.load(included, config_type="controller")
        validate_network_payload(payload, source=included)
        if payload["asic_version"] != asic_version:
            raise ValueError(f"{included}: ASIC family differs from {config_path}")
        _validate_network_sources(included, asic_version, channel_sources)


def validate_control_network(config_path, io_group, asic_family):
    """Check a saved topology before a homogeneous control process opens IO."""
    payload = validate_external_roots(config_path)
    if normalize_asic_family(payload['asic_version']) != asic_family:
        raise ValueError(f'{config_path}: ASIC family does not match IOG {io_group} ({asic_family!r})')
    groups = {int(key) for key in payload['network'] if str(key).isdigit()}
    if groups != {io_group}:
        raise ValueError(f'{config_path}: expected only IOG {io_group}, found {sorted(groups)}')
    for channel in payload['network'][str(io_group)]:
        logical_to_physical_uart(channel)
    return payload


def validate_network_payload(payload, source="controller network"):
    """Validate non-empty controller JSON, roots, and UART-labelled links."""
    if payload.get("_config_type") != "controller":
        raise ValueError(f"{source}: not a controller configuration")
    if payload.get("asic_version") not in ("2d", 3):
        raise ValueError(f"{source}: unsupported or missing ASIC version")
    network = payload.get("network", {})
    checked = 0
    for io_group, channels in network.items():
        if not str(io_group).isdigit():
            continue
        for io_channel, channel in channels.items():
            nodes = channel.get("nodes", [])
            if not nodes:
                raise ValueError(f"{source}: {io_group}-{io_channel} is empty")
            ids = [node.get("chip_id") for node in nodes]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{source}: duplicate nodes in {io_group}-{io_channel}")
            for node in nodes:
                for network_name in ("miso_us",):
                    links = node.get(network_name)
                    if not isinstance(links, list) or len(links) != 4:
                        raise ValueError(
                            f"{source}: {io_group}-{io_channel} node "
                            f"{node.get('chip_id')} must have four "
                            f"{network_name} UART entries"
                        )
                    dangling = [target for target in links
                                if target is not None and target not in ids]
                    if dangling:
                        raise ValueError(
                            f"{source}: dangling {network_name} target(s) "
                            f"{dangling} in {io_group}-{io_channel}"
                        )
            external = [node for node in nodes if node.get("chip_id") == "ext"]
            if len(external) != 1 or not external[0].get("root"):
                raise ValueError(
                    f"{source}: {io_group}-{io_channel} must contain "
                    "one external root node"
                )
            links = [value for value in external[0].get("miso_us", [])
                     if value is not None]
            chip_ids = {node.get("chip_id") for node in nodes
                        if node.get("chip_id") != "ext"}
            if len(links) != 1 or links[0] not in chip_ids:
                raise ValueError(
                    f"{source}: external root for {io_group}-{io_channel} "
                    "must point to exactly one ASIC node"
                )
            checked += 1
    if not checked:
        raise ValueError(f"{source}: no controller network channels found")
    return payload


def network_output_prefix(prefix):
    """Put bare Hydra output prefixes in configs; retain explicit directories."""
    if prefix is not None and not os.path.dirname(prefix):
        return os.path.join('configs', prefix)
    return prefix


def write_validated_network(payload, destination):
    """Atomically replace destination only after validation and JSON round-trip."""
    validate_network_payload(payload, source="generated controller network")
    directory = os.path.dirname(os.path.abspath(destination))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".hydra-network-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=4)
            output.write("\n")
        validate_external_roots(temporary)
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return destination
