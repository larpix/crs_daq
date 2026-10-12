"""Offline control-path regression tests; all hardware IO is replaced.

Run through run_offline.py for additional socket blocking and import isolation.
"""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import larpix
import larpix.io
import network_larpix
import configure_larpix
import hydra_v3
from base import config_loader, enforce_parallel, pacman_base
from base import network_base_FSD, network_base_FSD_v3, utility_base
from base.network_config import root_only_network
import test_cern_hardware as hardware_tests

FakeIO = hardware_tests.FakeIO

ROOT = Path(__file__).resolve().parents[1]


class ControlHandoffTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.previous = Path.cwd()
        os.chdir(temporary)
        self.stack.callback(os.chdir, self.previous)
        self.runtime = json.loads((ROOT / 'RUN_CONFIG.json').read_text())
        Path('RUN_CONFIG.json').write_text(json.dumps(self.runtime))
        for index in ('.default_asic_configs_.json', '.asic_configs_.json', '.network_configs_.json'):
            Path(index).write_text('{}')
        self.io_factory = self.stack.enter_context(mock.patch.object(larpix.io, 'PACMAN_IO'))
        self.enable = self.stack.enter_context(mock.patch.object(pacman_base, 'enable_pacman_uart_from_io_channel'))
        self.enable_all = self.stack.enter_context(mock.patch.object(pacman_base, 'enable_all_pacman_uart_from_io_group'))
        self.enforce = self.stack.enter_context(mock.patch.object(
            enforce_parallel, 'enforce_parallel', return_value=(True, {}, [])))
        self.replay2 = self.stack.enter_context(mock.patch.object(network_larpix.network_base_FSD, 'network_v2b'))
        self.replay3 = self.stack.enter_context(mock.patch.object(network_larpix.network_base_FSD_v3, 'network_v3'))

    def prepare(self, iog=1, channel=None, chip_id=21):
        family = '2d' if iog == 1 else 3
        channel = channel or (25 if iog == 1 else 37)
        topology = root_only_network(iog, channel, chip_id, 'fixture', family)
        Path('topology.json').write_text(json.dumps(topology))
        Path('controller.json').write_text(json.dumps({str(iog): 'topology.json'}))
        Path('pacman.json').write_text(json.dumps({'io_group': [[iog, '192.0.2.1']]}))
        controller = larpix.Controller()
        controller.load('topology.json')
        for key in controller.chips:
            controller[key].config.threshold_global = 73
        (self.replay2 if iog == 1 else self.replay3).return_value = controller
        self.iog, self.family, self.controller = iog, family, controller

    def select_defaults(self, directory='old'):
        config_loader.write_config_to_file(self.controller, directory)
        Path('.default_asic_configs_.json').write_text(json.dumps({str(self.iog): directory}))

    def run_network(self, **kwargs):
        return network_larpix.main(False, 'controller.json', pacman_config='pacman.json',
                                   config_path='fresh/iog', **kwargs)

    def index(self, path):
        return json.loads(Path(path).read_text())

    def test_generate_round_trip_and_configure_both_families(self):
        for iog in (1, 2):
            with self.subTest(iog=iog), tempfile.TemporaryDirectory(dir='.') as directory:
                previous = Path.cwd()
                os.chdir(directory)
                try:
                    Path('RUN_CONFIG.json').write_text(json.dumps(self.runtime))
                    for index in ('.default_asic_configs_.json', '.asic_configs_.json', '.network_configs_.json'):
                        Path(index).write_text('{}')
                    self.prepare(iog)
                    self.run_network()
                    saved = list(Path('fresh/iog').glob('*.json'))
                    self.assertEqual(len(saved), 1)
                    self.assertEqual(json.loads(saved[0].read_text())['meta']['ASIC_VERSION'], self.family)
                    loaded = self.enforce.call_args.args[0]
                    chip = next(iter(loaded.chips.values()))
                    self.assertEqual(chip.asic_version, self.family)
                    self.assertEqual(chip.config.threshold_global, 73)
                    self.assertEqual(self.index('.asic_configs_.json')[str(iog)], 'fresh/iog')
                    configure_larpix.main(False, None, None, 'pacman.json')
                    configured = self.enforce.call_args.args[0]
                    self.assertEqual(next(iter(configured.chips.values())).config.threshold_global, 73)
                    self.io_factory.assert_called_with(relaxed=True, config_filepath='pacman.json',
                                                       asic_version=2 if iog == 1 else 3)
                finally:
                    os.chdir(previous)

    def test_valid_reuse_does_not_create_unused_directory(self):
        self.prepare()
        self.select_defaults()
        self.run_network()
        self.assertFalse(Path('fresh').exists())
        self.assertEqual(self.index('.asic_configs_.json')['1'], 'old')

    def test_explicit_refresh_preserves_old_files(self):
        self.prepare()
        self.select_defaults()
        original = {p.name: p.read_bytes() for p in Path('old').glob('*.json')}
        self.run_network(update_default=True)
        self.assertEqual(self.index('.default_asic_configs_.json')['1'], 'fresh/iog')
        self.assertEqual(original, {p.name: p.read_bytes() for p in Path('old').glob('*.json')})

    def test_existing_output_is_never_overwritten(self):
        self.prepare()
        Path('fresh/iog').mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            self.run_network(update_default=True)
        self.replay2.assert_not_called()
        self.io_factory.assert_not_called()

    def test_actual_v3_mismatch_is_reported_before_io(self):
        self.prepare(2, 37, 1)
        self.select_defaults()
        self.prepare(2, 39, 1)
        with self.assertRaisesRegex(ValueError, r'missing=\[.2-39-1.\].*extra=\[.2-37-1.\]'):
            self.run_network()
        self.replay3.assert_not_called()
        self.io_factory.assert_not_called()

    def test_empty_default_is_rejected(self):
        self.prepare()
        Path('empty').mkdir()
        Path('.default_asic_configs_.json').write_text('{"1": "empty"}')
        with self.assertRaisesRegex(ValueError, 'missing=.*1-25-21'):
            self.run_network()
        self.io_factory.assert_not_called()

    def test_wrong_config_family_is_rejected_before_io(self):
        self.prepare()
        self.select_defaults()
        path = next(Path('old').glob('*.json'))
        path.write_text(json.dumps({'meta': {'CHIP_KEY': '1-25-21', 'ASIC_ID': '1-7-21', 'ASIC_VERSION': 3}}))
        with self.assertRaisesRegex(ValueError, 'wrong IOG/family=.*1-25-21'):
            self.run_network()
        self.io_factory.assert_not_called()

    def test_wrong_topology_family_or_group_is_rejected_before_io(self):
        self.prepare()
        payload = json.loads(Path('topology.json').read_text())
        payload['asic_version'] = 3
        Path('topology.json').write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'ASIC family'):
            self.run_network()
        self.io_factory.assert_not_called()

    def test_configure_leaf_path_and_unique_channel_remapping(self):
        self.prepare()
        self.select_defaults()
        self.prepare(1, 26, 21)
        Path('.network_configs_.json').write_text('{"1": "topology.json"}')
        configure_larpix.main(False, 'old', None, 'pacman.json')
        keys = self.enforce.call_args.args[1]
        self.assertEqual([[str(key) for key in net] for net in keys], [['1-25-21']])

    def test_configure_missing_chip_is_not_silently_dropped(self):
        self.prepare()
        self.select_defaults()
        self.prepare(1, 26, 22)
        Path('.network_configs_.json').write_text('{"1": "topology.json"}')
        with self.assertRaisesRegex(ValueError, 'missing=.*1-26-22'):
            configure_larpix.main(False, 'old', None, 'pacman.json')
        self.enforce.assert_not_called()
        self.io_factory.assert_not_called()

    def test_failure_preserves_default_and_invalidates_current_state(self):
        self.prepare()
        self.select_defaults()
        with mock.patch.object(network_larpix, 'enforce_iterative', return_value=(False, {'failed': {}}, [])):
            with self.assertRaisesRegex(RuntimeError, 'Unconfigured'):
                self.run_network(update_default=True)
        self.assertEqual(self.index('.default_asic_configs_.json')['1'], 'old')
        self.assertIsNone(self.index('.asic_configs_.json')['1'])
        self.assertIsNone(self.index('.network_configs_.json')['1'])


