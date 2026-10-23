# QuotaDock

**Your remaining Codex quota, always within reach.**

QuotaDock is a lightweight Linux desktop companion that shows your real remaining Codex quota in a compact overlay anchored to the active ChatGPT/Codex window, with a live system-tray icon. Built for Kubuntu and KDE Plasma, it keeps usage visible without opening the usage settings after every action.

It reads server-reported percentages from the local Codex app server. It does not count messages or estimate token consumption.

## Features

- **Real remaining quota:** percentages from 100% down to 0% for your short-term and weekly windows.
- **Live tray icon:** remaining percentage and proportional quota bars, with full values available on hover.
- **Automatic refresh:** polls every 30 seconds and accepts quota notifications received on its connection.
- **Next reset time:** visible below the hours panel, in your computer's local time zone.
- **Compact bar theme:** shows only the remaining percentage and its progress bar, with no app name or labels.
- **ChatGPT/Codex-only overlay:** by default the small pop-up stays inside the active ChatGPT/Codex window and hides when you switch to another app.
- **Minimize to tray:** keep monitoring in the background and click the icon to restore.
- **Choose your panels:** hours only, weekly only, or both.
- **Solid or translucent theme:** three background opacity levels, with readable opaque text.
- **Move and resize:** drag the title to move and the edges or corners to resize.
- **Saved preferences:** position, dimensions, theme and visible panels persist between sessions.
- **Optional login startup:** installs a desktop menu shortcut and can start automatically at login.
- **Honest offline state:** missing values appear as `—`; stale data is grayed out and the tray icon shows `?`.

## Compatibility

Tested on **Kubuntu 26.04, KDE Plasma, Wayland with XWayland**, Python 3 and PyQt6. Other Linux desktops may work but have not been verified. Windows and macOS are not currently supported.

QuotaDock is a standalone companion, **not a plugin injected into ChatGPT**. By default it anchors its pop-up to the active ChatGPT/Codex window and hides when that window loses focus. This uses the X11/XWayland window information tools (`xprop` and `xwininfo`); if those tools or a detectable XWayland window are unavailable, the app-only overlay stays hidden. Turn off **Show only over ChatGPT/Codex** in the tray menu to use it as a regular floating window. It does not modify the official application.

The displayed quota belongs to the account authenticated in the **local Codex runtime**. It is not a universal meter for every model or feature in regular ChatGPT chats. Some plans may omit one of the quota windows.

## Quick start — Ubuntu / Kubuntu

1. Install and sign in to ChatGPT/Codex. QuotaDock needs a local Codex executable with an authenticated ChatGPT account.
2. Install the UI dependency:

   ```sh
   sudo apt install python3-pyqt6
   ```

3. Download this repository using **Code → Download ZIP** and extract it. Open a terminal in the extracted folder and run:

   ```sh
   sh install.sh
   ```

4. Open **QuotaDock** from your application menu. Your quota loads automatically.

The installer runs as your normal user; do **not** run it with `sudo`. It creates a desktop shortcut and enables startup at login. To install without startup at login:

```sh
sh install.sh --no-autostart
```

For a checkout with Git:

```sh
git clone https://github.com/sakazaki1/quotadock.git
cd quotadock
sh install.sh
```

To try QuotaDock without installing:

```sh
sh launch.sh
```

### Codex executable discovery

QuotaDock looks for Codex bundled with common Linux ChatGPT/Codex installations, then checks `codex` on your `PATH`. If it cannot find your executable, launch it with an explicit path:

```sh
QUOTADOCK_CODEX=/path/to/codex sh launch.sh
```

To use this override from the application menu as well, configure the environment variable in your desktop session. Sign in using your Codex installation's normal sign-in flow. QuotaDock never asks you to paste credentials or API keys.

## Everyday controls

