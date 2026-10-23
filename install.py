#!/usr/bin/env python3
"""Install QuotaDock for the current user. No administrator access required."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil


def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-autostart', action='store_true', help='Do not start QuotaDock at login')
    args = parser.parse_args()
    if importlib.util.find_spec('PyQt6') is None:
        parser.exit(1, 'PyQt6 is missing. On Ubuntu/Kubuntu: sudo apt install python3-pyqt6\n')
    source = Path(__file__).resolve().parent
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    target = data / 'quotadock'
    target.mkdir(parents=True, exist_ok=True)
    for name in ('quotadock.py', 'limits.py', 'launch.sh', 'README.md', 'LICENSE', 'uninstall.py'):
        if (source / name).resolve() != (target / name).resolve():
            shutil.copy2(source / name, target / name)
    (target / 'launch.sh').chmod(0o755)
    desktop = '\n'.join([
        '[Desktop Entry]', 'Type=Application', 'Name=QuotaDock',
        'Comment=Live Codex quota in a floating window and system tray',
        'Exec=' + desktop_quote(target / 'launch.sh'),
        'Icon=utilities-system-monitor', 'Terminal=false', 'Categories=Utility;',
        'StartupNotify=false', 'X-KDE-autostart-after=panel', '',
    ])
    applications = data / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    (applications / 'quotadock.desktop').write_text(desktop, encoding='utf-8')
    autostart = config / 'autostart/quotadock.desktop'
    if args.no_autostart:
        autostart.unlink(missing_ok=True)
    else:
        autostart.parent.mkdir(parents=True, exist_ok=True)
        autostart.write_text(desktop, encoding='utf-8')
    print('Installed! Open QuotaDock from your application menu.')
    print('Start at login: ' + ('disabled' if args.no_autostart else 'enabled'))


if __name__ == '__main__':
    main()
