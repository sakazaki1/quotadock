# Manual platform validation

Run this checklist against a tagged release, not a source checkout. Record the operating system, desktop environment, display scale, and result in the corresponding GitHub Release or issue.

## Linux

| Environment | Checks |
| --- | --- |
| KDE X11 | Launch, tray restore, active-window anchoring, edge resize, two monitors, logout/login autostart. |
| KDE Wayland with XWayland | The same checks, plus moving between monitors and behavior after screen lock. |
| GNOME Wayland | Launch, tray availability, and the normal-floating-window fallback when active-window tracking is unavailable. |

## Windows 10 and 11

- Test 100%, 150%, and 200% display scaling.
- Test moving the ChatGPT/Codex window across two monitors with different DPI.
- Test foreground-window anchoring, tray restore, sleep/resume, Start-menu install, update, and uninstall.
- Confirm that a missing target application leaves QuotaDock usable as a normal floating window after the user disables app-only mode.

## Result criteria

Do not mark a platform as supported merely because CI passes. Record **pass**, **known limitation**, or **blocked** for every row, with a concise reproduction note for anything other than pass.