| Control | Action |
| --- | --- |
| Drag the title | Move the floating window |
| Drag any edge or corner | Resize the window; compact and full window sizes are saved separately |
| **−** | Minimize to the tray without stopping updates |
| Left-click the tray icon | Show or hide the window |
| Right-click the tray icon / **⋮** | Open the settings menu |
| **Minimal interface** | Switch compact/detailed layout |
| **Display style → Bar and percentage only** | Hide the title and labels and show a small usage bar with its remaining percentage; drag the bar to move it and right-click for options |
| **Display style → Full window** | Leave minimal mode and restore the full quota panel |
| **Show only over ChatGPT/Codex** | Keep the pop-up inside the active ChatGPT/Codex window and hide it elsewhere |
| **Visible panels** | Select hours, weekly, or both |
| **Theme** | Choose solid or translucent; adjust background opacity |
| **Refresh now** | Request an immediate reading |
| **Quit** | Stop QuotaDock and its helper process |

With both panels selected, the tray number represents the hours window; the top strip represents hours and the lower strip represents the weekly window. With weekly only, the number represents the weekly window. Hover for the complete percentages and reset time. The short-term window label follows the actual duration returned by Codex; it is not hardcoded to five hours.

## Accuracy and privacy

QuotaDock starts `codex app-server --stdio`, completes initialization, and calls the documented `account/rateLimits/read` method. Remaining quota is calculated as `100 - usedPercent`. It also accepts `account/rateLimits/updated` notifications on that connection.

Updates are periodic, not guaranteed after every individual action. The service itself can delay reporting. QuotaDock does not predict resets or synthesize new percentages when the local clock reaches a reset time. When offline, the window retains the last reading in gray with a warning; the tray switches to `?`. Readings older than 75 seconds are marked stale.

- No analytics, ad services or QuotaDock backend.
- No direct reading, copying or transmission of authentication files by QuotaDock.
- No prompt submission, conversation collection, credit purchases or reset consumption.
- The Codex helper uses its own existing authentication and contacts OpenAI under the runtime's normal configuration. QuotaDock has no separate API key requirement.
- Only UI preferences are stored by QuotaDock, normally in `~/.config/QuotaDock/QuotaDock.conf`. The Codex runtime may maintain its own files independently.
- Switching accounts in the main application may require restarting QuotaDock so its helper reconnects with the current local authentication.

QuotaDock is an independent community project and is not affiliated with or endorsed by OpenAI. ChatGPT and Codex are trademarks of their respective owners.

## Troubleshooting

**No percentage appears:** check your Codex sign-in and executable location. Missing or inaccessible quota is shown as unavailable rather than 100%.

**Cannot move or resize the compact bar:** drag its center to move, drag a narrow edge to resize, or right-click it to open the menu. In full-window mode, drag the title to move and any edge or corner to resize. On KDE Wayland, the launcher uses XWayland when available; check for custom KWin window rules if the compositor blocks these actions.

**Window is hidden:** click the tray icon, or use its right-click menu → **Show / hide QuotaDock**. Use **Reset window position** if it is offscreen. Without a system tray, minimizing falls back to a regular taskbar entry.

**Multiple launches:** only one instance is allowed per desktop session. Use the existing tray icon to restore it.

**Disable login startup:** open your desktop's Autostart settings and disable QuotaDock, or reinstall using `--no-autostart`.

## Update or uninstall

To update, quit QuotaDock from its tray menu, download the new version, run `sh install.sh` again, then reopen it from the menu. Reuse `--no-autostart` if you want startup disabled.

To uninstall, quit QuotaDock and run:

```sh
python3 ~/.local/share/quotadock/uninstall.py
```

The uninstaller respects custom `XDG_DATA_HOME` and `XDG_CONFIG_HOME` settings. With a custom data directory, run the uninstaller from that directory instead. It removes only QuotaDock's installation, shortcuts and UI preferences, leaving ChatGPT/Codex and its authentication untouched.

## Development

```sh
python3 -m unittest discover -s . -p 'test_*.py'
QT_QPA_PLATFORM=offscreen XDG_CONFIG_HOME=/tmp/quotadock-test \
  XDG_RUNTIME_DIR=/tmp/quotadock-runtime python3 quotadock.py --smoke-test
```

The smoke test uses fixture values and writes a preview to `/tmp/quotadock-preview.png`; normal operation never uses fixture quota values. No credentials are required for tests.

Protocol reference: [Codex App Server documentation](https://learn.chatgpt.com/docs/app-server).

## License

MIT. See [LICENSE](LICENSE).
