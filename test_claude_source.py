import json
import tempfile
import unittest
from pathlib import Path

from claude_source import ClaudeFileClient, claude_bucket


class ClaudeSourceTests(unittest.TestCase):
    def test_validates_and_maps_local_status_file(self):
        payload = {'five_hour': {'used_percentage': 30, 'resets_at': 100},
                   'seven_day': {'used_percentage': 9.5, 'resets_at': 200}}
        bucket = claude_bucket(payload)
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

