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
from base import config_loader, enforce_parallel, pacman_base
from base.network_config import root_only_network

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
