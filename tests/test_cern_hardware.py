import unittest
import json
import os
import tempfile

from base.asic_family import (
    normalize_asic_family,
    packet_family_for_asic,
    uart_clock_ratio_for_asic,
)
from base.hijinks import (
    DEAD_LOGICAL_CHANNELS,
    logical_to_physical_uart,
    physical_rx_mask,
    physical_uart_clock_register,
)
from base.network_config import (
    root_only_network,
    validate_external_roots,
    validate_network_payload,
)
from base.tile_layout import (
    PHYSICAL_ROOT_POSITIONS,
    column_row_from_position,
    physical_neighbors,
    position_from_column_row,
    validate_physical_links,
    validate_physical_root_assignment,
)


class FakeIO:
    def __init__(self):
        self.registers = {}
        self.clock_calls = []

    def set_reg(self, address, value, io_group):
        self.registers[(io_group, address)] = value

    def get_reg(self, address, io_group):
        return self.registers.get((io_group, address), 0x5A)

    def set_uart_clock_ratio(self, uart, ratio, io_group):
        self.clock_calls.append((io_group, uart, ratio))
        return ratio


class FamilyPolicyTest(unittest.TestCase):
    def test_supported_mapping(self):
        self.assertEqual(normalize_asic_family("2d"), "2d")
        self.assertEqual(normalize_asic_family(3), 3)
        self.assertEqual(packet_family_for_asic("2d"), 2)
        self.assertEqual(packet_family_for_asic(3), 3)
        self.assertEqual(uart_clock_ratio_for_asic("2d"), 2)
        self.assertEqual(uart_clock_ratio_for_asic(3), 1)

    def test_unknown_family_rejected(self):
        with self.assertRaises(ValueError):
            packet_family_for_asic(2)

    def test_larpix_semantic_register_translation(self):
        try:
            from larpix import Chip, Key
        except ModuleNotFoundError:
            self.skipTest("larpix-control is not installed")
        expected = {
            "2d": (118, 119),
            3: (114, 115),
        }
        for family, registers in expected.items():
            chip = Chip(Key(1, 1, 1), version=family)
            self.assertEqual(
                list(chip.config.register_map["digital_monitor_enable"])[0],
                registers[0],
            )
            self.assertEqual(
                list(chip.config.register_map["digital_monitor_chan"])[0],
                registers[1],
            )
            self.assertEqual(
                list(chip.config.register_map["enable_hit_veto"])[0], 128
            )


class HijinksMappingTest(unittest.TestCase):
    def test_mapping_boundaries_and_examples(self):
        self.assertEqual(logical_to_physical_uart(1), 1)
        self.assertEqual(logical_to_physical_uart(27), 23)
        self.assertEqual(logical_to_physical_uart(37), 30)
        self.assertEqual(logical_to_physical_uart(39), 32)

    def test_dead_channels(self):
        self.assertEqual(DEAD_LOGICAL_CHANNELS, frozenset(range(12, 41, 4)))
        for channel in DEAD_LOGICAL_CHANNELS:
            with self.assertRaises(ValueError):
                logical_to_physical_uart(channel)

    def test_active_low_masks(self):
        self.assertEqual(physical_rx_mask([]), 0xFFFFFFFF)
        self.assertEqual(physical_rx_mask([23]), 0xFFBFFFFF)
        self.assertEqual(physical_rx_mask([30]), 0xDFFFFFFF)

    def test_hardware_helpers_translate_before_register_access(self):
        # Importing pacman_base requires larpix in production, so this focused
        # integration check is skipped in minimal analysis environments.
        try:
            from base import pacman_base
        except ModuleNotFoundError as error:
            if error.name == "larpix":
                self.skipTest("larpix-control is not installed")
            raise
        io = FakeIO()
        pacman_base.enable_pacman_uart_from_io_channels(io, 2, [37])
        self.assertEqual(io.registers[(2, 0x201C)], 0xDFFFFFFF)
        pacman_base.set_uart_clock_ratio(io, 2, 37, 1)
        self.assertEqual(io.clock_calls, [(2, 30, 1)])
        pacman_base.set_channel_packet_delay(io, 2, 37, 0xFF)
        self.assertEqual(io.registers[(2, 0x20014)], 0xFF5A)

        pacman_base.set_all_packet_delays(io, 2, 0xFF)
        delay_addresses = {
            address for io_group, address in io.registers if io_group == 2
            and 0x03014 <= address <= 0x22014
        }
        self.assertEqual(len(delay_addresses), 32)

        io.clock_calls.clear()
        pacman_base.configure_hijinks_uart_infrastructure(
            io, 1, "2d", [25, 26, 27, 28]
        )
        self.assertEqual(io.registers[(1, 0x18)], 0x3FF)
        self.assertEqual(io.clock_calls, [(1, 21, 2), (1, 22, 2), (1, 23, 2)])
        io.clock_calls.clear()
        pacman_base.configure_hijinks_uart_infrastructure(
            io, 2, 3, [37, 38, 39, 40]
        )
        self.assertEqual(io.clock_calls, [(2, 30, 1), (2, 31, 1), (2, 32, 1)])

    def test_persistent_uart_clock_registers(self):
        self.assertEqual(physical_uart_clock_register(21), 0x17010)
        self.assertEqual(physical_uart_clock_register(22), 0x18010)
        self.assertEqual(physical_uart_clock_register(23), 0x19010)
        self.assertEqual(physical_uart_clock_register(30), 0x20010)

    def test_fsd_v2d_root_return_path(self):
        try:
            from larpix import Controller, Key
            from base.network_base_FSD import configure_root_chip
        except ModuleNotFoundError as error:
            if error.name == "larpix":
                self.skipTest("larpix-control is not installed")
            raise
        controller = Controller()
        key = Key(1, 25, 21)
        controller.add_chip(key, version="2d")
        controller.write_configuration = lambda *args, **kwargs: None
        configure_root_chip(controller, key, "2d", 0, 0, 15, 2, 8)
        config = controller[key].config
        self.assertEqual(config.enable_posi, [0, 0, 0, 1])
        self.assertEqual(config.enable_piso_downstream, [0, 0, 1, 0])
        self.assertEqual(config.enable_piso_upstream, [0, 0, 0, 0])
        self.assertEqual(config.i_tx_diff2, 0)
        self.assertEqual(config.tx_slices2, 15)