class WrapperTest(unittest.TestCase):
    def test_wrappers_forward_arguments_and_child_failures(self):
        # Only a temporary copy of each wrapper runs. PATH's python is a stub;
        # it records argv and never opens a Python production entry point.
        for wrapper in ('network', 'configure'):
            with self.subTest(wrapper=wrapper), tempfile.TemporaryDirectory() as directory:
                tmp = Path(directory)
                (tmp / 'bin').mkdir()
                stub = tmp / 'bin/python'
                stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n'
                                'case "$*" in *pacman_io2.json*) exit 7;; esac\n')
                stub.chmod(0o755)
                (tmp / '.envrc').write_text('IOG1_PID=0\nIOG2_PID=0\n')
                (tmp / 'wrapper.sh').write_bytes((ROOT / f'run/iog/{wrapper}.sh').read_bytes())
                args = ['bash', 'wrapper.sh', 'all']
                if wrapper == 'network':
                    args.append('--update_default')
                else:
                    args.append('selected-parent')
                env = dict(os.environ, PATH=str(tmp / 'bin') + os.pathsep + os.environ['PATH'])
                result = subprocess.run(args, cwd=tmp, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 7, result.stderr)
                logs = list((tmp / 'log').glob('*.log'))
                self.assertEqual(len(logs), 2)
                for log in logs:
                    content = log.read_text()
                    self.assertIn('--pacman_config', content)
                    self.assertIn('--update_default' if wrapper == 'network' else '--config_subdir', content)
                self.assertFalse((tmp / 'asic_configs').exists())


