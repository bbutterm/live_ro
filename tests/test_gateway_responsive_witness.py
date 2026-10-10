import contextlib
import json
import multiprocessing
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from packages.body_gateway.journal import Journal


def blocked_gateway(path, store, entered, release):
    from packages.body_gateway.service import serve
    def events(*args):
        entered.set()
        release.wait(3)
        return []
    with patch('packages.body_gateway.service.get_events', events), patch('packages.body_gateway.service.query', return_value=''):
        serve(path, store, 'resident_a', 150001, 'ResidentA', 2)


class ResponsiveWitnessTest(unittest.TestCase):
    def test_status_remains_responsive_while_server_witness_read_is_blocked(self):
        ctx = multiprocessing.get_context('fork')
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'body.sock')
            store = str(Path(tmp) / 'journal.sqlite')
            j = Journal(store)
            j.db.executemany('INSERT INTO cursors VALUES (?, ?)', [('witness', 1), ('chat', 1), ('picklog', 1)])
            j.db.commit()
            j.close()
            entered, release = ctx.Event(), ctx.Event()
            worker = ctx.Process(target=blocked_gateway, args=(path, store, entered, release))
            worker.start()
            try:
                self.assertTrue(entered.wait(2), 'server read must be in flight before control request')
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(.5)
                    client.connect(path + '.control')
                    client.sendall(b'{"op":"status"}\n')
                    data = client.recv(8192)
                self.assertTrue(json.loads(data)['ok'])
            finally:
                release.set()
                worker.join(4)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(2)
            self.assertEqual(worker.exitcode, 0)


if __name__ == '__main__':
    unittest.main()
