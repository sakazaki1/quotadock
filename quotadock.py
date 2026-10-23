#!/usr/bin/python3
"""Native movable quota monitor for KDE. Uses the signed-in local Codex app-server."""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QSize, QLockFile, QObject, QProcess, QSettings, QStandardPaths, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QActionGroup, QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget
from limits import codex_bucket, remaining, window_label


def executable():
    override = os.environ.get('QUOTADOCK_CODEX')
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    for location in ('/usr/lib/chatgpt/resources/codex', '/usr/lib/codex/resources/codex',
                     '/opt/ChatGPT/resources/codex', '/opt/Codex/resources/codex'):
        path = Path(location)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return shutil.which('codex')


class QuotaClient(QObject):
    updated = pyqtSignal(dict)
    failed = pyqtSignal(str)

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
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
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
        if not self.stopping:
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


class Handle(QLabel):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemMove()


class Bar(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings('QuotaDock', 'QuotaDock')
        self.last_update = 0
        self.bucket = None
        self.is_failed = True
        self.client = QuotaClient()
        self.client.updated.connect(self.update_quota)
        self.client.failed.connect(self.error)
        self.setWindowTitle('QuotaDock — Codex usage monitor')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.theme = self.settings.value('theme', 'solid')
        self.panels = self.settings.value('panels', 'both')
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
        menu_button.setAccessibleName('QuotaDock options')
        header.addWidget(menu_button)
        layout.addLayout(header)
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
        minimal_action = self.menu.addAction('Minimal interface')
        minimal_action.setCheckable(True)
        minimal_action.setChecked(self.minimal)
        minimal_action.toggled.connect(self.set_minimal)
        refresh = self.menu.addAction('Refresh now')
        refresh.triggered.connect(self.client.refresh)
        theme_menu = self.menu.addMenu('Theme')
        self.theme_actions = self.choices(theme_menu, [('solid', 'Solid dark'), ('translucent', 'Translucent')], self.theme, self.set_theme)
        opacity_menu = theme_menu.addMenu('Background opacity')
        self.choices(opacity_menu, [(140, '55%'), (175, '70%'), (220, '86%')], self.transparency, self.set_opacity)
        panel_menu = self.menu.addMenu('Visible panels')
        self.choices(panel_menu, [('primary', 'Hours only'), ('secondary', 'Weekly only'), ('both', 'Hours and weekly')], self.panels, self.set_panels)
        self.menu.addAction('Reset window position').triggered.connect(self.center)
        self.menu.addSeparator()
        self.menu.addAction('Quit').triggered.connect(QApplication.instance().quit)
        menu_button.setMenu(self.menu)
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
        self.setMinimumHeight((140 if panels == 'both' else 100) if self.minimal else (190 if panels == 'both' else 145))
        if hasattr(self, 'tray'):
            self.update_tray()

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
            self.settings.setValue('geometry', self.saveGeometry())

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.isVisible():
            self.settings.setValue('geometry', self.saveGeometry())

    def center(self):
        rect = QApplication.primaryScreen().availableGeometry()
        self.move(rect.right() - self.width() - 25, rect.top() + 80)

    def set_minimal(self, enabled):
        self.minimal = enabled
        self.settings.setValue('minimal', enabled)
        self.apply_minimal()
        self.set_panels(self.panels, save=False)
        self.resize(240, 150 if self.panels == 'both' else 115) if enabled else self.resize(310, 230)
        self.paint_quota()

    def apply_minimal(self):
        self.status.setVisible(not self.minimal or self.is_failed)

    def minimize_to_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
        else:
            # Without a tray, provide a regular taskbar entry to restore the window.
            self.setWindowFlag(Qt.WindowType.Tool, False)
            self.showMinimized()

    def toggle_window(self):
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
        tips = ['QuotaDock • remaining' if not self.is_failed else 'QuotaDock • offline']
        for index in selected:
            window = windows[index]
            value = remaining(window)
            tips.append(window_label(window, ('Hours', 'Week')[index]) + ': ' + (f'{value:g}%' if value is not None else 'unavailable'))
        tips.append(self.reset_label.text())
        tips.append('Click to show/hide; right-click for options.')
        self.tray.setToolTip('\n'.join(tips))

    def update_quota(self, bucket):
        self.bucket = bucket
        self.last_update = time.monotonic()
        self.is_failed = False
        self.paint_quota()
        self.tick()

    def paint_quota(self):
        for index, (label, value, progress) in enumerate(self.rows):
            window = self.bucket.get(('primary', 'secondary')[index]) if self.bucket else None
            amount = remaining(window)
            label.setText(window_label(window, ('Window', 'Week')[index]))
            value.setText(f'{amount:g}%' if amount is not None else '—')
            progress.setValue(round(amount * 10) if amount is not None else 0)
            color = '#7fe7b2' if amount is not None and amount > 25 else '#ffd17e' if amount is not None and amount > 10 else '#ff9292'
            if self.is_failed or amount is None:
                color = '#7e8982'
            progress.setStyleSheet(f'QProgressBar::chunk {{ background: {color}; border-radius: 4px; }}')
            progress.setEnabled(not self.is_failed and amount is not None)
            tip = 'Remaining percentage reported by Codex.'
            reset = window.get('resetsAt') if isinstance(window, dict) else None
            if index == 0:
                self.reset_label.setText('Next reset: —')
            if isinstance(reset, (int, float)):
                try:
                    reset_text = datetime.fromtimestamp(reset).strftime('%Y-%m-%d at %H:%M')
                    tip += '\nResets: ' + reset_text
                    if index == 0:
                        self.reset_label.setText(('Resets at ' + datetime.fromtimestamp(reset).strftime('%H:%M')) if self.minimal else ('Next reset: ' + reset_text))
                        self.reset_label.setToolTip('Next reset: ' + reset_text)
                except (ValueError, OverflowError, OSError):
                    pass
            value.setToolTip(tip)
            progress.setToolTip(tip)
        self.apply_minimal()
        self.update_tray()

    def tick(self):
        if not self.last_update:
            return
        age = int(time.monotonic() - self.last_update)
        if age > 75 and not self.is_failed:
            self.error('Stale data. Waiting for Codex…')
        elif not self.is_failed:
            self.status.setText(f'Remaining • updated {age}s ago')
            self.setToolTip(f'Real quota • updated {age}s ago')

    def error(self, message):
        self.is_failed = True
        self.status.setText(message + (' Last reading shown in gray.' if self.last_update else ''))
        self.paint_quota()
        self.update_tray()

    def closeEvent(self, event):
        QApplication.instance().quit()
        event.accept()

    def cleanup(self):
        QApplication.instance().removeEventFilter(self)
        self.settings.setValue('geometry', self.saveGeometry())
        self.client.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    import signal
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    app.setApplicationName('QuotaDock')
    app.setQuitOnLastWindowClosed(False)
    runtime = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
    lock = QLockFile(str(Path(runtime) / 'quotadock.lock'))
    if not lock.tryLock(100):
        return 0
    bar = Bar()
    app.aboutToQuit.connect(bar.cleanup)
    bar.show()
    if args.smoke_test:
        bar.update_quota({'primary': {'usedPercent': 6, 'windowDurationMins': 300}, 'secondary': {'usedPercent': 1, 'windowDurationMins': 10080}})
        assert bar.rows[0][1].text() == '94%'
        assert bar.rows[1][1].text() == '99%'
        bar.error('Offline test')
        assert bar.is_failed
        app.processEvents()
        bar.grab().save('/tmp/quotadock-preview.png')
        QTimer.singleShot(100, app.quit)
    else:
        QTimer.singleShot(0, bar.client.start)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
