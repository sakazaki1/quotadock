#!/usr/bin/env python3
"""Claude Code status-line helper: mirror plan usage to a local file for QuotaDock."""
import argparse
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_STATE = '~/.claude/statusline-state.json'
WINDOWS = ('five_hour', 'seven_day')


def usable(window):
    if not isinstance(window, dict):
        return None
    used = window.get('used_percentage')
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
        return None
    entry = {'used_percentage': used}
    resets = window.get('resets_at')
    if (isinstance(resets, (int, float)) and not isinstance(resets, bool)) or isinstance(resets, str):
        entry['resets_at'] = resets
    return entry


def extract(payload):
    limits = payload.get('rate_limits') if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        return {}
    return {name: entry for name in WINDOWS if (entry := usable(limits.get(name)))}


def write_state(state_path, windows):
    path = Path(os.path.expanduser(state_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    merged = {name: previous[name] for name in WINDOWS if isinstance(previous, dict) and isinstance(previous.get(name), dict)}
    merged.update(windows)
    handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.quotadock-', delete=False)
    try:
        with handle:
            json.dump({'updated_at': int(time.time()), **merged}, handle)
        try:
            os.chmod(handle.name, 0o600)
        except OSError:
            pass
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def summary(windows):
    parts = [f"{label} {100 - windows[name]['used_percentage']:g}% left" for label, name in (('5h', 'five_hour'), ('7d', 'seven_day')) if name in windows]
    return 'Claude ' + ' · '.join(parts) if parts else ''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', default=DEFAULT_STATE)
    parser.add_argument('--then')
    args = parser.parse_args(argv)
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    windows = extract(payload)
    if windows:
        try:
            write_state(args.state, windows)
        except OSError:
            pass
    if args.then:
        try:
            command = [os.path.expanduser(part) for part in shlex.split(args.then, posix=os.name != 'nt')]
            sys.stdout.write(subprocess.run(command, input=raw, capture_output=True, text=True, timeout=5).stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    elif (line := summary(windows)):
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