class DonorCompatibilityTest(unittest.TestCase):
    def test_unavailable_links_are_skipped_and_invalid_channels_fail(self):
        io = FakeIO()
        self.assertIsNone(pacman_base.convert_io_channel_to_uart(40))
        pacman_base.enable_pacman_uart_from_io_channels(io, 2, [37, 38, 39, 40])
        self.assertEqual(io.registers[(2, 0x201C)], 0x1FFFFFFF)
        pacman_base.enable_pacman_uart_from_io_channel(io, 2, 40)
        self.assertEqual(io.registers[(2, 0x201C)], 0xFFFFFFFF)
        with self.assertRaises(ValueError):
            pacman_base.enable_pacman_uart_from_io_channels(io, 2, [41])

    def test_original_positional_delay_contract(self):
        io = FakeIO()
        pacman_base.set_packet_delay(io, 2, 0xAB)
        self.assertEqual(len(io.registers), 32)
        for physical in range(32):
            self.assertEqual(io.registers[(2, 0x03014 + physical * 0x1000)], 0xAB5A)

    def test_v3_daughter_uses_donor_uart_directions_without_v2d_register(self):
        c = larpix.Controller()
        parent, daughter = larpix.Key(2, 37, 151), larpix.Key(2, 37, 152)
        c.add_chip(parent, version=3)
        with mock.patch.object(c, 'write_configuration') as write, \
             mock.patch.object(utility_base, 'reconcile_configuration', return_value=(True, {})):
            ok, _, piso = network_base_FSD_v3.setup_daughter(
                c, FakeIO(), parent, daughter, False, 3, 5, 7, 15, 7, 0)
        self.assertTrue(ok)
        self.assertEqual(piso, 2)
        self.assertEqual(c[daughter].config.enable_posi, [0, 0, 0, 1])
        self.assertEqual(c[daughter].config.enable_piso_downstream, [0, 0, 1, 0])
        self.assertFalse(any(call.args[1] == 'ref_current_trim' for call in write.call_args_list))


