"""Install QuotaDock for the current Windows user without administrator rights."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def shortcut(path, pythonw, script, folder):
    args = '"' + str(script) + '"'
    command = (
        '$link = (New-Object -ComObject WScript.Shell).CreateShortcut(' + ps_literal(path) + '); '
        '$link.TargetPath = ' + ps_literal(pythonw) + '; '
        '$link.Arguments = ' + ps_literal(args) + '; '
        '$link.WorkingDirectory = ' + ps_literal(folder) + '; '
        '$link.Save()'
    )
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-autostart', action='store_true', help='Do not start QuotaDock at login')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('This installer is for Windows. Use install.sh on Linux.')
    local = os.environ.get('LOCALAPPDATA')
    roaming = os.environ.get('APPDATA')
    if not local or not roaming:
        parser.error('LOCALAPPDATA and APPDATA must be set by Windows.')
    source = Path(__file__).resolve().parent
    target = Path(local) / 'QuotaDock'
    target.mkdir(parents=True, exist_ok=True)
    environment = target / '.venv'
    if not (environment / 'Scripts' / 'pythonw.exe').is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / 'Scripts' / 'python.exe'
    pythonw = environment / 'Scripts' / 'pythonw.exe'
    subprocess.run([str(python), '-m', 'pip', 'install', 'PyQt6>=6.5,<7'], check=True)
    for name in ('quotadock.py', 'windows_watcher.py', 'limits.py', 'quotadock.svg'):
        shutil.copy2(source / name, target / name)
    menu = Path(roaming) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
    startup = menu / 'Startup'
    shortcut(menu / 'QuotaDock.lnk', pythonw, target / 'quotadock.py', target)
    if args.no_autostart:
        (startup / 'QuotaDock.lnk').unlink(missing_ok=True)
    else:
        shortcut(startup / 'QuotaDock.lnk', pythonw, target / 'quotadock.py', target)
    print('QuotaDock installed. Open it from the Start menu.')
    print('Start at login: ' + ('disabled' if args.no_autostart else 'enabled'))


if __name__ == '__main__':
    main()
