# QuotaDock roadmap

This roadmap orders work by impact on reliable public use. Checkmarks indicate foundations already present, not a final promise of platform-wide support.

## Milestone 1 — Publish a dependable beta

**Goal:** let a new user install and understand QuotaDock without working from source.

- [ ] Create the first `v1.1.0` GitHub Release and verify its Linux and Windows artifacts on clean machines.
- [ ] Add a **Check for updates** menu action that opens the matching GitHub Release. It must never download, install, or send credentials automatically.
- [ ] Add a short installation matrix to the README: Kubuntu/KDE, other Linux desktops, Windows, and their current support status.
- [x] Generate Linux and Windows release artifacts from version tags in GitHub Actions.
- [x] Provide `pyproject.toml` for isolated developer installation.

**Done when:** a tester can download a release, launch it, see a real or honest unavailable state, and update using documented steps.

## Milestone 2 — Validate the supported platforms

**Goal:** turn CI coverage into real desktop confidence.

- [ ] Test Windows 10 and 11 on physical hardware: high-DPI scaling, two monitors, ChatGPT and Codex window anchoring, tray behavior, resume from sleep, and installation/update paths.
- [ ] Test Linux on KDE/X11, KDE Wayland with XWayland, and GNOME Wayland.
- [ ] Make the overlay fall back clearly to a normal floating window when a compositor cannot expose the active application window.
- [ ] Document known limitations instead of silently hiding the overlay.

**Done when:** each supported platform has a reproducible manual test checklist and its results are recorded in an issue or release note.

## Milestone 3 — Complete the provider architecture

**Goal:** keep the interface independent from Codex, Claude, or future providers.

- [ ] Move the remaining Qt-bound Codex client and window watcher from `quotadock.py` into provider adapters.
- [ ] Make the UI render the provider-neutral `QuotaWindow` model rather than provider-shaped dictionaries.
- [ ] Give every source an explicit freshness policy: Codex readings expire after their read age; Claude local-file values remain valid until their provider-reported reset.
- [ ] Add a source selector and source-specific configuration UI.
- [x] Define a provider-neutral model and source protocol in `quota_core.py`.
- [x] Add an optional local-file Claude adapter that never reads browser credentials.

**Done when:** a new provider can be added without conditional `provider == ...` logic in the UI.

## Milestone 4 — Strengthen reliability and security

**Goal:** protect users from misleading values and unsafe local execution.

- [ ] Add a full stdio contract test process that simulates initialization, notifications, JSON-RPC errors, timeouts, malformed messages, and process exits.
- [ ] Test the executable trust policy on POSIX ownership and permission combinations.
- [ ] Record the resolved Codex executable path and its file metadata in preferences; show a visible warning if it changes before a later launch.
- [ ] Keep all runtime output out of persistent logs unless the user explicitly enables diagnostic mode.
- [x] Use capped exponential retry with jitter after connection failures.
- [x] Reject POSIX executables writable by group or other users.

**Done when:** protocol changes and common local failures are detected by tests before release, with no quota fabricated by recovery code.

## Milestone 5 — Improve daily usability

**Goal:** help users act on real quota information without turning QuotaDock into a noisy monitor.

- [ ] Add opt-in threshold notifications at 20% and 10%, with a per-window choice and a quiet-hours option.
- [ ] Add an opt-in reset notification when a provider delivers a newly refreshed quota value.
- [ ] Add accessible keyboard navigation, screen-reader labels, and contrast checks for every theme.
- [ ] Revisit the compact layout on high-DPI displays after real Windows testing.

**Done when:** all notifications are optional, rate-limited, and based only on provider-reported values.

## Milestone 6 — Optional local history

**Goal:** offer useful context without weakening the privacy model.

- [ ] Design a local-only SQLite history setting, disabled by default, with retention controls and a one-click delete action.
- [ ] Store only timestamp, provider, window identifier, remaining percentage, and reset timestamp. Never store prompts, account identifiers, tokens, or raw protocol messages.
- [ ] Label any exhaustion estimate as a projection and keep the raw reported value visually distinct.

**Done when:** users can inspect and delete their local history, and the project can explain every stored field.

## Later exploration

- [ ] Verify the Claude Code status-line file format against official documentation before advertising it beyond an experimental option.
- [ ] Investigate Wayland-native active-window integration per compositor.
- [ ] Consider macOS as a separate project scope, beginning with tray-only mode and explicit accessibility permission handling.

## Order of implementation

1. Milestone 1: release and installation experience.
2. Milestone 2: real hardware validation and honest fallbacks.
3. Milestone 4: contract tests and executable-change warning.
4. Milestone 3: finish source/UI separation and configuration.
5. Milestone 5: threshold and reset notifications.
6. Milestone 6: optional history and projections.

The order deliberately puts correct installation, platform behavior, and honest quota data before new dashboard features.
