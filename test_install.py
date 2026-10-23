import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import install
import uninstall


@unittest.skipUnless(sys.platform == 'linux', 'Linux XDG installer test')
class InstallTests(unittest.TestCase):
    def test_install_update_and_uninstall_in_custom_xdg_directories(self):
        with tempfile.TemporaryDirectory() as root:
            data = Path(root) / 'data'
            config = Path(root) / 'config'
            with patch.dict(os.environ, {'XDG_DATA_HOME': str(data), 'XDG_CONFIG_HOME': str(config)}):
                with patch.object(sys, 'argv', ['install.py']):
                    install.main()
                desktop = data / 'applications/quotadock.desktop'
                startup = config / 'autostart/quotadock.desktop'
                self.assertIn('Exec=', desktop.read_text())
                self.assertEqual(desktop.read_text(), startup.read_text())
                self.assertTrue((data / 'quotadock/quotadock.py').is_file())
                with patch.object(sys, 'argv', ['install.py', '--no-autostart']):
                    install.main()
                self.assertFalse(startup.exists())
                self.assertFalse(list(desktop.parent.glob('.quotadock-*')))
                uninstall.main()
                self.assertFalse(desktop.exists())
                self.assertFalse((data / 'quotadock').exists())

    def test_relative_xdg_path_is_rejected(self):
        with patch.dict(os.environ, {'XDG_DATA_HOME': 'relative/data', 'XDG_CONFIG_HOME': '/tmp/config'}):
            with patch.object(sys, 'argv', ['install.py']):
                with self.assertRaises(SystemExit):
                    install.main()


if __name__ == '__main__':
    unittest.main()
