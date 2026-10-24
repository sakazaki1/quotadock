import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from claude_source import ClaudeFileClient, claude_bucket


class ClaudeSourceTests(unittest.TestCase):
    def test_validates_and_maps_local_status_file(self):
        payload = {'five_hour': {'used_percentage': 30, 'resets_at': 100},
                   'seven_day': {'used_percentage': 9.5, 'resets_at': 200}}
        bucket = claude_bucket(payload, now=50)
        self.assertEqual(bucket['primary']['usedPercent'], 30)
        self.assertEqual(bucket['secondary']['windowDurationMins'], 10080)
        self.assertIsNone(claude_bucket({'five_hour': {'used_percentage': 101}}))

    def test_client_never_treats_missing_file_as_quota(self):
        client = ClaudeFileClient('/definitely/not/a/claude-state.json')
        errors = []
        client.failed.connect(errors.append)
        client.refresh()
        self.assertEqual(errors, ['Claude usage file is unavailable.'])
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            state.write_text(json.dumps({'five_hour': {'used_percentage': 30}}))
            client = ClaudeFileClient(state)
            received = []
            client.updated.connect(received.append)
            client.refresh()
            self.assertEqual(received[0]['primary']['usedPercent'], 30)

    def test_expired_window_is_dropped_never_zeroed(self):
        payload = {'five_hour': {'used_percentage': 80, 'resets_at': 100},
                   'seven_day': {'used_percentage': 9, 'resets_at': 10_000}}
        bucket = claude_bucket(payload, now=500)
        self.assertNotIn('primary', bucket)
        self.assertEqual(bucket['secondary']['usedPercent'], 9)
        self.assertIsNone(claude_bucket({'five_hour': payload['five_hour']}, now=500))

    def test_accepts_iso_reset_times(self):
        expected = datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp()
        bucket = claude_bucket({'five_hour': {'used_percentage': 42, 'resets_at': '2099-01-01T00:00:00Z'}}, now=0)
        self.assertEqual(bucket['primary']['resetsAt'], expected)
        self.assertEqual(claude_bucket({'five_hour': {'used_percentage': 42, 'resets_at': 'garbage'}}, now=0)['primary']['resetsAt'], None)

    def test_client_emits_only_when_data_changes_and_reports_file_age(self):
        future = 4_000_000_000
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            state.write_text(json.dumps({'five_hour': {'used_percentage': 30, 'resets_at': future}}))
            client = ClaudeFileClient(state)
            received = []
            client.updated.connect(received.append)
            client.refresh()
            client.refresh()
            self.assertEqual(len(received), 1)
            self.assertAlmostEqual(received[0]['observedAt'], state.stat().st_mtime, places=3)
            state.write_text(json.dumps({'five_hour': {'used_percentage': 31, 'resets_at': future}}))
            client.refresh()
            self.assertEqual(len(received), 2)
            self.assertEqual(received[1]['primary']['usedPercent'], 31)

    def test_oversized_file_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            state.write_text(' ' * (70 * 1024) + '{}')
            client = ClaudeFileClient(state)
            errors = []
            client.failed.connect(errors.append)
            client.refresh()
            self.assertEqual(errors, ['Claude usage file is unavailable.'])
