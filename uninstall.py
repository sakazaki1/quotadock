#!/usr/bin/env python3
"""Remove only the per-user QuotaDock installation and preferences."""
import os
from pathlib import Path
import shutil


def main():
    home = Path.home()
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(home / '.config')))
    data = Path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share')))
    for entry in (data / 'applications/quotadock.desktop', config / 'autostart/quotadock.desktop'):
        entry.unlink(missing_ok=True)
    target = data / 'quotadock'
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)
    (config / 'QuotaDock/QuotaDock.conf').unlink(missing_ok=True)
    print('QuotaDock removed. If it is running, use its tray menu → Quit.')


if __name__ == '__main__':
    main()
