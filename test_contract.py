import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from quotadock import QuotaClient, executable_identity, trusted_executable


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_stdio_fixture_completes_handshake_notification_and_read(self):
        stub = str(Path(__file__).with_name('test_app_server_stub.py'))
        client = QuotaClient(binary_resolver=lambda: sys.executable, process_arguments=[stub])
        received = []
        client.updated.connect(received.append)
        client.start()
        loop = QEventLoop()
        QTimer.singleShot(1500, loop.quit)
        client.updated.connect(loop.quit)
        loop.exec()
        self.assertTrue(received)
        self.assertEqual(received[-1]['primary']['usedPercent'], 25)
        client.stop()

    @unittest.skipIf(os.name == 'nt', 'POSIX file mode test')
    def test_rejects_group_or_other_writable_executables(self):
        target = Path(sys.executable)
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / 'codex'
            shutil.copy2(target, copy)
            copy.chmod(0o777)
            self.assertIsNone(trusted_executable(copy))


if __name__ == '__main__':
    unittest.main()