class V3DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        previous = Path.cwd()
        os.chdir(tmp)
        self.stack.callback(os.chdir, previous)
        Path('RUN_CONFIG.json').write_bytes((ROOT / 'RUN_CONFIG.json').read_bytes())
        Path('pacman.json').write_text('{"io_group": [[2, "192.0.2.2"]]}')
        for name in ('.asic_configs_.json', '.network_configs_.json'):
            Path(name).write_text('{}')
        self.io = mock.MagicMock()
        self.io.get_reg.return_value = 1
        self.io_factory = self.stack.enter_context(mock.patch.object(larpix.io, 'PACMAN_IO', return_value=self.io))
        self.stack.enter_context(mock.patch.object(larpix.Controller, 'write_configuration'))
        self.stack.enter_context(mock.patch.object(utility_base, 'reconcile_configuration', return_value=(True, {})))
        self.stack.enter_context(mock.patch.object(hydra_v3.time, 'sleep'))

    def discover(self):
        return hydra_v3.main(2, pacman_tile=10, pacman_config='pacman.json', file_prefix='fresh')

    def test_real_donor_traversal_waitlist_export_and_replay_with_mocked_hardware(self):
        with mock.patch.object(network_base_FSD_v3, 'initial_network', wraps=network_base_FSD_v3.initial_network) as initial, \
             mock.patch.object(network_base_FSD_v3, 'iterate_waitlist', wraps=network_base_FSD_v3.iterate_waitlist) as waitlist:
            c, _ = self.discover()
        initial.assert_called_once()
        waitlist.assert_called_once()
        self.assertEqual(len(c.chips), 160)
        self.assertEqual(set(c.network[2]), {37, 38, 39})
        filename = 'fresh-hydra-network.json'
        loaded = larpix.Controller()
        loaded.load(filename)
        self.assertEqual(set(loaded.chips), set(c.chips))
        # Exercise the actual replay and library graph initialization; only
        # packet transport and readback are replaced with mocks.
        with mock.patch.object(larpix.Controller, 'send'):
            replay = network_base_FSD_v3.network_v3(filename, pacman_config='pacman.json')
        self.assertEqual(set(replay.chips), set(c.chips))
        self.io_factory.assert_called_with(relaxed=True, config_filepath='pacman.json', asic_version=3)
        for key in c.chips:
            for field in ('enable_piso_upstream', 'enable_piso_downstream', 'enable_posi'):
                self.assertEqual(list(getattr(c[key].config, field)), list(getattr(replay[key].config, field)), (str(key), field))

    def test_failed_live_root_cannot_export(self):
        with mock.patch.object(network_base_FSD_v3, 'setup_root', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'Live root 2-37-151'):
                self.discover()
        self.assertFalse(Path('fresh-hydra-network.json').exists())

    def test_incomplete_discovery_cannot_export(self):
        with mock.patch.object(network_base_FSD_v3, 'initial_network'), \
             mock.patch.object(network_base_FSD_v3, 'iterate_waitlist', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'undiscovered ASICs'):
                self.discover()
        self.assertFalse(Path('fresh-hydra-network.json').exists())

    def test_existing_output_rejected_before_io(self):
        Path('fresh-hydra-network.json').write_text('known-good')
        with self.assertRaises(FileExistsError):
            self.discover()
        self.io_factory.assert_not_called()
        self.assertEqual(Path('fresh-hydra-network.json').read_text(), 'known-good')


class SavedTopologyTest(unittest.TestCase):
    def test_real_v2d_fixture_matches_donor_directed_uart_graph(self):
        cern, donor = larpix.Controller(), larpix.Controller()
        cern.load(str(ROOT / 'tests/fixtures/cern_v2d_saved_network.json'))
        donor.load(str(ROOT / 'tests/fixtures/fsd_v2d_saved_network.json'))
        # The donor includes an unused external node on unavailable channel28.
        donor.network[1].pop(28)
        self.assertEqual(set(cern.chips), set(donor.chips))
        self.assertEqual(hardware_tests.NetworkConfigTest._edge_signature(cern), hardware_tests.NetworkConfigTest._edge_signature(donor))
        with mock.patch.object(larpix.Controller, 'send'):
            for c in (cern, donor):
                for channel in c.network[1]:
                    c.init_network(1, channel)
        for key in cern.chips:
            for field in ('enable_piso_upstream', 'enable_piso_downstream', 'enable_posi'):
                self.assertEqual(list(getattr(cern[key].config, field)), list(getattr(donor[key].config, field)))
        expected = hardware_tests.NetworkConfigTest._edge_signature(cern)
        with tempfile.TemporaryDirectory() as directory:
            saved = network_base_FSD.write_network_to_file(cern, str(Path(directory) / 'saved'), {1: [7]}, [], asic_version='2d')
            reloaded = larpix.Controller()
            reloaded.load(saved)
            # configure_asic_network_links (inherited from FSD) adds auxiliary
            # root-neighbor DS/POSI edges to its input controller; the exported
            # graph must still reproduce the original saved graph.
            self.assertEqual(expected, hardware_tests.NetworkConfigTest._edge_signature(reloaded))
