#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# On KDE Wayland, XWayland supports native movement, resizing and keep-above.
if [ -n "${DISPLAY:-}" ]; then
    export QT_QPA_PLATFORM=xcb
fi
exec /usr/bin/python3 "$APP_DIR/quotadock.py" "$@"
