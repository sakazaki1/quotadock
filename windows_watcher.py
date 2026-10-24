"""Follow the foreground ChatGPT/Codex window on Windows using Win32."""
import ctypes
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QTimer, Signal


class AppWindowWatcher(QObject):
    changed = Signal(bool, QRect)

    def __init__(self, own_window, parent=None):
        super().__init__(parent)
        self.own_window = own_window
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.last_target = None
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.poll)

    def start(self):
        self.timer.start()
        self.poll()

    def stop(self):
        self.timer.stop()
        self.last_target = None

    def process_name(self, window):
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if not pid.value:
            return ''
        handle = self.kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ''
        try:
            size = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                return Path(path.value).name.casefold()
            return ''
        finally:
            self.kernel32.CloseHandle(handle)

    def poll(self):
        window = self.user32.GetForegroundWindow()
        if window and int(window) == int(self.own_window.winId()):
            window = self.last_target
        elif window and self.process_name(window) in ('chatgpt.exe', 'codex.exe'):
            self.last_target = window
        else:
            self.last_target = None
            self.changed.emit(False, QRect())
            return
        if not window or not self.user32.IsWindow(window):
            self.last_target = None
            self.changed.emit(False, QRect())
            return
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(window, ctypes.byref(rect)) or rect.right <= rect.left or rect.bottom <= rect.top:
            self.changed.emit(False, QRect())
            return
        self.changed.emit(True, QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top))
