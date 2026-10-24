import json
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEventLoop, QProcess, QTimer
from quotadock import QuotaClient, X11AppWindowWatcher


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_fragmented_and_batched_notifications(self):
        client = QuotaClient()
        received = []
        client.updated.connect(received.append)
        first = json.dumps({'method': 'account/rateLimits/updated', 'params': {
            'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 10}}}}).encode() + b'\n'
        second = json.dumps({'method': 'account/rateLimits/updated', 'params': {
            'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 20}}}}).encode() + b'\n'
        client.consume(first[:12])
        self.assertEqual(received, [])
        client.consume(first[12:] + b'not json\n' + second)
        self.assertEqual([item['primary']['usedPercent'] for item in received], [10, 20])
        client.stop()

    def test_oversized_incomplete_and_complete_lines_reconnect(self):
        for payload in (b'x' * 65, b'x' * 65 + b'\n'):
            client = QuotaClient()
            client.MAX_MESSAGE_BYTES = 64
            client.consume(payload)
            self.assertEqual(client.buffer, b'')
            self.assertTrue(client.retry.isActive())
            client.stop()
        client = QuotaClient()
        client.MAX_MESSAGE_BYTES = 64
        client.consume(b'{}\n' * 100)
        self.assertFalse(client.retry.isActive())
        self.assertEqual(client.buffer, b'')
        client.stop()

    def test_request_timeout_clears_pending_and_retries(self):
        client = QuotaClient()
        client.initialized = True
        client.pending = 4
        client.abort('Request timed out. Retrying…')
        self.assertIsNone(client.pending)
        self.assertFalse(client.initialized)
        self.assertTrue(client.retry.isActive())
        client.finished()
        self.assertTrue(client.retry.isActive())
        client.stop()

    def test_retry_backoff_increases_and_is_capped(self):
        client = QuotaClient()
        client.schedule_retry()
        first = client.retry.interval()
        client.retry.stop()
        client.schedule_retry()
        second = client.retry.interval()
        self.assertGreater(second, first)
        for _ in range(10):
            client.retry.stop()
            client.schedule_retry()
        self.assertLessEqual(client.retry.interval(), 138000)
        client.stop()

    def test_handshake_read_notification_and_single_pending_request(self):
        class Process:
            def __init__(self):
                self.writes = []

            def write(self, data):
                self.writes.append(json.loads(data))

        client = QuotaClient()
        client.process = Process()
        received = []
        client.updated.connect(received.append)
        client.handshake()
        self.assertEqual(client.process.writes[0]['method'], 'initialize')
        client.consume(json.dumps({'id': client.pending, 'result': {}}).encode() + b'\n')
        self.assertTrue(client.initialized)
        self.assertEqual(client.process.writes[-1]['method'], 'account/rateLimits/read')
        pending = client.pending
        client.refresh()
        self.assertEqual(client.pending, pending)
        self.assertEqual(len([item for item in client.process.writes if item['method'] == 'account/rateLimits/read']), 1)
        bucket = {'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 15}}}
        client.consume(json.dumps({'id': pending, 'result': bucket}).encode() + b'\n')
        self.assertIsNone(client.pending)
        self.assertEqual(received[-1]['primary']['usedPercent'], 15)
        client.process = None
        client.stop()

    @unittest.skipUnless(sys.platform == 'linux', 'X11 helper test requires Linux')
    def test_window_watcher_recovers_from_hang_and_missing_command(self):
        class OwnWindow:
            def winId(self):
                return 0

        watcher = X11AppWindowWatcher(OwnWindow())
        states = []
        watcher.changed.connect(lambda active, _: states.append(active))
        watcher.timeout.setInterval(50)
        watcher.command('active', '/usr/bin/sleep', ['5'])
        loop = QEventLoop()
        QTimer.singleShot(250, loop.quit)
        loop.exec()
        self.assertIsNone(watcher.stage)
        self.assertEqual(watcher.process.state(), QProcess.ProcessState.NotRunning)
        self.assertIn(False, states)
        watcher.command('active', '/path/that/does/not/exist', [])
        loop = QEventLoop()
        QTimer.singleShot(100, loop.quit)
        loop.exec()
        self.assertIsNone(watcher.stage)
        watcher.command('geometry', '/bin/false', [])
        loop = QEventLoop()
        QTimer.singleShot(100, loop.quit)
        loop.exec()
        self.assertIsNone(watcher.stage)
        watcher.stop()


if __name__ == '__main__':
    unittest.main()
