"""Codex and Claude windows running side by side in one process."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from claude_source import ClaudeFileClient
from quotadock import Bar

FUTURE = 4_000_000_000
PAST = 1_000_000_000


class MultiSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # Redirect both formats: QSettings resolves to the native .conf file, so IniFormat alone would
        # leave these tests writing into the developer's real ~/.config/QuotaDock.
        for settings_format in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
            QSettings.setPath(settings_format, QSettings.Scope.UserScope, self.directory.name)
        QSettings('QuotaDock', 'QuotaDock').clear()
        self.state = Path(self.directory.name) / 'claude.json'

    def write_state(self, five_hour, seven_day):
        self.state.write_text(json.dumps({'five_hour': five_hour, 'seven_day': seven_day}))

    def bars(self):
        codex = Bar()
        claude = Bar(ClaudeFileClient(self.state))
        self.addCleanup(codex.cleanup)
        self.addCleanup(claude.cleanup)
        return codex, claude

    def test_each_window_shows_only_its_own_provider_data(self):
        self.write_state({'used_percentage': 30, 'resets_at': FUTURE}, {'used_percentage': 9, 'resets_at': FUTURE})
        codex, claude = self.bars()
        codex.update_quota({'primary': {'usedPercent': 75, 'windowDurationMins': 300},
                            'secondary': {'usedPercent': 4, 'windowDurationMins': 10080}})
        claude.client.refresh()
        self.assertEqual((codex.rows[0][1].text(), codex.rows[1][1].text()), ('25%', '96%'))
        self.assertEqual((claude.rows[0][1].text(), claude.rows[1][1].text()), ('70%', '91%'))

    def test_preferences_are_isolated_but_codex_keeps_its_historical_keys(self):
        codex, claude = self.bars()
        codex.set_theme('translucent')
        claude.set_theme('dashboard')
        stored = QSettings('QuotaDock', 'QuotaDock')
        self.assertEqual(stored.value('theme'), 'translucent')       # unchanged layout for existing installs
        self.assertEqual(stored.value('claude/theme'), 'dashboard')
        self.assertEqual((codex.theme, claude.theme), ('translucent', 'dashboard'))

    def test_claude_window_is_branded_and_never_anchors_to_a_codex_host(self):
        codex, claude = self.bars()
        self.assertIn('Claude', claude.title_label.text())
        self.assertIn('Claude', claude.windowTitle())
        self.assertIn('Claude', claude.tray.toolTip())
        self.assertNotIn('Claude', codex.title_label.text())
        self.assertFalse(claude.app_only)
        self.assertFalse(claude.compact_tag.isHidden())   # compact bars must be tellable apart
        self.assertTrue(codex.compact_tag.isHidden())
        self.assertNotEqual(codex.t('Remaining percentage reported by Codex.'), claude.t('Remaining percentage reported by Codex.'))

    def test_expired_claude_window_shows_unavailable_instead_of_old_value(self):
        self.write_state({'used_percentage': 80, 'resets_at': PAST}, {'used_percentage': 9, 'resets_at': FUTURE})
        _, claude = self.bars()
        claude.client.refresh()
        self.assertEqual(claude.rows[0][1].text(), '—')
        self.assertEqual(claude.rows[1][1].text(), '91%')

    def test_missing_claude_file_degrades_only_the_claude_window(self):
        codex, claude = self.bars()
        codex.update_quota({'primary': {'usedPercent': 10, 'windowDurationMins': 300}})
        claude.client.refresh()
        self.assertTrue(claude.is_failed)
        self.assertFalse(codex.is_failed)

    def test_language_choice_follows_between_windows(self):
        codex, claude = self.bars()
        claude.language_changed.connect(codex.follow_language)
        codex.language_changed.connect(claude.follow_language)
        claude.set_language('pt')
        self.assertEqual(codex.language, 'pt')
        self.assertEqual(codex.minimal_action.text(), 'Interface minimalista')
        codex.set_language('en')
        self.assertEqual(claude.language, 'en')

    def test_history_source_label_comes_from_the_source(self):
        codex, claude = self.bars()
        self.assertEqual(codex.client.source_id, 'codex')
        self.assertEqual(claude.client.source_id, 'claude-file')


if __name__ == '__main__':
    unittest.main()
