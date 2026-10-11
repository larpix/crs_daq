"""Run the inspected unit suites in temporary runtime state with IO blocked.

Usage: python -B tests/run_offline.py
Tests may replace PACMAN_IO with a fake, but no real socket/context is allowed.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / 'tests'))
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='crs-offline-') as temporary:
        os.chdir(temporary)
        Path('RUN_CONFIG.json').write_bytes((root / 'RUN_CONFIG.json').read_bytes())
        try:
            # Imports define classes only; PACMAN_IO construction opens ZMQ.
            import larpix.io
            import zmq
            import socket
            blocked = AssertionError('Real hardware/network access forbidden in offline tests')
            with mock.patch.object(larpix.io, 'PACMAN_IO', side_effect=blocked), \
                 mock.patch.object(zmq, 'Context', side_effect=blocked), \
                 mock.patch.object(socket.socket, 'connect', side_effect=blocked), \
                 mock.patch.object(socket.socket, 'connect_ex', side_effect=blocked), \
                 mock.patch.object(socket, 'create_connection', side_effect=blocked):
                suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern='test_*.py')
                result = unittest.TextTestRunner(verbosity=2).run(suite)
            print(f'OFFLINE RESULT: run={result.testsRun}, failures={len(result.failures)}, '
                  f'errors={len(result.errors)}, skipped={len(result.skipped)}')
            return 0 if result.wasSuccessful() and not result.skipped else 1
        finally:
            os.chdir(original)


if __name__ == '__main__':
    sys.exit(main())
