#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! /usr/bin/python3 -c 'import PyQt6' >/dev/null 2>&1; then
    printf '%s\n' 'PyQt6 is required. On Ubuntu/Kubuntu, run:' '  sudo apt install python3-pyqt6' 'Then run this installer again.'
    exit 1
fi
exec /usr/bin/python3 "$APP_DIR/install.py" "$@"