class TileGeometryTest(unittest.TestCase):
    def test_all_positions_round_trip_and_corners_match(self):
        positions = []
        for column in range(16):
            for row in range(10):
                position = position_from_column_row(column, row)
                positions.append(position)
                self.assertEqual(column_row_from_position(position), (column, row))
        self.assertEqual(len(set(positions)), 160)
        self.assertEqual(position_from_column_row(0, 0), 11)
        self.assertEqual(position_from_column_row(0, 9), 20)
        self.assertEqual(position_from_column_row(15, 0), 161)
        self.assertEqual(position_from_column_row(15, 9), 170)

    def test_roots_are_top_row_and_neighbors_do_not_wrap(self):
        self.assertTrue(all(column_row_from_position(root)[1] == 0
                            for root in PHYSICAL_ROOT_POSITIONS))
        self.assertNotIn(21, physical_neighbors(20))
        self.assertEqual(physical_neighbors(11), {12, 21})
        for position in range(11, 171):
            for neighbor in physical_neighbors(position):
                column, row = column_row_from_position(position)
                other_column, other_row = column_row_from_position(neighbor)
                self.assertEqual(abs(column - other_column) + abs(row - other_row), 1)

    def test_physical_connector_assignment_must_be_explicit(self):
        assignment = validate_physical_root_assignment(
            {25: 21, 26: 61, 27: 111}, {25, 26, 27})
        self.assertEqual(assignment[27], 111)
        with self.assertRaises(ValueError):
            validate_physical_root_assignment({25: 21, 26: 61}, {25, 26, 27})
        with self.assertRaises(ValueError):
            validate_physical_root_assignment(
                {25: 21, 26: 61, 27: 101}, {25, 26, 27})

    def test_declared_physical_links_match_layout(self):
        links = []
        for position in range(11, 171):
            links.extend((position, neighbor)
                         for neighbor in physical_neighbors(position))
        self.assertEqual(validate_physical_links(links), links)
        with self.assertRaisesRegex(ValueError, "non-neighbor"):
            validate_physical_links([(20, 21)])


