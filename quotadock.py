#!/usr/bin/python3
"""Native movable quota monitor for KDE. Uses the signed-in local Codex app-server."""
import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QLockFile, QObject, QProcess, QSettings, QStandardPaths, QUrl, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget
from history import QuotaHistory
from limits import codex_bucket, remaining, window_label
from quota_core import QuotaSource
from i18n import translate

VERSION = '1.1.0'


class ProviderSettings:
    """QSettings view for one quota provider.

    Codex keeps the historical flat keys, so existing installs keep their layout. Other providers get
    their own ``<provider>/`` namespace, which lets several windows run in one process without sharing
    position, theme or alert choices. Only the interface language is shared.
    """
    SHARED = frozenset({'language'})

    def __init__(self, provider):
        self.provider = provider
        self._settings = QSettings('QuotaDock', 'QuotaDock')

    def _key(self, key):
        return key if self.provider == 'codex' or key in self.SHARED else f'{self.provider}/{key}'

    def value(self, key, default=None, type=None):
        if type is None:
            return self._settings.value(self._key(key), default)
        return self._settings.value(self._key(key), default, type=type)

    def setValue(self, key, value):
        self._settings.setValue(self._key(key), value)


def trusted_executable(path):
    """Return an executable only when it is safe to run as the current user."""
    try:
        candidate = Path(path).expanduser().resolve(strict=True)
        stat = candidate.stat()
    except OSError:
        return None
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None
    if os.name != 'nt':
        # A binary writable by another account could be swapped after discovery.
        if stat.st_mode & 0o022:
            return None
        if stat.st_uid not in (os.getuid(), 0):
            return None
    return str(candidate)


def executable_identity(path):
    """Stable, non-secret metadata used to warn about an executable swap."""
    try:
        stat = Path(path).stat()
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None
    return f'{stat.st_uid}:{stat.st_mode & 0o777:o}:{stat.st_size}:{stat.st_mtime_ns}:{digest}'


def executable():
    override = os.environ.get('QUOTADOCK_CODEX')
    if override:
        path = Path(override).expanduser()
        if sys.platform == 'win32' and path.suffix.casefold() != '.exe':
            return None
        return trusted_executable(path)
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA')
        if local:
            for relative in ('Programs/ChatGPT/resources/codex.exe', 'Programs/Codex/resources/codex.exe'):
                path = Path(local) / relative
                trusted = trusted_executable(path)
                if trusted:
                    return trusted
        return trusted_executable(shutil.which('codex.exe')) if shutil.which('codex.exe') else None
    for location in ('/usr/lib/chatgpt/resources/codex', '/usr/lib/codex/resources/codex',
                     '/opt/ChatGPT/resources/codex', '/opt/Codex/resources/codex'):
        path = Path(location)
        trusted = trusted_executable(path)
        if trusted:
            return trusted
    found = shutil.which('codex')
    return trusted_executable(found) if found else None


class QuotaClient(QObject):
    updated = Signal(dict)
    failed = Signal(str)
    resolved = Signal(str)
    MAX_MESSAGE_BYTES = 1024 * 1024
    provider = 'codex'
    provider_label = 'Codex'
    source_id = 'codex'

    def __init__(self, binary_resolver=executable, process_arguments=None):
        super().__init__()
        self.binary_resolver = binary_resolver
        self.process_arguments = process_arguments
        self.process = None
        self.buffer = b''
        self.serial = 0
        self.pending = None
        self.initialized = False
        self.stopping = False
        self.poll = QTimer(self)
        self.poll.setInterval(30000)
        self.poll.timeout.connect(self.refresh)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(20000)
        self.timeout.timeout.connect(lambda: self.abort('Request timed out. Retrying…'))
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry_attempt = 0
        self.retry.timeout.connect(self.start)
        self.stale_after = 75

    def send(self, method, params=None, request=True):
        obj = {'method': method}
        if request:
            self.serial += 1
            obj['id'] = self.serial
        if params is not None:
            obj['params'] = params
        self.process.write((json.dumps(obj) + '\n').encode())
        return obj.get('id')

    def start(self):
        if self.stopping:
            return
        self.retry.stop()
        binary = self.binary_resolver()
        if not binary:
            self.failed.emit('Codex not found. See README for setup.')
            self.schedule_retry()
            return
        self.resolved.emit(binary)
        self.initialized = False
        self.pending = None
        self.buffer = b''
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.started.connect(self.handshake)
        self.process.readyReadStandardOutput.connect(self.read)
        # Drain stderr without retaining tokens, account details or log files.
        self.process.readyReadStandardError.connect(lambda: self.process.readAllStandardError())
        self.process.errorOccurred.connect(lambda _: self.abort('Could not connect to Codex.'))
        self.process.finished.connect(self.finished)
        self.process.start(binary, self.process_arguments or ['app-server', '--stdio'])
        self.timeout.start()

    def handshake(self):
        self.pending = self.send('initialize', {'clientInfo': {'name': 'quotadock', 'version': VERSION}})

    def read(self):
        if self.stopping or self.retry.isActive():
            self.process.readAllStandardOutput()
            return
        self.consume(bytes(self.process.readAllStandardOutput()))

    def consume(self, data):
        while data:
            end = data.find(b'\n')
            if end < 0:
                if len(self.buffer) + len(data) > self.MAX_MESSAGE_BYTES:
                    self.buffer = b''
                    self.abort('Codex response exceeded the size limit. Reconnecting…')
                    return
                self.buffer += data
                return
            if len(self.buffer) + end > self.MAX_MESSAGE_BYTES:
                self.buffer = b''
                self.abort('Codex response exceeded the size limit. Reconnecting…')
                return
            line = self.buffer + data[:end]
            self.buffer = b''
            data = data[end + 1:]
            try:
                msg = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get('method') == 'account/rateLimits/updated':
                self.accept(msg.get('params', {}))
            elif msg.get('id') == self.pending and self.pending is not None:
                self.pending = None
                self.timeout.stop()
                if 'error' in msg:
                    self.failed.emit('Usage unavailable. Check your Codex sign-in.')
                    if self.initialized:
                        self.poll.start()
                    else:
                        self.abort('Could not initialize the Codex connection.')
                    continue
                if not self.initialized:
                    self.initialized = True
                    self.send('initialized', request=False)
                    self.refresh()
                    self.poll.start()
                else:
                    self.accept(msg.get('result', {}))

    def accept(self, payload):
        if not isinstance(payload, dict):
            self.failed.emit('Usage response unavailable.')
            return
        bucket = codex_bucket(payload)
        if bucket is None:
            self.failed.emit('No Codex quota available for this session.')
            return
        self.updated.emit(bucket)
        self.retry_attempt = 0

    def refresh(self):
        if self.initialized and self.pending is None:
            self.pending = self.send('account/rateLimits/read')
            self.timeout.start()

    def abort(self, reason):
        if self.stopping:
            return
        self.failed.emit(reason)
        self.poll.stop()
        self.timeout.stop()
        self.initialized = False
        self.pending = None
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        self.schedule_retry()

    def finished(self, *_):
        self.poll.stop()
        self.timeout.stop()
        self.initialized = False
        self.pending = None
        if not self.stopping and not self.retry.isActive():
            self.failed.emit('Connection closed. Reconnecting…')
            self.schedule_retry()

    def schedule_retry(self):
        """Retry with a capped exponential delay and small jitter."""
        if self.stopping or self.retry.isActive():
            return
        delay = min(120000, 2000 * (2 ** min(self.retry_attempt, 6)))
        jitter = int(delay * 0.15 * ((time.monotonic() * 1000) % 1))
        self.retry.setInterval(delay + jitter)
        self.retry_attempt += 1
        self.retry.start()

    def stop(self):
        self.stopping = True
        for timer in (self.poll, self.timeout, self.retry):
            timer.stop()
        if self.process:
            self.process.terminate()
            if not self.process.waitForFinished(1500):
                self.process.kill()
                self.process.waitForFinished(500)


