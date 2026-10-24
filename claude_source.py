"""Optional local-file source for Claude Code status-line mirrors.

This module never reads browser data, credentials, cookies, or network endpoints.
"""
import json
import math
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal


def claude_bucket(payload):
    """Map a documented status-line mirror into QuotaDock's provider-neutral shape."""
    if not isinstance(payload, dict):
        return None
    result = {}
    for target, source in (('primary', 'five_hour'), ('secondary', 'seven_day')):
        item = payload.get(source)
        used = item.get('used_percentage') if isinstance(item, dict) else None
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
            continue
        result[target] = {
            'usedPercent': used,
            'resetsAt': item.get('resets_at'),
            'windowDurationMins': 300 if target == 'primary' else 10080,
        }
    return result or None


class ClaudeFileClient(QObject):
    """Polls an explicitly selected local status-line mirror while Claude Code runs."""
    updated = Signal(dict)
    failed = Signal(str)
    stale_after = None

    def __init__(self, state_path):
        super().__init__()
        self.path = Path(state_path).expanduser()
        self.poll = QTimer(self)
        self.poll.setInterval(15000)
        self.poll.timeout.connect(self.refresh)

    def start(self):
        self.refresh()
        self.poll.start()

    def refresh(self):
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.failed.emit('Claude usage file is unavailable.')
            return
        bucket = claude_bucket(payload)
        if bucket is None:
            self.failed.emit('No Claude quota is available in the usage file.')
            return
        self.updated.emit(bucket)

    def stop(self):
        self.poll.stop()
