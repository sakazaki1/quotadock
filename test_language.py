import os
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
from quotadock import Bar


class LanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_default_switch_and_saved_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, directory)
            settings = QSettings('QuotaDock', 'QuotaDock')
            settings.clear()
            bar = Bar()
            self.assertEqual(bar.language, 'en')
            self.assertEqual(bar.minimal_action.text(), 'Minimal interface')
            bar.set_language('pt')
            self.assertEqual(bar.minimal_action.text(), 'Interface minimalista')
            bar.error('Connection closed. Reconnecting…')
            self.assertIn('Reconectando', bar.status.text())
            self.assertEqual(QSettings('QuotaDock', 'QuotaDock').value('language'), 'pt')
            bar.set_language('en')
            self.assertEqual(bar.minimal_action.text(), 'Minimal interface')
            self.assertIn('Reconnecting', bar.status.text())
            bar.cleanup()


if __name__ == '__main__':
    unittest.main()
