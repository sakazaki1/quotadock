#!/usr/bin/env python3
"""Install QuotaDock for the current user. No administrator access required."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile


def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.quotadock-', delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-autostart', action='store_true', help='Do not start QuotaDock at login')
    args = parser.parse_args()
    if importlib.util.find_spec('PySide6') is None:
        parser.exit(1, 'PySide6 is missing. Install it with: python3 -m pip install --user PySide6\n')
    source = Path(__file__).resolve().parent
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    if not data.is_absolute() or not config.is_absolute():
        parser.error('XDG_DATA_HOME and XDG_CONFIG_HOME must be absolute paths.')
    target = data / 'quotadock'
    target.mkdir(parents=True, exist_ok=True)
    for name in ('quotadock.py', 'limits.py', 'i18n.py', 'quota_core.py', 'claude_source.py', 'launch.sh', 'README.md', 'LICENSE', 'uninstall.py', 'quotadock.svg'):
        if (source / name).resolve() != (target / name).resolve():
            shutil.copy2(source / name, target / name)
    (target / 'launch.sh').chmod(0o755)
    desktop = '\n'.join([
        '[Desktop Entry]', 'Type=Application', 'Name=QuotaDock',
        'Comment=Live Codex quota in a floating window and system tray',
        'Exec=' + desktop_quote(target / 'launch.sh'),
        'Icon=' + desktop_quote(target / 'quotadock.svg'), 'Terminal=false', 'Categories=Utility;',
        'StartupNotify=false', 'X-KDE-autostart-after=panel', '',
    ])
    applications = data / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    atomic_write(applications / 'quotadock.desktop', desktop)
    autostart = config / 'autostart/quotadock.desktop'
    if args.no_autostart:
        autostart.unlink(missing_ok=True)
    else:
        autostart.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(autostart, desktop)
    print('Installed! Open QuotaDock from your application menu.')
    print('Start at login: ' + ('disabled' if args.no_autostart else 'enabled'))


if __name__ == '__main__':
    main()
