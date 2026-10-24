"""Optional local-file source for Claude Code status-line mirrors.

This module never reads browser data, credentials, cookies, or network endpoints.
"""
import json
import math
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

MAX_FILE_BYTES = 64 * 1024


def _epoch(value):
    """Return ``resets_at`` as epoch seconds. Accepts numbers or ISO-8601 text; anything else is unknown."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace('Z', '+00:00')).timestamp()
        except ValueError:
            return None
    return None


def claude_bucket(payload, now=None):
    """Map a documented status-line mirror into QuotaDock's provider-neutral shape.

    A window whose reset time has already passed is omitted rather than shown or zeroed:
    the old percentage no longer describes the new window, and QuotaDock never invents a value.
    """
    if not isinstance(payload, dict):
        return None
    now = time.time() if now is None else now
    result = {}
    for target, source in (('primary', 'five_hour'), ('secondary', 'seven_day')):
        item = payload.get(source)
        used = item.get('used_percentage') if isinstance(item, dict) else None
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
            continue
        resets_at = _epoch(item.get('resets_at'))
        if resets_at is not None and resets_at <= now:
            continue
        result[target] = {
            'usedPercent': used,
            'resetsAt': resets_at,
            'windowDurationMins': 300 if target == 'primary' else 10080,
        }
    return result or None


class ClaudeFileClient(QObject):
    """Polls an explicitly selected local status-line mirror while Claude Code runs."""
    updated = Signal(dict)
    failed = Signal(str)
    provider = 'claude'
    provider_label = 'Claude'
    source_id = 'claude-file'
    # Freshness is judged per window by its reset time (see claude_bucket), not by reading age.
    stale_after = None

    def __init__(self, state_path):
        super().__init__()
        self.path = Path(state_path).expanduser()
        self._last = None
        self.poll = QTimer(self)
        self.poll.setInterval(15000)
        self.poll.timeout.connect(self.refresh)

    def start(self):
        self.refresh()
        self.poll.start()

    def refresh(self):
        try:
            stat = self.path.stat()
            if stat.st_size > MAX_FILE_BYTES:
                raise ValueError('unexpectedly large usage file')
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self._last = None
            self.failed.emit('Claude usage file is unavailable.')
            return
        bucket = claude_bucket(payload)
        if bucket is None:
            self._last = None
            self.failed.emit('No Claude quota is available in the usage file.')
            return
        # Emit only when something changed, so "updated Ns ago" reflects real data, not our polling.
        signature = (stat.st_mtime_ns, json.dumps(bucket, sort_keys=True))
        if signature == self._last:
            return
        self._last = signature
        bucket['observedAt'] = stat.st_mtime
        self.updated.emit(bucket)

    def stop(self):
        self.poll.stop()
