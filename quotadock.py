#!/usr/bin/python3
"""Native movable quota monitor for KDE. Uses the signed-in local Codex app-server."""
import argparse
import ctypes
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

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, QLockFile, QObject, QProcess, QSettings, QStandardPaths, QUrl, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QActionGroup, QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget
from limits import codex_bucket, remaining, window_label
from i18n import translate


def executable():
    override = os.environ.get('QUOTADOCK_CODEX')
    if override:
        path = Path(override).expanduser()
        if sys.platform == 'win32' and path.suffix.casefold() != '.exe':
            return None
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA')
        if local:
            for relative in ('Programs/ChatGPT/resources/codex.exe', 'Programs/Codex/resources/codex.exe'):
                path = Path(local) / relative
                if path.is_file():
                    return str(path)
        return shutil.which('codex.exe')
    for location in ('/usr/lib/chatgpt/resources/codex', '/usr/lib/codex/resources/codex',
                     '/opt/ChatGPT/resources/codex', '/opt/Codex/resources/codex'):
        path = Path(location)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return shutil.which('codex')


class QuotaClient(QObject):
    updated = pyqtSignal(dict)
    failed = pyqtSignal(str)
    MAX_MESSAGE_BYTES = 1024 * 1024

    def __init__(self):
        super().__init__()
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
        self.retry.setInterval(30000)
        self.retry.timeout.connect(self.start)

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
        binary = executable()
        if not binary:
            self.failed.emit('Codex not found. See README for setup.')
            self.retry.start()
            return
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
        self.process.start(binary, ['app-server', '--stdio'])
        self.timeout.start()

    def handshake(self):
        self.pending = self.send('initialize', {'clientInfo': {'name': 'quotadock', 'version': '1.0.0'}})

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
        self.retry.start()

    def finished(self, *_):
        self.poll.stop()
        self.timeout.stop()
        self.initialized = False
        self.pending = None
        if not self.stopping and not self.retry.isActive():
            self.failed.emit('Connection closed. Reconnecting…')
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
    changed = pyqtSignal(bool, QRect)

    def __init__(self, own_window, parent=None):
        super().__init__(parent)
        self.own_window = own_window
        self.xprop = shutil.which('xprop')
        self.xwininfo = shutil.which('xwininfo')
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
    def __init__(self):
        super().__init__()
        self.settings = QSettings('QuotaDock', 'QuotaDock')
        self.language = self.settings.value('language', 'en')
        if self.language not in ('en', 'pt'):
            self.language = 'en'
        self.last_error_message = None
        self.last_update = 0
        self.bucket = None
        self.is_failed = True
        self.client = QuotaClient()
        self.client.updated.connect(self.update_quota)
        self.client.failed.connect(self.error)
        self.setWindowTitle('QuotaDock — Codex usage monitor')
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().with_name('quotadock.svg'))))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.theme = self.settings.value('theme', 'solid')
        self.panels = self.settings.value('panels', 'both')
        self.display_mode = self.settings.value('displayMode', 'compact_bar')
        self.app_only = self.settings.value('appOnly', True, type=bool)
        self.app_offset_right = self.settings.value('appOffsetRight', 12, type=int)
        self.app_offset_top = self.settings.value('appOffsetTop', 72, type=int)
        self.host_rect = QRect()
        self._placing = False
        self.transparency = self.settings.value('transparency', 175, type=int)
        self.expanded_size = None
        self.minimal = self.settings.value('minimal', True, type=bool)
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
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(5)
        header = QHBoxLayout()
        title = Handle('⠿ QuotaDock')
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
        compact_layout.addWidget(self.compact_progress, 1)
        compact_layout.addWidget(self.compact_percentage)
        layout.addWidget(self.compact_widget)
        self.compact_widget.hide()
        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        self.rows = []
        self.panel_widgets = []
        for fallback in ('Window', 'Week'):
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(0, 0, 0, 0)
            panel_layout.setSpacing(3)
            row = QHBoxLayout()
            name, value = QLabel(fallback), QLabel('—')
            value.setObjectName('percentage')
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
            body_layout.addWidget(panel, 1)
            self.panel_widgets.append(panel)
            self.rows.append((name, value, progress))
        layout.addWidget(self.body)
        self.status = QLabel('Connecting to Codex…')
        self.status.setObjectName('status')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.menu = QMenu(self)
        self.menu.addAction('Show / hide QuotaDock').triggered.connect(self.toggle_window)
        self.minimal_action = self.menu.addAction('Minimal interface')
        self.minimal_action.setCheckable(True)
        self.minimal_action.setChecked(self.minimal)
        self.minimal_action.toggled.connect(self.set_minimal)
        display_menu = self.menu.addMenu('Display style')
        self.display_actions = self.choices(display_menu, [('standard', 'Full window'), ('compact_bar', 'Bar and percentage only')], self.display_mode, self.set_display_mode)
        app_only_action = self.menu.addAction('Show only over ChatGPT/Codex')
        app_only_action.setCheckable(True)
        app_only_action.setChecked(self.app_only)
        app_only_action.toggled.connect(self.set_app_only)
        refresh = self.menu.addAction('Refresh now')
        refresh.triggered.connect(self.client.refresh)
        theme_menu = self.menu.addMenu('Theme')
        self.theme_actions = self.choices(theme_menu, [('solid', 'Solid dark'), ('translucent', 'Translucent')], self.theme, self.set_theme)
        opacity_menu = theme_menu.addMenu('Background opacity')
        self.choices(opacity_menu, [(140, '55%'), (175, '70%'), (220, '86%')], self.transparency, self.set_opacity)
        panel_menu = self.menu.addMenu('Visible panels')
        self.choices(panel_menu, [('primary', 'Hours only'), ('secondary', 'Weekly only'), ('both', 'Hours and weekly')], self.panels, self.set_panels)
        self.menu.addAction('Reset window position').triggered.connect(self.center)
        language_menu = self.menu.addMenu('Language')
        self.language_actions = self.choices(language_menu, [('en', 'English'), ('pt', 'Português')], self.language, self.set_language)
        self.menu.addSeparator()
        self.menu.addAction('Copy Codex executable path').triggered.connect(
            lambda: QApplication.clipboard().setText(executable() or self.t('Codex executable not found'))
        )
        self.menu.addAction('QuotaDock on GitHub').triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl('https://github.com/sakazaki1/quotadock'))
        )
        self.menu.addSeparator()
        self.menu.addAction('Quit').triggered.connect(QApplication.instance().quit)
        self.menu_texts = []
        self.collect_menu_texts(self.menu)
        menu_button.setMenu(self.menu)
        for widget in (self.compact_widget, self.compact_progress, self.compact_percentage):
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
        self.tray.setToolTip('Real Codex quota')
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
        if self.app_only:
            self.hide()
            self.window_watcher.start()
        self.set_display_mode(self.display_mode)
        self.retranslate_ui()

    def t(self, text):
        return translate(text, self.language)

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

    def retranslate_ui(self):
        self.setWindowTitle(self.t('QuotaDock — Codex usage monitor'))
        for action, english in self.menu_texts:
            action.setText(self.t(english))
        self.header.findChild(QLabel, 'title').setToolTip(self.t('Drag to position over your app'))
        self.compact.setAccessibleName(self.t('Minimize to tray'))
        self.compact.setToolTip(self.t('Minimize to tray; click the icon to restore'))
        self.menu_button.setAccessibleName(self.t('QuotaDock options'))
        self.compact_progress.setAccessibleName(self.t('Remaining quota'))
        for index, (_, _, progress) in enumerate(self.rows):
            progress.setAccessibleName(self.t(('Remaining quota: Window', 'Remaining quota: Week')[index]))
        self.paint_quota()
        if self.last_error_message is not None:
            self.error(self.last_error_message)
        elif not self.last_update:
            self.status.setText(self.t('Connecting to Codex…'))
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
        background = f'rgba(32, 40, 36, {self.transparency})' if self.theme == 'translucent' else '#202824'
        self.setStyleSheet(self.style_template.replace('CARD_BACKGROUND', background))

    def set_theme(self, theme):
        self.theme = theme
        self.settings.setValue('theme', theme)
        self.apply_theme()

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
        if self.display_mode != 'compact_bar':
            self.setMinimumHeight((140 if panels == 'both' else 100) if self.minimal else (190 if panels == 'both' else 145))
        if hasattr(self, 'tray'):
            self.update_tray()

    def set_display_mode(self, mode):
        self.display_mode = mode
        self.settings.setValue('displayMode', mode)
        compact = mode == 'compact_bar'
        if not compact and self.minimal:
            self.minimal = False
            self.settings.setValue('minimal', False)
            self.minimal_action.setChecked(False)
            self.apply_minimal()
        self.header.setVisible(not compact)
        self.body.setVisible(not compact)
        self.status.setVisible(not compact and (not self.minimal or self.is_failed))
        self.compact_widget.setVisible(compact)
        if compact:
            self.setMinimumSize(112, 38)
            self.resize(self.settings.value('compactWidth', 152, type=int), self.settings.value('compactHeight', 42, type=int))
        else:
            self.setMinimumSize(210, 115)
            self.resize(self.settings.value('fullWidth', 310, type=int), self.settings.value('fullHeight', 230, type=int))
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
            elif (self.display_mode == 'compact_bar' and event.type() == QEvent.Type.MouseButtonPress
                  and event.button() == Qt.MouseButton.LeftButton):
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemMove()
                    return True
            watched.setCursor(Qt.CursorShape.SizeAllCursor if not edges and watched.objectName() == 'title' else cursor)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The layouts reflow with the window; scale text on wider panels as well.
        scale = min(1.7, max(0.85, self.width() / 310))
        if hasattr(self, 'rows'):
            for label, value, progress in self.rows:
                label.setStyleSheet(f'font-size: {round(12 * scale)}px;')
                value.setStyleSheet(f'font-size: {round(18 * scale)}px; font-weight: bold;')
                progress.setMinimumHeight(round(7 * scale))
        if hasattr(self, 'settings') and self.isVisible():
            if self.app_only and not self._placing:
                prefix = 'compact' if self.display_mode == 'compact_bar' else 'full'
                self.settings.setValue(f'{prefix}Width', self.width())
                self.settings.setValue(f'{prefix}Height', self.height())
            elif not self.app_only:
                self.settings.setValue('geometry', self.saveGeometry())

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
        self.move(rect.right() - self.width() - 25, rect.top() + 80)

    def set_minimal(self, enabled):
        self.minimal = enabled
        self.settings.setValue('minimal', enabled)
        self.apply_minimal()
        self.set_panels(self.panels, save=False)
        if self.display_mode != 'compact_bar':
            self.resize(240, 150 if self.panels == 'both' else 115) if enabled else self.resize(310, 230)
        self.paint_quota()

    def apply_minimal(self):
        self.status.setVisible(self.display_mode != 'compact_bar' and (not self.minimal or self.is_failed))

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
        for index in selected:
            window = windows[index]
            value = remaining(window)
            tips.append(self.t(window_label(window, ('Hours', 'Week')[index])) + ': ' + (f'{value:g}%' if value is not None else self.t('unavailable')))
        tips.append(self.reset_label.text())
        tips.append(self.t('Click to show/hide; right-click for options.'))
        self.tray.setToolTip('\n'.join(tips))

    def update_quota(self, bucket):
        self.bucket = bucket
        self.last_update = time.monotonic()
        self.is_failed = False
        self.last_error_message = None
        self.paint_quota()
        self.tick()

    def paint_quota(self):
        for index, (label, value, progress) in enumerate(self.rows):
            window = self.bucket.get(('primary', 'secondary')[index]) if self.bucket else None
            amount = remaining(window)
            label.setText(self.t(window_label(window, ('Window', 'Week')[index])))
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
        self.apply_minimal()
        self.update_tray()

    def tick(self):
        if not self.last_update:
            return
        age = int(time.monotonic() - self.last_update)
        if age > 75 and not self.is_failed:
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
    bar = Bar()
    app.aboutToQuit.connect(bar.cleanup)
    if not bar.app_only:
        bar.show()
        QTimer.singleShot(0, bar.hide_from_taskbar)
    if args.smoke_test:
        bar.update_quota({'primary': {'usedPercent': 6, 'windowDurationMins': 300}, 'secondary': {'usedPercent': 1, 'windowDurationMins': 10080}})
        assert bar.rows[0][1].text() == '94%'
        assert bar.rows[1][1].text() == '99%'
        bar.error('Offline test')
        assert bar.is_failed
        app.processEvents()
        bar.grab().save(str(Path(tempfile.gettempdir()) / 'quotadock-preview.png'))
        QTimer.singleShot(100, app.quit)
    else:
        QTimer.singleShot(0, bar.client.start)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
