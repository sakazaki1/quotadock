import os
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
from quotadock import Bar


class ResponsiveLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dashboard_remains_readable_at_small_and_large_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, directory)
            QSettings('QuotaDock', 'QuotaDock').clear()
            bar = Bar()
            try:
                bar.set_app_only(False)
                bar.set_language('pt')
                bar.set_theme('dashboard')
                bar.set_display_mode('standard')
                bar.resize(340, 220)
                bar.update_quota({'primary': {'usedPercent': 17, 'windowDurationMins': 300},
                                  'secondary': {'usedPercent': 4, 'windowDurationMins': 10080}})
                self.app.processEvents()

                self.assertTrue(bar.dashboard_footer.isHidden())
                self.assertTrue(bar.status.isHidden())
                for panel, (_, value, progress) in zip(bar.panel_widgets, bar.rows):
                    self.assertTrue(value.isVisible())
                    self.assertLess(value.geometry().bottom(), progress.geometry().top())
                    self.assertLess(progress.mapTo(bar, progress.rect().bottomRight()).y(), bar.height())
                small_font = bar.rows[0][1].font().pixelSize()

                bar.resize(600, 420)
                self.app.processEvents()
                self.assertTrue(bar.dashboard_footer.isVisible())
                self.assertGreater(bar.rows[0][1].font().pixelSize(), small_font)

                bar.set_display_mode('dashboard_strip')
                bar.resize(300, 58)
                self.app.processEvents()
                self.assertNotIn('QuotaDock', bar.strip_brand.text())
                self.assertIn('Semana  96%', bar.strip_segments[1][1].text())
                bar.resize(440, 68)
                self.app.processEvents()
                self.assertIn('QuotaDock', bar.strip_brand.text())
            finally:
                bar.cleanup()


if __name__ == '__main__':
    unittest.main()