class NetworkConfigTest(unittest.TestCase):
    @staticmethod
    def _edge_signature(controller):
        return {
            (io_group, io_channel, network_name): set(graph.edges(data="uart"))
            for io_group, channels in controller.network.items()
            for io_channel, graphs in channels.items()
            for network_name, graph in graphs.items()
        }

    def test_v2d_three_channel_export_validate_reload_round_trip(self):
        try:
            import larpix
            from base.network_base_FSD import write_network_to_file
        except ModuleNotFoundError as error:
            if error.name == "larpix":
                self.skipTest("larpix-control is not installed")
            raise
        controller = larpix.Controller()
        for channel, root in ((25, 21), (26, 61), (27, 101)):
            controller.add_network_node(1, channel, controller.network_names,
                                        "ext", root=True)
            controller.add_chip(larpix.Key(1, channel, root), version="2d", root=False)
            controller.add_chip(larpix.Key(1, channel, root + 1), version="2d", root=False)
            for chip_id in (root, root + 1):
                config = controller[larpix.Key(1, channel, chip_id)].config
                config.enable_piso_upstream = [0, 0, 0, 0]
                config.enable_piso_downstream = [0, 0, 0, 0]
                config.enable_posi = [0, 0, 0, 0]
            controller.add_network_link(1, channel, "miso_us", ("ext", root), 3)
            controller.add_network_link(1, channel, "miso_us", (root, root + 1), 0)
            controller.add_network_link(1, channel, "miso_ds", (root, "ext"), 1)
            controller.add_network_link(1, channel, "miso_ds", (root + 1, root), 2)
            controller.add_network_link(1, channel, "mosi", ("ext", root), 2)
            controller.add_network_link(1, channel, "mosi", (root, "ext"), 0)
            controller.add_network_link(1, channel, "mosi", (root, root + 1), 3)
            controller.add_network_link(1, channel, "mosi", (root + 1, root), 1)

        with tempfile.TemporaryDirectory() as directory:
            prefix = os.path.join(directory, "iog_1-tile_7")
            filename = write_network_to_file(
                controller, prefix, {1: [7]}, [], asic_version="2d")
            payload = validate_external_roots(filename)
            self.assertEqual(set(payload["network"]["1"]), {"25", "26", "27"})
            reloaded = larpix.Controller()
            reloaded.load(filename)

        self.assertEqual(set(controller.chips), set(reloaded.chips))
        self.assertEqual(self._edge_signature(controller),
                         self._edge_signature(reloaded))
        for channel, root in ((25, 21), (26, 61), (27, 101)):
            self.assertTrue(reloaded.network[1][channel]["miso_us"].nodes["ext"]["root"])
            self.assertEqual(
                reloaded[larpix.Key(1, channel, root)].asic_version, "2d")

    def test_missing_live_channel_does_not_overwrite_output(self):
        try:
            import larpix
            from base.network_base_FSD import write_network_to_file
        except ModuleNotFoundError as error:
            if error.name == "larpix":
                self.skipTest("larpix-control is not installed")
            raise
        controller = larpix.Controller()
        with tempfile.TemporaryDirectory() as directory:
            prefix = os.path.join(directory, "existing")
            destination = prefix + "-hydra-network.json"
            with open(destination, "w", encoding="utf-8") as output:
                output.write("known-good")
            with self.assertRaisesRegex(ValueError, "missing live channels"):
                write_network_to_file(
                    controller, prefix, {1: [7]}, [], asic_version="2d")
            with open(destination, encoding="utf-8") as saved:
                self.assertEqual(saved.read(), "known-good")

    def test_empty_and_malformed_networks_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_network_payload({"_config_type": "controller",
                                      "asic_version": "2d", "network": {}})
        malformed = root_only_network(1, 25, 21, "malformed", "2d")
        malformed["network"]["1"]["25"]["nodes"][0]["miso_us"][3] = 999
        with self.assertRaisesRegex(ValueError, "dangling"):
            validate_network_payload(malformed)

    def test_root_graph_points_from_external_node_to_asic(self):
        payload = root_only_network(2, 37, 1, "v3-root", 3)
        nodes = payload["network"]["2"]["37"]["nodes"]
        self.assertEqual(nodes[0]["chip_id"], "ext")
        self.assertTrue(nodes[0]["root"])
        self.assertEqual(nodes[0]["miso_us"], [None, None, None, 1])
        self.assertEqual(nodes[1]["chip_id"], 1)

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as output:
            json.dump(payload, output)
            output.flush()
            self.assertEqual(validate_external_roots(output.name), payload)

    def test_reversed_root_link_is_rejected(self):
        payload = root_only_network(2, 37, 1, "v3-root", 3)
        nodes = payload["network"]["2"]["37"]["nodes"]
        nodes[0]["miso_us"] = [None, None, None, None]
        nodes[1]["miso_us"] = [None, None, None, "ext"]
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as output:
            json.dump(payload, output)
            output.flush()
            with self.assertRaisesRegex(ValueError, "must point"):
                validate_external_roots(output.name)


if __name__ == "__main__":
    unittest.main()
