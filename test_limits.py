import unittest
from limits import codex_bucket, remaining, window_label


class LimitsTests(unittest.TestCase):
    def test_remaining_including_exhausted_and_unknown(self):
        for used, expected in [(0, 100), (6, 94), (100, 0), (25.5, 74.5)]:
            self.assertEqual(remaining({'usedPercent': used}), expected)
        for invalid in [None, {}, {'usedPercent': None}, {'usedPercent': True}, {'usedPercent': -1}, {'usedPercent': 101}, {'usedPercent': float('nan')}]:
            self.assertIsNone(remaining(invalid))

    def test_multi_bucket_never_substitutes_another_quota(self):
        codex = {'limitId': 'codex'}
        self.assertEqual(codex_bucket({'rateLimitsByLimitId': {'codex': codex}}), codex)
        self.assertIsNone(codex_bucket({'rateLimitsByLimitId': {'other': {}}, 'rateLimits': codex}))
        self.assertIsNone(codex_bucket({'rateLimits': {'limitId': 'other'}}))
        self.assertEqual(codex_bucket({'rateLimits': codex}), codex)

    def test_actual_window_duration(self):
        self.assertEqual(window_label({'windowDurationMins': 300}, '?'), '5h')
        self.assertEqual(window_label({'windowDurationMins': 10080}, '?'), 'Week')
        self.assertEqual(window_label(None, 'Window'), 'Window')


if __name__ == '__main__':
    unittest.main()
