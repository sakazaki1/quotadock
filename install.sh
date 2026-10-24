#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! /usr/bin/python3 -c 'import PySide6' >/dev/null 2>&1; then
    printf '%s\n' 'PySide6 is required. On Ubuntu/Kubuntu, run:' '  sudo apt install python3-pyside6.qtwidgets' 'Then run this installer again.'
    exit 1
fi
exec /usr/bin/python3 "$APP_DIR/install.py" "$@"
