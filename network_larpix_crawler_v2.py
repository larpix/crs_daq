#!/usr/bin/env python3
"""
network_larpix_crawler_v2.py

Per-ASIC configuration extension for network_larpix_crawler.py.

The Hydra JSON remains the sole authority for network topology and crawl order.
When --asic-config-dir is supplied, each ASIC gets its operating configuration
from the matching physical ASIC identity (ASIC_ID) found inside the per-chip
JSON files. The source filename / old CHIP_KEY does not need to match the
current crawl io_channel.

Examples
--------
Use per-chip self-trigger configuration while replaying the known Hydra:

    python network_larpix_crawler_v2.py \
        configs/CRAWLER-iog-6-pacman-tile-5-hydra-network_v3.json \
        --asic-config-dir /data/CRS/asic_configs/SelfTrigger_v3/m2 \
        --yes

Validate topology + per-chip configuration set without touching hardware:

    python network_larpix_crawler_v2.py \
        configs/CRAWLER-iog-6-pacman-tile-5-hydra-network_v3.json \
        --asic-config-dir /data/CRS/asic_configs/SelfTrigger_v3/m2 \
        --dry-run --print-plan

Without --asic-config-dir this delegates to the original crawler behavior.

Design
------
* network_larpix_crawler.py owns topology, reset scope, safe crawl ordering,
  PACMAN RX handling, and PISO/POSI transitions.
* Per-chip JSON files own normal ASIC operating registers, including
  threshold_global, pixel_trim_dac, csa_enable, vref/vcm, periodic trigger/reset
  settings, masks, etc.
* Hydra-routing registers are NEVER taken from the per-chip JSON files.
* channel_mask is read from the per-chip JSON, but all channels are forced
  masked during network construction. The saved per-chip mask is applied only
  during the final deepest-to-roots activation phase.
* Per-chip configs are validated before the original crawler is allowed to
  touch hardware. ASICs present in the Hydra but absent from the tuned config
  directory fall back to the original crawler's safe uniform configuration.

This file is intentionally a thin extension of network_larpix_crawler.py so the
validated hardware sequencing stays in one place instead of being copied and
forked.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import network_larpix_crawler as crawler


EXTENSION_VERSION = "2.5.2-per-asic-config-tolerant"


# Registers whose values define/participate in the Hydra path. These remain
# exclusively under crawler control even if they are present in the source
# ASIC JSON.
TOPOLOGY_REGISTERS = {
    "chip_id",
    "enable_piso_upstream",
    "enable_piso_downstream",
    "enable_posi",
    # Included defensively for config formats / ASIC versions that expose the
    # older naming convention. If the v2b config does not have these fields,
    # they are simply ignored.
    "enable_miso_upstream",
    "enable_miso_downstream",
    "enable_mosi",
}

# channel_mask is special: the desired value comes from the per-chip file, but
# it is NOT written during crawl construction. Every chip stays [1] * 64 until
# the complete network is established.
SPECIAL_DEFERRED_REGISTERS = {
    "channel_mask",
}

# JSON bookkeeping / identity fields which are not operating registers to
# transplant into larpix-control Configuration objects.
NON_REGISTER_KEYS = {
    "meta",
    "CHIP_KEY",
    "ASIC_ID",
    "ASIC_VERSION",
}

# In per-ASIC mode these are the minimum fields expected from a threshold file.
# The rest of the supported register fields are also applied automatically.
REQUIRED_THRESHOLD_KEYS = {
    "threshold_global",
    "pixel_trim_dac",
}


# Populated by preflight before crawler.main() is called.
_ACTIVE_CONFIGS: Optional[Dict[Tuple[int, int, int], Dict[str, Any]]] = None
_ACTIVE_CONFIG_PATHS: Optional[Dict[Tuple[int, int, int], Path]] = None
_ACTIVE_CONFIG_DIR: Optional[Path] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _crawl_key(io_group: int, io_channel: int, chip_id: int) -> str:
    """Current logical address used by the crawler while configuring hardware."""
    return f"{io_group}-{io_channel}-{chip_id}"


def _physical_asic_id(io_group: int, tile: int, chip_id: int) -> str:
    """Stable physical ASIC identity used by threshold/config files."""
    return f"{io_group}-{tile}-{chip_id}"


def _json_chip_key(payload: Dict[str, Any]) -> Optional[str]:
    meta = payload.get("meta")
    if isinstance(meta, dict) and meta.get("CHIP_KEY") is not None:
        return str(meta["CHIP_KEY"])
    if payload.get("CHIP_KEY") is not None:
        return str(payload["CHIP_KEY"])
    return None


def _json_asic_id(payload: Dict[str, Any]) -> Optional[str]:
    meta = payload.get("meta")
    if isinstance(meta, dict) and meta.get("ASIC_ID") is not None:
        return str(meta["ASIC_ID"])
    if payload.get("ASIC_ID") is not None:
        return str(payload["ASIC_ID"])
    return None


def _parse_asic_id(value: str, *, path: Path) -> Tuple[int, int, int]:
    try:
        io_group_s, tile_s, chip_id_s = str(value).split("-")
        return int(io_group_s), int(tile_s), int(chip_id_s)
    except Exception as exc:
        raise ValueError(
            f"{path}: malformed ASIC_ID={value!r}; expected '<io_group>-<tile>-<chip_id>'"
        ) from exc


def _copy_value(value: Any) -> Any:
    """Copy list-like JSON values before assigning to larpix smart lists."""
    if isinstance(value, list):
        return list(value)
    return value


def _validate_64_vector(
    filename: Path,
    payload: Dict[str, Any],
    key: str,
    *,
    allowed_values: Optional[set] = None,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> None:
    if key not in payload:
        return

    value = payload[key]
    if not isinstance(value, list) or len(value) != 64:
        raise ValueError(
            f"{filename}: {key} must be a 64-element JSON array; "
            f"got {type(value).__name__} length "
            f"{len(value) if isinstance(value, list) else 'n/a'}"
        )

    for index, item in enumerate(value):
        if isinstance(item, bool):
            item_for_range = int(item)
        elif isinstance(item, int):
            item_for_range = item
        else:
            raise ValueError(
                f"{filename}: {key}[{index}]={item!r} is not an integer"
            )

        if allowed_values is not None and item_for_range not in allowed_values:
            raise ValueError(
                f"{filename}: {key}[{index}]={item_for_range}; "
                f"allowed values are {sorted(allowed_values)}"
            )
        if minimum is not None and item_for_range < minimum:
            raise ValueError(
                f"{filename}: {key}[{index}]={item_for_range} < {minimum}"
            )
        if maximum is not None and item_for_range > maximum:
            raise ValueError(
                f"{filename}: {key}[{index}]={item_for_range} > {maximum}"
            )


def _validate_one_config(
    path: Path,
    *,
    expected_asic_id: Tuple[int, int, int],
) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, dict):
        raise ValueError(f"{path}: top-level JSON object must be a dictionary")

    embedded_asic_id = _json_asic_id(payload)
    if embedded_asic_id is None:
        raise ValueError(
            f"{path}: missing ASIC_ID. Per-ASIC recovery matches configs by physical "
            "ASIC_ID so thresholds remain attached to the same chip even if its "
            "Hydra io_channel/CHIP_KEY changed."
        )

    parsed_asic_id = _parse_asic_id(embedded_asic_id, path=path)
    if parsed_asic_id != expected_asic_id:
        raise ValueError(
            f"{path}: embedded ASIC_ID={embedded_asic_id!r}, expected "
            f"{_physical_asic_id(*expected_asic_id)!r}"
        )

    missing_required = sorted(REQUIRED_THRESHOLD_KEYS - set(payload))
    if missing_required:
        raise ValueError(
            f"{path}: missing required threshold field(s): {missing_required}"
        )

    threshold = payload["threshold_global"]
    if isinstance(threshold, bool) or not isinstance(threshold, int):
        raise ValueError(f"{path}: threshold_global must be an integer")
    if threshold < 0 or threshold > 255:
        raise ValueError(
            f"{path}: threshold_global={threshold}; expected range 0..255"
        )

    _validate_64_vector(
        path, payload, "pixel_trim_dac", minimum=0, maximum=31
    )
    _validate_64_vector(
        path, payload, "channel_mask", allowed_values={0, 1}
    )
    _validate_64_vector(
        path, payload, "csa_enable", allowed_values={0, 1}
    )
    _validate_64_vector(
        path, payload, "periodic_trigger_mask", allowed_values={0, 1}
    )

    return payload


def preflight_config_directory(
    target: crawler.TargetNetwork,
    config_dir: Path,
) -> Tuple[
    Dict[Tuple[int, int, int], Dict[str, Any]],
    Dict[Tuple[int, int, int], Path],
]:
    """
    Load configs by physical ASIC_ID, not by source filename / old CHIP_KEY.

    A physical ASIC is identified as:
        <io_group>-<PACMAN tile>-<chip_id>

    This deliberately allows the same physical chip to move between the four
    Hydra io_channels belonging to a tile while retaining its tuned threshold
    and pixel-trim configuration.
    """

    config_dir = config_dir.expanduser().resolve()
    if not config_dir.is_dir():
        raise ValueError(f"ASIC config directory does not exist: {config_dir}")

    # First inventory every source config by its embedded physical ASIC_ID.
    # Filename / CHIP_KEY are intentionally NOT used as identity because they
    # may describe an older Hydra route.
    source_by_asic: Dict[Tuple[int, int, int], Tuple[Path, Dict[str, Any]]] = {}
    skipped_without_asic_id: List[Path] = []

    for path in sorted(config_dir.glob("config_*.json")):
        if not path.is_file():
            continue

        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            raise ValueError(f"Could not read source config {path}: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValueError(f"{path}: top-level JSON object must be a dictionary")

        raw_asic_id = _json_asic_id(payload)
        if raw_asic_id is None:
            skipped_without_asic_id.append(path)
            continue

        asic_id = _parse_asic_id(raw_asic_id, path=path)

        previous = source_by_asic.get(asic_id)
        if previous is not None:
            raise ValueError(
                "Multiple source configs claim the same physical ASIC_ID "
                f"{_physical_asic_id(*asic_id)}:\n"
                f"  {previous[0]}\n"
                f"  {path}\n"
                "Refusing to guess which tuned configuration is authoritative."
            )

        source_by_asic[asic_id] = (path, payload)

    configs: Dict[Tuple[int, int, int], Dict[str, Any]] = {}
    paths: Dict[Tuple[int, int, int], Path] = {}
    missing: List[Tuple[str, str]] = []

    thresholds: List[int] = []
    trims: List[int] = []
    masked_pixels = 0
    configs_without_channel_mask = 0
    remapped_chip_keys: List[Tuple[str, str, Path]] = []
    used_source_paths = set()

    # All trees in TargetNetwork are guaranteed by the upstream parser to live
    # on one PACMAN tile. That physical tile, not the current io_channel, is the
    # stable identity component used by ASIC_ID.
    tile = target.pacman_tile

    for tree in target.trees:
        for chip_id in tree.chip_ids:
            crawl_key = (target.io_group, tree.io_channel, chip_id)
            physical_id = (target.io_group, tile, chip_id)
            source = source_by_asic.get(physical_id)

            if source is None:
                missing.append((
                    _physical_asic_id(*physical_id),
                    _crawl_key(*crawl_key),
                ))
                continue

            source_path, _ = source
            payload = _validate_one_config(
                source_path,
                expected_asic_id=physical_id,
            )

            configs[crawl_key] = payload
            paths[crawl_key] = source_path
            used_source_paths.add(source_path)

            thresholds.append(int(payload["threshold_global"]))
            trims.extend(int(v) for v in payload["pixel_trim_dac"])

            if "channel_mask" in payload:
                masked_pixels += sum(int(v) != 0 for v in payload["channel_mask"])
            else:
                configs_without_channel_mask += 1

            old_chip_key = _json_chip_key(payload)
            new_chip_key = _crawl_key(*crawl_key)
            if old_chip_key is not None and old_chip_key != new_chip_key:
                remapped_chip_keys.append((old_chip_key, new_chip_key, source_path))

    # Missing tuned configs are allowed. Those ASICs are still part of the
    # Hydra and MUST still be configured so they can act as routers; they simply
    # fall back to the original crawler's uniform safe operating configuration.
    # Refuse only the pathological case where the directory matched nothing at
    # all, which is much more likely to be a wrong path / wrong tile than an
    # intentional partial threshold set.
    if not configs:
        raise ValueError(
            f"No per-ASIC configs in {config_dir} matched the selected Hydra "
            f"(io_group {target.io_group}, tile {tile}). Refusing to silently "
            "fall back for the entire tile."
        )

    unused_paths = sorted(
        path
        for (iog, source_tile, _chip), (path, _payload) in source_by_asic.items()
        if iog == target.io_group
        and source_tile == tile
        and path not in used_source_paths
    )

    print("=" * 78)
    print("PER-ASIC CONFIG PREFLIGHT")
    print(f"  extension version      : {EXTENSION_VERSION}")
    print(f"  config directory       : {config_dir}")
    print(f"  physical matching      : ASIC_ID = io_group-tile-chip_id")
    print(f"  target PACMAN tile     : {tile}")
    print(f"  topology ASICs         : {target.chip_count}")
    print(f"  configs loaded         : {len(configs)}")
    print(
        f"  missing configs        : {len(missing)} "
        "(allowed; legacy safe-config fallback)"
    )
    print(f"  old CHIP_KEY remaps    : {len(remapped_chip_keys)}")
    print(f"  unused same-tile files : {len(unused_paths)} (ignored)")
    if skipped_without_asic_id:
        print(
            f"  files without ASIC_ID  : {len(skipped_without_asic_id)} "
            "(ignored; cannot safely identify physical ASIC)"
        )
    if thresholds:
        print(
            f"  threshold_global       : min={min(thresholds)} "
            f"max={max(thresholds)}"
        )
    if trims:
        print(
            f"  pixel_trim_dac         : {len(trims)} pixels, "
            f"min={min(trims)} max={max(trims)}"
        )
    print(f"  saved masked pixels    : {masked_pixels}")
    if configs_without_channel_mask:
        print(
            f"  channel_mask absent    : {configs_without_channel_mask} config(s); "
            "those chips will finish fully unmasked"
        )
    print("  source CHIP_KEY        : informational only; may belong to old Hydra")
    print("  topology registers     : crawler-controlled; source JSON ignored")
    print("  crawl-time channel mask: [1] * 64 for every ASIC")
    print("  final channel mask     : restored from each source JSON")
    print("  missing-config ASICs   : legacy crawler config; fully unmasked at end")
    print("=" * 78)
    print()


    if missing:
        print("WARNING: topology ASICs with no tuned config; using crawler fallback:")
        for asic_id, crawl_key in missing[:25]:
            print(f"  {asic_id} -> {crawl_key}")
        if len(missing) > 25:
            print(f"  ... and {len(missing) - 25} more")
        print()

    if remapped_chip_keys:
        print("ASICs whose tuned config came from a different old Hydra address:")
        for old_key, new_key, source_path in remapped_chip_keys[:20]:
            print(f"  {old_key} -> {new_key}   [{source_path.name}]")
        if len(remapped_chip_keys) > 20:
            print(f"  ... and {len(remapped_chip_keys) - 20} more")
        print()

    if unused_paths:
        print("Unused same-tile ASIC configs (not present in selected Hydra topology):")
        for path in unused_paths[:20]:
            raw = _json_asic_id(source_by_asic[next(
                key for key, value in source_by_asic.items() if value[0] == path
            )][1])
            print(f"  {path.name}  ASIC_ID={raw}")
        if len(unused_paths) > 20:
            print(f"  ... and {len(unused_paths) - 20} more")
        print()

    return configs, paths


# ---------------------------------------------------------------------------
# Hardware extension
# ---------------------------------------------------------------------------


class PerAsicConfigCrawlerHardware(crawler.MaskedCrawlerHardware):
    """Masked crawler that sources normal ASIC registers from per-chip JSONs."""

    def __init__(
        self,
        options: crawler.HardwareOptions,
        log: crawler.ConsoleLogger,
        operational_config: Dict[str, Any],
    ):
        super().__init__(options, log, operational_config)
        self._final_channel_masks: Dict[int, List[int]] = {}
        self._write_names_by_chip: Dict[int, List[str]] = {}
        self._reported_unknown_keys: set = set()

    def _source_config(self, chip_id: int) -> Optional[Dict[str, Any]]:
        if _ACTIVE_CONFIGS is None:
            return None

        key = (self.options.io_group, self.options.io_channel, chip_id)
        return _ACTIVE_CONFIGS.get(key)

    def _source_path(self, chip_id: int) -> Path:
        key = (self.options.io_group, self.options.io_channel, chip_id)
        if _ACTIVE_CONFIG_PATHS is None or key not in _ACTIVE_CONFIG_PATHS:
            raise RuntimeError(f"No source path recorded for crawl address {key}")
        return _ACTIVE_CONFIG_PATHS[key]

    def _set_safe_config(
        self,
        chip_id: int,
        *,
        input_posi: int,
    ) -> None:
        source = self._source_config(chip_id)

        # No tuned config for this physical ASIC: keep the chip in the Hydra,
        # but use the original crawler's safe uniform operating configuration.
        # This is important: skipping configuration entirely could break the
        # routing path to downstream chips.
        if source is None:
            self._final_channel_masks[chip_id] = [0] * 64
            print(
                f"[config] WARNING: no tuned config for "
                f"{_crawl_key(self.options.io_group, self.options.io_channel, chip_id)}; "
                "using legacy crawler fallback configuration"
            )
            return super()._set_safe_config(chip_id, input_posi=input_posi)

        chip = self._ensure_chip(chip_id)
        cfg = chip.config

        write_names: List[str] = []
        seen = set()

        def remember(name: str) -> None:
            if name not in seen:
                seen.add(name)
                write_names.append(name)

        # Apply every source JSON field which is a real register, except the
        # topology/safety fields that the crawler must own.
        for name, value in source.items():
            if name in NON_REGISTER_KEYS:
                continue
            if name in TOPOLOGY_REGISTERS:
                continue
            if name in SPECIAL_DEFERRED_REGISTERS:
                continue

            if name not in cfg.register_map:
                marker = (chip_id, name)
                if marker not in self._reported_unknown_keys:
                    print(
                        f"[config] WARNING: {self._source_path(chip_id).name} "
                        f"contains non-register key {name!r}; ignoring it"
                    )
                    self._reported_unknown_keys.add(marker)
                continue

            setattr(cfg, name, _copy_value(value))
            remember(name)

        # Save desired final pixel masking, but keep the entire ASIC quiet until
        # the topology is completely established.
        final_mask = source.get("channel_mask", [0] * 64)
        self._final_channel_masks[chip_id] = [int(v) for v in final_mask]

        # The crawler exclusively controls identity + Hydra pathing while the
        # network is being built.
        cfg.chip_id = chip_id
        cfg.enable_piso_upstream = [0, 0, 0, 0]
        cfg.enable_piso_downstream = [0, 0, 0, 0]

        posi = [0, 0, 0, 0]
        posi[input_posi] = 1
        cfg.enable_posi = posi

        # Critical invariant: no source JSON may unmask a chip during crawl.
        cfg.channel_mask = [1] * 64

        # Preserve the validated original crawler write pattern: write the chip
        # ID/config plus the explicit safe topology state on every blind pass.
        for name in (
            "channel_mask",
            "enable_piso_upstream",
            "enable_piso_downstream",
            "enable_posi",
            "chip_id",
        ):
            if name in cfg.register_map:
                remember(name)

        self._write_names_by_chip[chip_id] = write_names

        print(
            f"[config] {_json_asic_id(source)} -> "
            f"{_crawl_key(self.options.io_group, self.options.io_channel, chip_id)} "
            f"from {self._source_path(chip_id).name}: "
            f"threshold_global={source.get('threshold_global')} "
            f"trim_range={min(source['pixel_trim_dac'])}..{max(source['pixel_trim_dac'])} "
            f"final_masked={sum(int(v) != 0 for v in final_mask)}"
        )

    def _blind_configure_masked(
        self,
        chip_id: int,
        *,
        input_posi: int,
        is_trial: bool,
    ) -> None:
        # Legacy behavior if no config folder was requested OR if this one ASIC
        # has no tuned config in an otherwise-valid partial config set.
        if _ACTIVE_CONFIGS is None or self._source_config(chip_id) is None:
            return super()._blind_configure_masked(
                chip_id,
                input_posi=input_posi,
                is_trial=is_trial,
            )

        self._set_safe_config(chip_id, input_posi=input_posi)
        register_names = self._write_names_by_chip[chip_id]

        for attempt in range(1, self.options.config_write_repeats + 1):
            self.log.event(
                "SAFE_CONFIG_ATTEMPT_BEGIN",
                "Per-ASIC config write; candidate/root remains masked and downstream-disabled",
                console=True,
                chip=chip_id,
                attempt=attempt,
                repeats=self.options.config_write_repeats,
                is_trial=is_trial,
                source="per-ASIC JSON",
            )

            # Preserve the original robust claim sequence.
            self._claim_chip_id(chip_id, attempt)

            self._write_register_names(
                chip_id,
                register_names,
                reason="blind masked per-ASIC configuration",
                attempt=attempt,
            )

            self.log.event(
                "SAFE_CONFIG_ATTEMPT_END",
                chip=chip_id,
                attempt=attempt,
                is_trial=is_trial,
                source="per-ASIC JSON",
            )

            if attempt != self.options.config_write_repeats:
                time.sleep(self.options.write_delay_s)

    def unmask_chip(self, chip_id: int) -> None:
        # Legacy behavior if no config folder was requested OR this ASIC had no
        # tuned source config. Missing-config ASICs therefore finish fully
        # unmasked, exactly like the original crawler.
        if _ACTIVE_CONFIGS is None or self._source_config(chip_id) is None:
            return super().unmask_chip(chip_id)

        chip = self._ensure_chip(chip_id)
        final_mask = list(self._final_channel_masks.get(chip_id, [0] * 64))
        chip.config.channel_mask = final_mask

        masked_channels = sum(int(v) != 0 for v in final_mask)

        self.log.event(
            "UNMASK_BEGIN",
            "Apply final per-ASIC channel mask after complete network build",
            console=True,
            chip=chip_id,
            masked_channels=masked_channels,
        )

        self._write_register_names(
            chip_id,
            ["channel_mask"],
            reason=f"apply final per-ASIC channel mask to chip {chip_id}",
        )

        self.log.event(
            "CHIP_LIVE",
            "Final per-ASIC channel mask applied",
            chip=chip_id,
            masked_channels=masked_channels,
            console=True,
        )


# ---------------------------------------------------------------------------
# Extend the original command line without duplicating crawler.main().
# ---------------------------------------------------------------------------


_ORIGINAL_BUILD_PARSER = crawler.build_parser
_ORIGINAL_OPERATIONAL_CONFIG_FROM_ARGS = crawler.operational_config_from_args


def build_parser_v2() -> argparse.ArgumentParser:
    parser = _ORIGINAL_BUILD_PARSER()
    parser.add_argument(
        "--asic-config-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing tuned per-chip JSON files. Files are matched to the "
            "current Hydra by embedded physical ASIC_ID=<io_group>-<tile>-<chip_id>, "
            "not by filename or old CHIP_KEY/io_channel. These JSONs define normal "
            "ASIC operating registers (including threshold_global and pixel_trim_dac); "
            "Hydra routing remains crawler-controlled. Missing physical ASIC configs "
            "are allowed and use the legacy crawler's uniform CLI/default configuration."
        ),
    )
    return parser


def operational_config_from_args_v2(args: argparse.Namespace) -> Dict[str, Any]:
    # Preserve the original CLI-derived uniform configuration as a fallback for
    # topology ASICs that do not have a tuned per-chip JSON. For ASICs that DO
    # have a source JSON, PerAsicConfigCrawlerHardware ignores this dictionary
    # and applies the per-chip values directly.
    return _ORIGINAL_OPERATIONAL_CONFIG_FROM_ARGS(args)


# Patch only the extension points; all validated topology / hardware sequencing
# remains in the upstream crawler module.
crawler.build_parser = build_parser_v2
crawler.operational_config_from_args = operational_config_from_args_v2
crawler.MaskedCrawlerHardware = PerAsicConfigCrawlerHardware
crawler.SCRIPT_VERSION = EXTENSION_VERSION


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    global _ACTIVE_CONFIGS
    global _ACTIVE_CONFIG_PATHS
    global _ACTIVE_CONFIG_DIR

    args = build_parser_v2().parse_args(argv)

    _ACTIVE_CONFIGS = None
    _ACTIVE_CONFIG_PATHS = None
    _ACTIVE_CONFIG_DIR = None

    if args.asic_config_dir is not None:
        try:
            _, target = crawler.load_target_network(
                args.network_json,
                requested_io_group=args.io_group,
                requested_io_channel=args.io_channel,
            )

            config_dir = args.asic_config_dir.expanduser().resolve()
            configs, paths = preflight_config_directory(target, config_dir)

            _ACTIVE_CONFIGS = configs
            _ACTIVE_CONFIG_PATHS = paths
            _ACTIVE_CONFIG_DIR = config_dir

        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"ERROR: per-ASIC config preflight failed: {exc}", file=sys.stderr)
            return 2

    # Delegate execution to the original crawler after preflight. Since its
    # parser/class/config hooks were replaced above, it transparently uses the
    # per-ASIC behavior while retaining the validated crawl sequence.
    return crawler.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