class X11AppWindowWatcher(QObject):
    """Follow the active ChatGPT/Codex window through the X11 bridge on KDE."""
    changed = Signal(bool, QRect)

    def __init__(self, own_window, parent=None):
        super().__init__(parent)
        self.own_window = own_window
        self.xprop = shutil.which('xprop')
        self.xwininfo = shutil.which('xwininfo')
        self.available = bool(self.xprop and self.xwininfo)
        self.process = QProcess(self)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(lambda _: self.fail())
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.poll)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(2000)
        self.timeout.timeout.connect(self.fail)
        self.stage = None
        self.last_target_id = None
        self.last_target_active = False
        self.current_id = None

    def start(self):
        self.timer.start()
        self.poll()

    def stop(self):
        self.timer.stop()
        self.timeout.stop()
        self.stage = None
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        self.last_target_active = False

    def poll(self):
        if self.stage is not None or self.process.state() != QProcess.ProcessState.NotRunning:
            return
        if not self.xprop or not self.xwininfo:
            self.last_target_active = False
            self.changed.emit(False, QRect())
            return
        self.command('active', self.xprop, ['-root', '_NET_ACTIVE_WINDOW'])

    def command(self, stage, program, arguments):
        self.stage = stage
        self.timeout.start()
        self.process.start(program, arguments)

    def fail(self):
        self.timeout.stop()
        self.stage = None
        self.current_id = None
        self.last_target_id = None
        self.last_target_active = False
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        self.changed.emit(False, QRect())

    @staticmethod
    def window_id(output):
        match = re.search(rb'window id # (0x[0-9a-fA-F]+)', output)
        return match.group(1).decode() if match else None

    def finished(self, exit_code=0, *_):
        self.timeout.stop()
        if self.stage is None:
            return
        if exit_code != 0:
            self.fail()
            return
        output = bytes(self.process.readAllStandardOutput())
        stage, self.stage = self.stage, None
        if stage == 'active':
            active_id = self.window_id(output)
            if not active_id:
                self.last_target_active = False
                self.changed.emit(False, QRect())
            elif active_id == f'0x{int(self.own_window.winId()):x}':
                if self.last_target_active and self.last_target_id:
                    self.read_geometry(self.last_target_id)
                else:
                    self.changed.emit(False, QRect())
            else:
                self.current_id = active_id
                self.command('identity', self.xprop, ['-id', active_id, '_NET_WM_NAME', 'WM_CLASS', '_NET_WM_PID'])
        elif stage == 'identity':
            identity = output.decode(errors='ignore').casefold()
            pid_match = re.search(r'_net_wm_pid.*?=\s*(\d+)', identity)
            if pid_match and int(pid_match.group(1)) == os.getpid():
                if self.last_target_active and self.last_target_id:
                    self.read_geometry(self.last_target_id)
                else:
                    self.changed.emit(False, QRect())
            else:
                class_match = re.search(r'wm_class.*?=\s*"([^"]*)",\s*"([^"]*)"', identity)
                class_name = ' '.join(class_match.groups()) if class_match else ''
                app_match = 'chatgpt' in class_name or 'codex' in class_name
                if not app_match and pid_match:
                    try:
                        process_name = Path(f'/proc/{pid_match.group(1)}/comm').read_text(encoding='utf-8').casefold()
                        app_match = 'chatgpt' in process_name or 'codex' in process_name
                    except OSError:
                        pass
                if app_match:
                    self.last_target_id = self.current_id
                    self.last_target_active = True
                    self.read_geometry(self.current_id)
                else:
                    self.last_target_active = False
                    self.changed.emit(False, QRect())
        elif stage == 'geometry':
            values = {}
            for key in ('Absolute upper-left X', 'Absolute upper-left Y', 'Width', 'Height'):
                match = re.search(rb'^\s*' + re.escape(key.encode()) + rb':\s*(-?\d+)', output, re.MULTILINE)
                if match:
                    values[key] = int(match.group(1))
            if len(values) == 4 and values['Width'] > 0 and values['Height'] > 0:
                rect = QRect(values['Absolute upper-left X'], values['Absolute upper-left Y'], values['Width'], values['Height'])
                self.changed.emit(True, rect)
            else:
                self.last_target_active = False
                self.changed.emit(False, QRect())

    def read_geometry(self, window_id):
        self.command('geometry', self.xwininfo, ['-id', window_id])


if sys.platform == 'win32':
    from windows_watcher import AppWindowWatcher
else:
    AppWindowWatcher = X11AppWindowWatcher


class Handle(QLabel):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemMove()


