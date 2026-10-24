import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HELPER = str(Path(__file__).with_name('claude_statusline_mirror.py'))
FUTURE = 4_000_000_000


def run(payload, *extra):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, HELPER, *extra], input=raw, capture_output=True, text=True, timeout=20)


class MirrorTests(unittest.TestCase):
    def test_writes_validated_windows_and_prints_a_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'sub' / 'state.json'
            result = run({'rate_limits': {'five_hour': {'used_percentage': 30.0, 'resets_at': FUTURE},
                                          'seven_day': {'used_percentage': 9, 'resets_at': FUTURE}}}, '--state', str(state))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), 'Claude 5h 70% left · 7d 91% left')
            written = json.loads(state.read_text())
            self.assertEqual(written['five_hour'], {'used_percentage': 30.0, 'resets_at': FUTURE})
            self.assertLessEqual(abs(written['updated_at'] - time.time()), 5)
            self.assertFalse(list(state.parent.glob('.quotadock-*')))

    def test_missing_or_invalid_limits_never_overwrite_the_last_good_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            good = {'rate_limits': {'five_hour': {'used_percentage': 30, 'resets_at': FUTURE}}}
            run(good, '--state', str(state))
            before = state.read_text()
            for bad in ({}, {'rate_limits': {}}, {'rate_limits': {'five_hour': {'used_percentage': 140}}},
                        {'rate_limits': {'five_hour': {'used_percentage': True}}}, 'not json'):
                result = run(bad, '--state', str(state))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout.strip(), '')
            self.assertEqual(state.read_text(), before)

    def test_a_render_with_one_window_keeps_the_other_known_window(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            run({'rate_limits': {'five_hour': {'used_percentage': 30, 'resets_at': FUTURE},
                                 'seven_day': {'used_percentage': 9, 'resets_at': FUTURE}}}, '--state', str(state))
            run({'rate_limits': {'five_hour': {'used_percentage': 35, 'resets_at': FUTURE}}}, '--state', str(state))
            written = json.loads(state.read_text())
            self.assertEqual(written['five_hour']['used_percentage'], 35)
            self.assertEqual(written['seven_day']['used_percentage'], 9)

    def test_chains_an_existing_status_line_with_the_same_input(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            existing = Path(directory) / 'existing.py'
            existing.write_text('import json,sys; print("model=" + json.load(sys.stdin)["model"])')
            result = run({'model': 'x', 'rate_limits': {'five_hour': {'used_percentage': 1, 'resets_at': FUTURE}}},
                         '--state', str(state), '--then', f'{sys.executable} {existing}')
            self.assertEqual(result.stdout.strip(), 'model=x')
            self.assertTrue(state.is_file())

    def test_a_broken_chained_command_does_not_break_the_status_line(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run({}, '--state', str(Path(directory) / 's.json'), '--then', '/definitely/not/a/command')
            self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