class Bar(QWidget):
    language_changed = Signal(str)

    def __init__(self, client: QuotaSource | None = None):
        super().__init__()
        self.client = client or QuotaClient()
        self.provider = getattr(self.client, 'provider', 'codex')
        self.provider_label = getattr(self.client, 'provider_label', 'Codex')
        self.brand = 'QuotaDock' if self.provider == 'codex' else f'QuotaDock · {self.provider_label}'
        self.accent = '#7fe7b2' if self.provider == 'codex' else '#d97757'
        self.strip_accent = '#58efad' if self.provider == 'codex' else '#d97757'
        self.settings = ProviderSettings(self.provider)
        self.language = self.settings.value('language', 'en')
        if self.language not in ('en', 'pt'):
            self.language = 'en'
        self.last_error_message = None
        self.last_remaining = [None, None]
        self.last_resets = [None, None]
        self.overlay_warning = None
        self.last_update = 0
        self.bucket = None
        self.is_failed = True
        self.client.updated.connect(self.update_quota)
        self.client.failed.connect(self.error)
        self.setWindowTitle('QuotaDock — Codex usage monitor')
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().with_name('quotadock.svg'))))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.theme = self.settings.value('theme', 'solid')
        self.panels = self.settings.value('panels', 'both')
        self.display_mode = self.settings.value('displayMode', 'compact_bar')
        self.app_only = self.provider == 'codex' and self.settings.value('appOnly', True, type=bool)
        self.app_offset_right = self.settings.value('appOffsetRight', 12, type=int)
        self.app_offset_top = self.settings.value('appOffsetTop', 72, type=int)
        self.host_rect = QRect()
        self._placing = False
        self.transparency = self.settings.value('transparency', 175, type=int)
        self.expanded_size = None
        self.minimal = self.settings.value('minimal', True, type=bool)
        self.history_enabled = self.settings.value('historyEnabled', False, type=bool)
        self.setMinimumSize(210, 115)
        self.resize(240, 150)
        self.style_template = """
          QWidget { font-family: "Noto Sans", sans-serif; color: #edf6ef; font-size: 12px; }
          QFrame#card { background: CARD_BACKGROUND; border: 1px solid #4c5a51; border-radius: 10px; }
          QLabel#title { font-weight: bold; font-size: 13px; }
          QLabel#status { color: #b7c8bd; font-size: 10px; }
          QLabel#percentage { font-size: 18px; font-weight: bold; }
          QProgressBar { border: none; border-radius: 4px; background: #424e46; height: 7px; }
          QProgressBar::chunk { background: #7fe7b2; border-radius: 4px; }
          QPushButton { border: none; border-radius: 5px; padding: 3px 7px; background: #34433a; }
          QPushButton:hover { background: #4b5e51; }
          QMenu { background: #202824; border: 1px solid #637569; padding: 5px; }
          QMenu::item:selected { background: #4b5e51; }
        """
        self.apply_theme()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName('card')
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        self.card_layout = layout
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(5)
        header = QHBoxLayout()
        title = Handle(f'⠿ {self.brand}')
        self.title_label = title
        title.setObjectName('title')
        title.setToolTip('Drag to position over your app')
        title.setCursor(Qt.CursorShape.SizeAllCursor)
        header.addWidget(title, 1)
        self.compact = QPushButton('−')
        self.compact.setAccessibleName('Minimize to tray')
        self.compact.setToolTip('Minimize to tray; click the icon to restore')
        self.compact.clicked.connect(self.minimize_to_tray)
        header.addWidget(self.compact)
        menu_button = QPushButton('⋮')
        self.menu_button = menu_button
        menu_button.setAccessibleName('QuotaDock options')
        header.addWidget(menu_button)
        self.header = QWidget()
        self.header.setLayout(header)
        layout.addWidget(self.header)
        self.compact_widget = QWidget()
        compact_layout = QHBoxLayout(self.compact_widget)
        compact_layout.setContentsMargins(5, 3, 5, 3)
        compact_layout.setSpacing(6)
        self.compact_progress = QProgressBar()
        self.compact_progress.setRange(0, 1000)
        self.compact_progress.setTextVisible(False)
        self.compact_progress.setAccessibleName('Remaining quota')
        self.compact_percentage = QLabel('—')
        self.compact_percentage.setObjectName('compactPercentage')
        self.compact_percentage.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.compact_tag = QLabel(self.provider_label)
        self.compact_tag.setStyleSheet(f'color: {self.accent}; font-size: 11px; font-weight: bold;')
        self.compact_tag.setVisible(self.provider != 'codex')
        compact_layout.addWidget(self.compact_tag)
        compact_layout.addWidget(self.compact_progress, 1)
        compact_layout.addWidget(self.compact_percentage)
        layout.addWidget(self.compact_widget)
        self.compact_widget.hide()
        self.dashboard_strip = QWidget()
        strip_layout = QHBoxLayout(self.dashboard_strip)
        strip_layout.setContentsMargins(3, 2, 3, 2)
        strip_layout.setSpacing(10)
        self.strip_brand = QLabel(f"<span style='color:{self.strip_accent}'>▂▅▇</span>  {self.brand}")
        self.strip_brand.setObjectName('stripBrand')
        strip_layout.addWidget(self.strip_brand)
        self.strip_segments = []
        for index in range(2):
            segment = QWidget()
            segment_layout = QVBoxLayout(segment)
            segment_layout.setContentsMargins(0, 0, 0, 0)
            segment_layout.setSpacing(3)
            caption = QLabel('—')
            meter = QProgressBar()
            meter.setRange(0, 1000)
            meter.setTextVisible(False)
            segment_layout.addWidget(caption)
            segment_layout.addWidget(meter)
            strip_layout.addWidget(segment, 1)
            self.strip_segments.append((segment, caption, meter))
        self.strip_options = QPushButton('⚙')
        self.strip_options.setObjectName('stripOptions')
        strip_layout.addWidget(self.strip_options)
        layout.addWidget(self.dashboard_strip)
        self.dashboard_strip.hide()
        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        self.rows = []
        self.panel_widgets = []
        self.panel_layouts = []
        for fallback in ('Window', 'Week'):
            panel = QFrame()
            panel.setObjectName('quotaPanel')
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(0, 0, 0, 0)
            panel_layout.setSpacing(3)
            self.panel_layouts.append(panel_layout)
            row = QHBoxLayout()
            name, value = QLabel(fallback), QLabel('—')
            value.setObjectName('percentage')
            emblem = QLabel('◷' if fallback == 'Window' else '▣')
            emblem.setObjectName('panelEmblem')
            row.addWidget(emblem)
            row.addWidget(name)
            row.addStretch()
            row.addWidget(value)
            panel_layout.addLayout(row)
            progress = QProgressBar()
            progress.setRange(0, 1000)
            progress.setValue(0)
            progress.setTextVisible(False)
            progress.setAccessibleName(f'Remaining quota: {fallback}')
            panel_layout.addWidget(progress)
            if fallback == 'Window':
                self.reset_label = QLabel('Next reset: —')
                self.reset_label.setStyleSheet('color: #b7c8bd; font-size: 11px;')
                self.reset_label.setWordWrap(True)
                panel_layout.addWidget(self.reset_label)
            panel_layout.addStretch(1)
            body_layout.addWidget(panel, 1)
            self.panel_widgets.append(panel)
            self.rows.append((name, value, progress))
        layout.addWidget(self.body)
        self.status = QLabel('Connecting to Codex…' if self.provider == 'codex' else 'Waiting for Claude Code…')
        self.status.setObjectName('status')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.dashboard_footer = QWidget()
        footer_layout = QHBoxLayout(self.dashboard_footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(7)
        self.footer_buttons = []
        for caption in ('Theme', 'Background opacity', 'Display style'):
            button = QPushButton(caption)
            button.setObjectName('footerButton')
            footer_layout.addWidget(button, 1)
            self.footer_buttons.append(button)
        layout.addWidget(self.dashboard_footer)
        self.dashboard_footer.hide()
        self.menu = QMenu(self)
        self.menu.addAction('Show / hide QuotaDock').triggered.connect(self.toggle_window)
        self.minimal_action = self.menu.addAction('Minimal interface')
        self.minimal_action.setCheckable(True)
        self.minimal_action.setChecked(self.minimal)
        self.minimal_action.toggled.connect(self.set_minimal)
        display_menu = self.menu.addMenu('Display style')
        self.display_actions = self.choices(display_menu, [('standard', 'Full window'), ('compact_bar', 'Bar and percentage only'), ('dashboard_strip', 'Dashboard strip')], self.display_mode, self.set_display_mode)
        app_only_action = self.menu.addAction('Show only over ChatGPT/Codex')
        app_only_action.setCheckable(True)
        app_only_action.setChecked(self.app_only)
        app_only_action.setVisible(self.provider == 'codex')
        app_only_action.toggled.connect(self.set_app_only)
        refresh = self.menu.addAction('Refresh now')
        refresh.triggered.connect(self.client.refresh)
        alerts_menu = self.menu.addMenu('Notifications')
        self.alert_actions = {}
        for key, text in (('alert20', 'Alert below 20%'), ('alert10', 'Alert below 10%'), ('alertReset', 'Alert when quota resets')):
            action = alerts_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(self.settings.value(key, True, type=bool))
            action.toggled.connect(lambda enabled, name=key: self.settings.setValue(name, enabled))
            self.alert_actions[key] = action
        self.history_action = self.menu.addAction('Local quota history')
        self.history_action.setCheckable(True)
        self.history_action.setChecked(self.history_enabled)
        self.history_action.toggled.connect(self.set_history_enabled)
        self.menu.addAction('Clear local quota history').triggered.connect(self.clear_history)
        theme_menu = self.menu.addMenu('Theme')
        self.theme_actions = self.choices(theme_menu, [('solid', 'Solid dark'), ('translucent', 'Translucent'), ('dashboard', 'Dashboard')], self.theme, self.set_theme)
        opacity_menu = theme_menu.addMenu('Background opacity')
        self.choices(opacity_menu, [(140, '55%'), (175, '70%'), (220, '86%')], self.transparency, self.set_opacity)
        panel_menu = self.menu.addMenu('Visible panels')
        self.choices(panel_menu, [('primary', 'Hours only'), ('secondary', 'Weekly only'), ('both', 'Hours and weekly')], self.panels, self.set_panels)
        self.menu.addAction('Reset window position').triggered.connect(self.center)
        language_menu = self.menu.addMenu('Language')
        self.language_actions = self.choices(language_menu, [('en', 'English'), ('pt', 'Português')], self.language, self.set_language)
        self.menu.addSeparator()
        copy_path = self.menu.addAction('Copy Codex executable path')
        copy_path.triggered.connect(
            lambda: QApplication.clipboard().setText(executable() or self.t('Codex executable not found'))
        )
        copy_path.setVisible(self.provider == 'codex')
        self.menu.addAction('QuotaDock on GitHub').triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl('https://github.com/sakazaki1/quotadock'))
        )
        self.menu.addAction('Check for updates').triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl('https://github.com/sakazaki1/quotadock/releases/latest'))
        )
        self.menu.addSeparator()
        self.menu.addAction('Quit').triggered.connect(QApplication.instance().quit)
        self.menu_texts = []
        self.collect_menu_texts(self.menu)
        menu_button.setMenu(self.menu)
        self.strip_options.setMenu(self.menu)
        for button, submenu in zip(self.footer_buttons, (theme_menu, opacity_menu, display_menu)):
            button.setMenu(submenu)
        for widget in (self.compact_widget, self.compact_tag, self.compact_progress, self.compact_percentage,
                       self.dashboard_strip, self.strip_brand,
                       *(part for segment in self.strip_segments for part in segment)):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda point, target=widget: self.menu.popup(target.mapToGlobal(point))
            )
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor('#202824'))
        painter = QPainter(pixmap)
        painter.fillRect(5, 13, 22, 7, QColor('#7fe7b2'))
        painter.end()
        self.tray = QSystemTrayIcon(QIcon(pixmap), self)
        self.tray.setToolTip(self.t('Real Codex quota'))
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        geometry = self.settings.value('geometry')
        if geometry:
            self.restoreGeometry(geometry)
        self.set_panels(self.panels, save=False)
        self.apply_minimal()
        if not self.settings.value('minimalLayoutV1', False, type=bool):
            self.resize(240, 150 if self.panels == 'both' else 115)
            self.settings.setValue('minimalLayoutV1', True)
        self.update_tray()
        self.setMouseTracking(True)
        for widget in self.findChildren(QWidget):
            widget.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)
        if not any(screen.availableGeometry().intersects(self.frameGeometry()) for screen in QApplication.screens()):
            self.center()
        if not geometry:
            self.center()
        self.age = QTimer(self)
        self.age.setInterval(1000)
        self.age.timeout.connect(self.tick)
        self.age.start()
        self.window_watcher = AppWindowWatcher(self, self)
        self.window_watcher.changed.connect(self.app_window_changed)
        if hasattr(self.client, 'resolved'):
            self.client.resolved.connect(self.remember_executable)
        if self.app_only:
            if not getattr(self.window_watcher, 'available', True):
                self.app_only = False
                self.settings.setValue('appOnly', False)
                self.show()
                self.overlay_warning = 'Overlay tracking unavailable. Showing as a floating window.'
            else:
                self.hide()
                self.window_watcher.start()
        self.set_display_mode(self.display_mode)
        self.retranslate_ui()

    def remember_executable(self, path):
        identity = executable_identity(path)
        previous_path = self.settings.value('trustedExecutable')
        previous_identity = self.settings.value('trustedExecutableIdentity')
        if previous_path and previous_identity and (previous_path != path or previous_identity != identity):
            self.status.setText(self.t('Codex executable changed. Verify it is trusted.'))
            self.status.setToolTip(path)
        self.settings.setValue('trustedExecutable', path)
        self.settings.setValue('trustedExecutableIdentity', identity or '')

    def t(self, text):
        translated = translate(text, self.language)
        return translated if self.provider_label == 'Codex' else translated.replace('Codex', self.provider_label)

    def collect_menu_texts(self, menu):
        for action in menu.actions():
            if action.text():
                self.menu_texts.append((action, action.text()))
            if action.menu():
                self.collect_menu_texts(action.menu())

    def set_language(self, language):
        self.language = language
        self.settings.setValue('language', language)
        self.retranslate_ui()
        self.language_changed.emit(language)

    def follow_language(self, language):
        """Adopt a language chosen in another QuotaDock window without announcing it again."""
        if language == self.language:
            return
        self.language = language
        action = self.language_actions.get(language)
        if action is not None:
            action.setChecked(True)
        self.retranslate_ui()

    def retranslate_ui(self):
        self.setWindowTitle(self.t('QuotaDock — Codex usage monitor'))
        for action, english in self.menu_texts:
            action.setText(self.t(english))
        self.header.findChild(QLabel, 'title').setToolTip(self.t('Drag to position over your app'))
        self.compact.setAccessibleName(self.t('Minimize to tray'))
        self.compact.setToolTip(self.t('Minimize to tray; click the icon to restore'))
        self.menu_button.setAccessibleName(self.t('QuotaDock options'))
        self.strip_options.setAccessibleName(self.t('QuotaDock options'))
        self.strip_options.setToolTip(self.t('QuotaDock options'))
        for button, label in zip(self.footer_buttons, ('Theme', 'Background opacity', 'Display style')):
            button.setText(self.t(label))
        self.compact_progress.setAccessibleName(self.t('Remaining quota'))
        for index, (_, _, progress) in enumerate(self.rows):
            progress.setAccessibleName(self.t(('Remaining quota: Window', 'Remaining quota: Week')[index]))
        self.paint_quota()
        if self.overlay_warning is not None:
            self.status.setText(self.t(self.overlay_warning))
        elif self.last_error_message is not None:
            self.error(self.last_error_message)
        elif not self.last_update:
            self.status.setText(self.t('Connecting to Codex…' if self.provider == 'codex' else 'Waiting for Claude Code…'))
        else:
            self.tick()

    def choices(self, menu, options, selected, callback):
        group = QActionGroup(menu)
        group.setExclusive(True)
        actions = {}
        for key, text in options:
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(key == selected)
            group.addAction(action)
            action.triggered.connect(lambda checked, value=key: callback(value))
            actions[key] = action
        return actions

    def apply_theme(self):
        if self.theme == 'dashboard':
            self.setStyleSheet("""
              QWidget { font-family: "Noto Sans", sans-serif; color: #edf3f2; font-size: 12px; }
              QFrame#card { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #131b21, stop:1 #0c1118); border: 1px solid #39464d; border-radius: 15px; }
              QFrame#quotaPanel { background: #151f25; border: 1px solid #34434a; border-radius: 10px; }
              QLabel#title, QLabel#stripBrand { font-size: 15px; font-weight: bold; }
              QLabel#panelEmblem { color: #69e9b3; font-size: 17px; }
              QLabel#percentage { font-size: 18px; font-weight: bold; }
              QLabel#status { color: #9fcbb7; font-size: 11px; }
              QProgressBar { border: none; border-radius: 5px; background: #324047; height: 11px; }
              QProgressBar::chunk { background: #58efad; border-radius: 5px; }
              QPushButton { border: 1px solid #34434a; border-radius: 7px; padding: 5px 8px; background: #1a252b; }
              QPushButton:hover { background: #283a40; border-color: #65dfab; }
              QMenu { background: #151d23; border: 1px solid #48575e; padding: 6px; }
              QMenu::item { padding: 5px 14px; }
              QMenu::item:selected { background: #28423a; }
            """)
        else:
            background = f'rgba(32, 40, 36, {self.transparency})' if self.theme == 'translucent' else '#202824'
            self.setStyleSheet(self.style_template.replace('CARD_BACKGROUND', background))
        if hasattr(self, 'card_layout'):
            self.title_label.setText(f"<span style='color:{self.strip_accent}'>▂▅▇</span>  {self.brand}" if self.theme == 'dashboard' else f'⠿ {self.brand}')
            self.update_responsive_layout()

    def set_theme(self, theme):
        self.theme = theme
        self.settings.setValue('theme', theme)
        self.apply_theme()
        self.set_display_mode('dashboard_strip' if theme == 'dashboard' and self.display_mode == 'compact_bar' else self.display_mode)

    def set_opacity(self, opacity):
        self.transparency = opacity
        self.settings.setValue('transparency', opacity)
        self.set_theme('translucent')
        self.theme_actions['translucent'].setChecked(True)

    def set_panels(self, panels, save=True):
        self.panels = panels
        for index, widget in enumerate(self.panel_widgets):
            widget.setVisible(panels == 'both' or panels == ('primary', 'secondary')[index])
        if save:
            self.settings.setValue('panels', panels)
        if self.display_mode == 'standard':
            minimum_height = 220 if self.theme == 'dashboard' else (190 if panels == 'both' else 145)
            self.setMinimumHeight(minimum_height)
        for index, (segment, _, _) in enumerate(self.strip_segments):
            segment.setVisible(panels == 'both' or panels == ('primary', 'secondary')[index])
        if self.display_mode == 'dashboard_strip':
            self.setMinimumWidth(300 if panels == 'both' else 225)
        self.update_responsive_layout()
        if hasattr(self, 'tray'):
            self.update_tray()

    def set_display_mode(self, mode):
        self.display_mode = mode
        self.settings.setValue('displayMode', mode)
        compact = mode == 'compact_bar'
        strip = mode == 'dashboard_strip'
        if mode == 'standard' and self.minimal:
            self.minimal = False
            self.settings.setValue('minimal', False)
            self.minimal_action.setChecked(False)
            self.apply_minimal()
        self.header.setVisible(mode == 'standard')
        self.body.setVisible(mode == 'standard')
        self.status.setVisible(mode == 'standard' and (not self.minimal or self.is_failed))
        self.compact_widget.setVisible(compact)
        self.dashboard_strip.setVisible(strip)
        self.dashboard_footer.setVisible(self.theme == 'dashboard' and mode == 'standard')
        if compact:
            self.setMinimumSize(112, 38)
            self.resize(self.settings.value('compactWidth', 152, type=int), self.settings.value('compactHeight', 42, type=int))
        elif strip:
            self.setMinimumSize(300 if self.panels == 'both' else 225, 58)
            self.resize(self.settings.value('stripWidth', 440, type=int), self.settings.value('stripHeight', 68, type=int))
        else:
            self.setMinimumSize(340 if self.theme == 'dashboard' else 210,
                                220 if self.theme == 'dashboard' else (190 if self.panels == 'both' else 145))
            self.resize(self.settings.value('fullWidth', 420 if self.theme == 'dashboard' else 310, type=int), self.settings.value('fullHeight', 300 if self.theme == 'dashboard' else 230, type=int))
        self.update_responsive_layout()
        self.paint_quota()

    def set_app_only(self, enabled):
        self.app_only = enabled
        self.settings.setValue('appOnly', enabled)
        if enabled:
            self.hide()
            self.window_watcher.start()
        else:
            self.window_watcher.stop()
            self.showNormal()
            self.raise_()

    def app_window_changed(self, active, rect):
        if not self.app_only:
            return
        if not active or rect.isNull():
            self.hide()
            return
        self.host_rect = QRect(rect)
        margin = 12
        width = min(self.width(), max(1, rect.width() - margin * 2))
        height = min(self.height(), max(1, rect.height() - margin * 2))
        x = max(rect.left() + margin, rect.right() - width - self.app_offset_right + 1)
        y = min(rect.top() + self.app_offset_top, rect.bottom() - height - margin + 1)
        y = max(rect.top() + margin, y)
        self._placing = True
        self.setGeometry(x, y, width, height)
        self._placing = False
        if not self.isVisible():
            self.show()
            QTimer.singleShot(0, self.hide_from_taskbar)
        self.raise_()

    def hide_from_taskbar(self):
        """Keep the floating monitor in the tray without a task-manager button."""
        if sys.platform != 'linux' or not os.environ.get('DISPLAY'):
            return
        window_id = f'0x{int(self.winId()):x}'
        wmctrl = shutil.which('wmctrl')
        if wmctrl:
            try:
                result = subprocess.run(
                    [wmctrl, '-ir', window_id, '-b', 'add,skip_taskbar'],
                    capture_output=True, timeout=1, check=False,
                )
                if result.returncode == 0:
                    return
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            x11 = ctypes.CDLL('libX11.so.6')
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            display = x11.XOpenDisplay(None)
            if display:
                class ClientMessageData(ctypes.Union):
                    _fields_ = [('b', ctypes.c_char * 20), ('s', ctypes.c_short * 10), ('l', ctypes.c_long * 5)]

                class ClientMessageEvent(ctypes.Structure):
                    _fields_ = [
                        ('type', ctypes.c_int), ('serial', ctypes.c_ulong),
                        ('send_event', ctypes.c_int), ('display', ctypes.c_void_p),
                        ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                        ('format', ctypes.c_int), ('data', ClientMessageData),
                    ]

                class XEvent(ctypes.Union):
                    _fields_ = [('type', ctypes.c_int), ('xclient', ClientMessageEvent), ('pad', ctypes.c_long * 24)]

                x11.XDefaultRootWindow.restype = ctypes.c_ulong
                x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
                x11.XInternAtom.restype = ctypes.c_ulong
                x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
                x11.XSendEvent.restype = ctypes.c_int
                x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.POINTER(XEvent)]
                x11.XFlush.argtypes = [ctypes.c_void_p]
                x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

                state = x11.XInternAtom(display, b'_NET_WM_STATE', 0)
                skip_taskbar = x11.XInternAtom(display, b'_NET_WM_STATE_SKIP_TASKBAR', 0)
                event = XEvent()
                event.xclient = ClientMessageEvent(
                    33, 0, 1, display, int(self.winId()), state, 32,
                    ClientMessageData(l=(1, skip_taskbar, 0, 0, 0)),
                )
                root = x11.XDefaultRootWindow(display)
                sent = x11.XSendEvent(display, root, 0, (1 << 19) | (1 << 20), ctypes.byref(event))
                x11.XFlush(display)
                x11.XCloseDisplay(display)
                if sent:
                    return
        except (OSError, AttributeError, TypeError, ValueError):
            pass
        xprop = shutil.which('xprop')
        if not xprop:
            return
        try:
            current = subprocess.run(
                [xprop, '-id', window_id, '_NET_WM_STATE'],
                capture_output=True, text=True, timeout=1, check=False,
            )
            match = re.search(r'_NET_WM_STATE(?:\(ATOM\))?\s*=\s*(.*)', current.stdout)
            states = re.findall(r'_NET_WM_STATE_[A-Z_]+', match.group(1)) if match else []
            if '_NET_WM_STATE_SKIP_TASKBAR' in states:
                return
            states.append('_NET_WM_STATE_SKIP_TASKBAR')
            subprocess.run(
                [xprop, '-id', window_id, '-f', '_NET_WM_STATE', '32a', '-set',
                 '_NET_WM_STATE', ', '.join(states)],
                capture_output=True, timeout=1, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def edge_at(self, point):
        edges = Qt.Edge(0)
        if point.x() < 7:
            edges |= Qt.Edge.LeftEdge
        elif point.x() >= self.width() - 7:
            edges |= Qt.Edge.RightEdge
        if point.y() < 7:
            edges |= Qt.Edge.TopEdge
        elif point.y() >= self.height() - 7:
            edges |= Qt.Edge.BottomEdge
        return edges

    def eventFilter(self, watched, event):
        if isinstance(watched, QWidget) and watched.window() is self and event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
            point = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self.edge_at(point)
            cursor = Qt.CursorShape.ArrowCursor
            if edges:
                horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
                vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
                if horizontal and vertical:
                    cursor = Qt.CursorShape.SizeFDiagCursor if edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge) else Qt.CursorShape.SizeBDiagCursor
                else:
                    cursor = Qt.CursorShape.SizeHorCursor if horizontal else Qt.CursorShape.SizeVerCursor
                if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                    self.windowHandle().startSystemResize(edges)
                    return True
            elif (self.display_mode in ('compact_bar', 'dashboard_strip') and not isinstance(watched, QPushButton)
                  and event.type() == QEvent.Type.MouseButtonPress
                  and event.button() == Qt.MouseButton.LeftButton):
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemMove()
                    return True
            watched.setCursor(Qt.CursorShape.SizeAllCursor if not edges and watched.objectName() == 'title' else cursor)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_responsive_layout()
        if hasattr(self, 'settings') and self.isVisible():
            if self.app_only and not self._placing:
                prefix = 'compact' if self.display_mode == 'compact_bar' else 'strip' if self.display_mode == 'dashboard_strip' else 'full'
                self.settings.setValue(f'{prefix}Width', self.width())
                self.settings.setValue(f'{prefix}Height', self.height())
            elif not self.app_only:
                self.settings.setValue('geometry', self.saveGeometry())

    def update_responsive_layout(self):
        if not hasattr(self, 'card_layout'):
            return
        width, height = self.width(), self.height()
        dashboard = self.theme == 'dashboard'
        if self.display_mode == 'dashboard_strip':
            scale = max(0.82, min(1.42, width / 440, height / 68))
        elif self.display_mode == 'compact_bar':
            scale = max(0.8, min(1.35, width / 152, height / 42))
        else:
            reference_width, reference_height = (420, 300) if dashboard else (310, 230)
            scale = max(0.85 if dashboard else 0.76,
                        min(1.45, width / reference_width, height / reference_height))

        def pixels(base):
            return max(8, round(base * scale))

        if self.display_mode == 'dashboard_strip':
            margin = max(7, round(12 * scale))
            self.card_layout.setContentsMargins(margin, max(5, round(8 * scale)), margin, max(5, round(8 * scale)))
            strip_layout = self.dashboard_strip.layout()
            strip_layout.setSpacing(max(5, round(10 * scale)))
            self.strip_brand.setText(f"<span style='color:{self.strip_accent}'>▂▅▇</span>" if width < 380 else f"<span style='color:{self.strip_accent}'>▂▅▇</span>  {self.brand}")
            self.strip_brand.setStyleSheet(f'font-size: {pixels(15)}px; font-weight: bold;')
            for _, caption, meter in self.strip_segments:
                caption.setStyleSheet(f'font-size: {pixels(12)}px;')
                meter.setFixedHeight(max(7, round(11 * scale)))
            self.strip_options.setStyleSheet(f'font-size: {pixels(12)}px; padding: {max(2, round(4 * scale))}px;')
            return

        if self.display_mode == 'compact_bar':
            margin = max(5, round(8 * scale))
            self.card_layout.setContentsMargins(margin, max(3, round(5 * scale)), margin, max(3, round(5 * scale)))
            self.compact_percentage.setStyleSheet(f'font-size: {pixels(12)}px;')
            self.compact_progress.setFixedHeight(max(6, round(9 * scale)))
            return

        tight = dashboard and (width < 390 or height < 270)
        horizontal = round((12 if tight else 16) * scale) if dashboard else round(11 * scale)
        vertical = round((8 if tight else 12) * scale) if dashboard else round(8 * scale)
        self.card_layout.setContentsMargins(max(7, horizontal), max(6, vertical), max(7, horizontal), max(6, vertical))
        self.card_layout.setSpacing(max(3, round((5 if tight else 9) * scale)) if dashboard else max(3, round(5 * scale)))
        for panel_layout in self.panel_layouts:
            inset = max(6, round((7 if tight else 12) * scale)) if dashboard else 0
            panel_layout.setContentsMargins(inset, max(4, round((6 if tight else 10) * scale)) if dashboard else 0,
                                            inset, max(4, round((6 if tight else 10) * scale)) if dashboard else 0)
            panel_layout.setSpacing(max(2, round((4 if tight else 7) * scale)) if dashboard else max(2, round(3 * scale)))
        self.title_label.setStyleSheet(f'font-size: {pixels(15 if dashboard else 13)}px; font-weight: bold;')
        for button in (self.compact, self.menu_button):
            button.setStyleSheet(f'font-size: {pixels(12)}px; padding: {max(2, round(4 * scale))}px {max(4, round(7 * scale))}px;')
        for panel, (label, value, progress) in zip(self.panel_widgets, self.rows):
            emblem = panel.findChild(QLabel, 'panelEmblem')
            emblem.setStyleSheet(f'color: #69e9b3; font-size: {pixels(17)}px;')
            label.setStyleSheet(f'font-size: {pixels(12)}px;')
            value.setStyleSheet(f'font-size: {pixels(18)}px; font-weight: bold;')
            progress.setFixedHeight(max(6, round((11 if dashboard else 7) * scale)))
        self.reset_label.setStyleSheet(f'color: #b7c8bd; font-size: {pixels(11)}px;')
        self.status.setStyleSheet(f'color: #9fcbb7; font-size: {pixels(11)}px;')
        self.status.setVisible(not self.minimal and (not dashboard or height >= 245) or self.is_failed and height >= 245)
        self.dashboard_footer.setVisible(dashboard and width >= 390 and height >= 270)
        for button in self.footer_buttons:
            button.setStyleSheet(f'font-size: {pixels(11)}px; padding: {max(2, round(4 * scale))}px;')

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.isVisible() and self.app_only and not self._placing and not self.host_rect.isNull():
            self.app_offset_right = max(0, self.host_rect.right() - self.geometry().right())
            self.app_offset_top = max(0, self.y() - self.host_rect.top())
            self.settings.setValue('appOffsetRight', self.app_offset_right)
            self.settings.setValue('appOffsetTop', self.app_offset_top)
        elif self.isVisible() and not self.app_only:
            self.settings.setValue('geometry', self.saveGeometry())

    def center(self):
        rect = QApplication.primaryScreen().availableGeometry()
        slot = 0 if self.provider == 'codex' else 1
        self.move(rect.right() - (self.width() + 12) * (slot + 1) - 13, rect.top() + 80)

    def set_minimal(self, enabled):
        self.minimal = enabled
        self.settings.setValue('minimal', enabled)
        self.apply_minimal()
        self.set_panels(self.panels, save=False)
        if self.display_mode == 'standard':
            self.resize(240, 150 if self.panels == 'both' else 115) if enabled else self.resize(310, 230)
        self.paint_quota()

    def apply_minimal(self):
        self.status.setVisible(self.display_mode == 'standard' and (not self.minimal or self.is_failed)
                               and (self.theme != 'dashboard' or self.height() >= 245))

    def minimize_to_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
        else:
            # Without a tray, provide a regular taskbar entry to restore the window.
            self.setWindowFlag(Qt.WindowType.Tool, False)
            self.showMinimized()

    def toggle_window(self):
        if self.app_only:
            self.window_watcher.poll()
            return
        if self.isVisible() and not self.isMinimized():
            self.minimize_to_tray()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_window()

    def update_tray(self):
        if not hasattr(self, 'tray'):
            return
        windows = [self.bucket.get(name) if self.bucket else None for name in ('primary', 'secondary')]
        selected = [1] if self.panels == 'secondary' else [0] if self.panels == 'primary' else [0, 1]
        amount = remaining(windows[selected[0]])
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#202824'))
        painter.drawRoundedRect(0, 0, 64, 64, 10, 10)
        if self.provider != 'codex':
            painter.fillRect(7, 3, 50, 3, QColor(self.accent))
        painter.setPen(QColor('#ffffff' if not self.is_failed else '#aab3ad'))
        painter.setFont(QFont('Noto Sans', 23, QFont.Weight.Bold))
        painter.drawText(0, 0, 64, 44, Qt.AlignmentFlag.AlignCenter, f'{amount:.0f}' if amount is not None and not self.is_failed else '?')
        for line, index in enumerate(selected):
            value = remaining(windows[index])
            painter.fillRect(7, 46 + line * 9, 50, 5, QColor('#4a554e'))
            if value is not None and not self.is_failed:
                color = '#7fe7b2' if value > 25 else '#ffd17e' if value > 10 else '#ff9292'
                painter.fillRect(7, 46 + line * 9, round(value / 2), 5, QColor(color))
        painter.end()
        self.tray.setIcon(QIcon(pixmap))
        tips = [self.t('QuotaDock • remaining' if not self.is_failed else 'QuotaDock • offline')]
        if self.provider != 'codex':
            tips[0] += f' ({self.provider_label})'
        for index in selected:
            window = windows[index]
            value = remaining(window)
            tips.append(self.t(window_label(window, ('Hours', 'Week')[index])) + ': ' + (f'{value:g}%' if value is not None else self.t('unavailable')))
        tips.append(self.reset_label.text())
        tips.append(self.t('Click to show/hide; right-click for options.'))
        self.tray.setToolTip('\n'.join(tips))

    def update_quota(self, bucket):
        self.bucket = bucket
        observed = bucket.get('observedAt')
        age = max(0.0, time.time() - observed) if isinstance(observed, (int, float)) and not isinstance(observed, bool) else 0.0
        self.last_update = time.monotonic() - age
        self.is_failed = False
        self.last_error_message = None
        self.notify_thresholds(bucket)
        if self.history_enabled:
            self.history().record(getattr(self.client, 'source_id', 'codex'), bucket)
        self.paint_quota()
        self.tick()

    def history(self):
        location = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation) or tempfile.gettempdir())
        return QuotaHistory(location / 'history.sqlite3')

    def set_history_enabled(self, enabled):
        self.history_enabled = enabled
        self.settings.setValue('historyEnabled', enabled)

    def clear_history(self):
        self.history().clear()

    def notify_thresholds(self, bucket):
        for index, name in enumerate(('primary', 'secondary')):
            amount = remaining(bucket.get(name))
            previous = self.last_remaining[index]
            reset = bucket.get(name, {}).get('resetsAt') if isinstance(bucket.get(name), dict) else None
            for threshold, key in ((20, 'alert20'), (10, 'alert10')):
                if self.settings.value(key, True, type=bool) and amount is not None and (previous is None or previous > threshold) and amount <= threshold:
                    self.tray.showMessage(self.brand, self.t('Quota alert: {amount}% remaining').format(amount=f'{amount:g}'))
            if self.settings.value('alertReset', True, type=bool) and self.last_resets[index] and isinstance(reset, (int, float)) and reset > self.last_resets[index]:
                self.tray.showMessage(self.brand, self.t('Quota reset detected.'))
            self.last_remaining[index] = amount
            self.last_resets[index] = reset if isinstance(reset, (int, float)) else self.last_resets[index]

    def paint_quota(self):
        for index, (label, value, progress) in enumerate(self.rows):
            window = self.bucket.get(('primary', 'secondary')[index]) if self.bucket else None
            amount = remaining(window)
            label_text = self.t(window_label(window, ('Window', 'Week')[index]))
            if self.theme == 'dashboard' and index == 0 and label_text not in (self.t('Window'), '—'):
                label_text = f'{self.t("Window")} · {label_text}'
            label.setText(label_text)
            value.setText(f'{amount:g}%' if amount is not None else '—')
            progress.setValue(round(amount * 10) if amount is not None else 0)
            color = '#7fe7b2' if amount is not None and amount > 25 else '#ffd17e' if amount is not None and amount > 10 else '#ff9292'
            if self.is_failed or amount is None:
                color = '#7e8982'
            progress.setStyleSheet(f'QProgressBar::chunk {{ background: {color}; border-radius: 4px; }}')
            progress.setEnabled(not self.is_failed and amount is not None)
            tip = self.t('Remaining percentage reported by Codex.')
            reset = window.get('resetsAt') if isinstance(window, dict) else None
            if index == 0:
                self.reset_label.setText(self.t('Next reset: —'))
                self.reset_label.setToolTip(self.t('Next reset: —'))
            if isinstance(reset, (int, float)):
                try:
                    reset_text = datetime.fromtimestamp(reset).strftime('%d/%m/%Y às %H:%M' if self.language == 'pt' else '%Y-%m-%d at %H:%M')
                    tip += '\n' + self.t('Resets: ') + reset_text
                    if index == 0:
                        self.reset_label.setText(self.t('Resets at {time}').format(time=datetime.fromtimestamp(reset).strftime('%H:%M')) if self.minimal else self.t('Next reset: {time}').format(time=reset_text))
                        self.reset_label.setToolTip(self.t('Next reset: {time}').format(time=reset_text))
                except (ValueError, OverflowError, OSError):
                    pass
            value.setToolTip(tip)
            progress.setToolTip(tip)
        selected_index = 1 if self.panels == 'secondary' else 0
        selected_window = self.bucket.get(('primary', 'secondary')[selected_index]) if self.bucket else None
        compact_amount = remaining(selected_window)
        self.compact_percentage.setText(f'{compact_amount:g}%' if compact_amount is not None else '—')
        self.compact_progress.setValue(round(compact_amount * 10) if compact_amount is not None else 0)
        compact_color = '#7e8982' if self.is_failed or compact_amount is None else '#7fe7b2' if compact_amount > 25 else '#ffd17e' if compact_amount > 10 else '#ff9292'
        self.compact_progress.setStyleSheet(f'QProgressBar::chunk {{ background: {compact_color}; border-radius: 4px; }}')
        self.compact_progress.setEnabled(not self.is_failed and compact_amount is not None)
        self.compact_widget.setToolTip(self.rows[selected_index][2].toolTip())
        for index, (segment, caption, meter) in enumerate(self.strip_segments):
            window = self.bucket.get(('primary', 'secondary')[index]) if self.bucket else None
            amount = remaining(window)
            label = self.t(window_label(window, ('Hours', 'Week')[index]))
            caption.setText(f'{label}  {amount:g}%' if amount is not None else f'{label}  —')
            meter.setValue(round(amount * 10) if amount is not None else 0)
            color = '#7e8982' if self.is_failed or amount is None else '#58efad' if amount > 25 else '#ffd17e' if amount > 10 else '#ff9292'
            meter.setStyleSheet(f'QProgressBar::chunk {{ background: {color}; border-radius: 5px; }}')
            meter.setToolTip(self.rows[index][2].toolTip())
            caption.setToolTip(meter.toolTip())
        self.apply_minimal()
        self.update_tray()

    def tick(self):
        if not self.last_update:
            return
        age = int(time.monotonic() - self.last_update)
        stale_after = getattr(self.client, 'stale_after', 75)
        if stale_after is not None and age > stale_after and not self.is_failed:
            self.error('Stale data. Waiting for Codex…')
        elif not self.is_failed:
            self.status.setText(self.t('Remaining • updated {age}s ago').format(age=age))
            self.setToolTip(self.t('Real quota • updated {age}s ago').format(age=age))

    def error(self, message):
        self.is_failed = True
        self.last_error_message = message
        self.status.setText(self.t(message) + (self.t(' Last reading shown in gray.') if self.last_update else ''))
        self.paint_quota()
        self.update_tray()

    def closeEvent(self, event):
        QApplication.instance().quit()
        event.accept()

    def cleanup(self):
        QApplication.instance().removeEventFilter(self)
        self.settings.setValue('geometry', self.saveGeometry())
        if hasattr(self, 'window_watcher'):
            self.window_watcher.stop()
        self.client.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--source', action='append', choices=('codex', 'claude-file', 'both'),
                        help='Quota source (default: codex). Repeat the option, or use "both", to show Codex and '
                             'Claude together. Claude reads only the local mirror file you specify.')
    parser.add_argument('--claude-state', default='~/.claude/statusline-state.json',
                        help='Path to a Claude Code status-line mirror JSON file.')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    import signal
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    app.setApplicationName('QuotaDock')
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().with_name('quotadock.svg'))))
    app.setQuitOnLastWindowClosed(False)
    runtime_location = (QStandardPaths.StandardLocation.AppLocalDataLocation if sys.platform == 'win32'
                        else QStandardPaths.StandardLocation.RuntimeLocation)
    runtime = Path(QStandardPaths.writableLocation(runtime_location) or tempfile.gettempdir())
    runtime.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(runtime / 'quotadock.lock'))
    if not lock.tryLock(100):
        return 0
    names = []
    for choice in args.source or ['codex']:
        for name in (('codex', 'claude-file') if choice == 'both' else (choice,)):
            if name not in names:
                names.append(name)
    bars = []
    for name in names:
        if name == 'claude-file':
            from claude_source import ClaudeFileClient
            client = ClaudeFileClient(args.claude_state)
        else:
            client = QuotaClient()
        bar = Bar(client)
        app.aboutToQuit.connect(bar.cleanup)
        bars.append(bar)
    for bar in bars:
        for other in bars:
            if other is not bar:
                bar.language_changed.connect(other.follow_language)
        if not bar.app_only:
            bar.show()
            QTimer.singleShot(0, bar.hide_from_taskbar)
    if args.smoke_test:
        for bar in bars:
            bar.update_quota({'primary': {'usedPercent': 6, 'windowDurationMins': 300}, 'secondary': {'usedPercent': 1, 'windowDurationMins': 10080}})
            assert bar.rows[0][1].text() == '94%'
            assert bar.rows[1][1].text() == '99%'
            bar.error('Offline test')
            assert bar.is_failed
        app.processEvents()
        for bar in bars:
            suffix = '' if bar.provider == 'codex' else f'-{bar.provider}'
            bar.grab().save(str(Path(tempfile.gettempdir()) / f'quotadock-preview{suffix}.png'))
        QTimer.singleShot(100, app.quit)
    else:
        for bar in bars:
            QTimer.singleShot(0, bar.client.start)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
