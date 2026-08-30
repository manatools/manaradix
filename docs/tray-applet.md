# Tray applet

A notification-area (system tray) applet for manaradix that runs in the
user session, quietly watches the health of the Btrfs filesystems, and
surfaces problems exactly when they need attention.

- **Created:** 2026-08-14 (manaradix 0.1.3)

---

## What it does

- **Quick GUI access** — a single click on the tray icon opens the
  manaradix GUI (the menu's *Open manaradix* item and the notification
  buttons do the same).
- **About** — the menu's *About* item shows a notification with the
  running version, a one-line description, and the project website
  (clicking the notification opens the GUI).
- **Disk space monitoring** — notifies when a Btrfs filesystem drops
  below the warn threshold (default 85 % used) and again, more urgently,
  below the critical threshold (default 95 %).
- **Device health monitoring** — watches the five btrfs error counters
  (`device stats`) and, when UDisks2 provides SMART data, the SMART
  health state and the SATA link up/down cycles of every drive.  The
  daemon's `GetDeviceStats` read method merges both sources into one
  report, so the applet sees SMART attributes when they exist and
  silently does without when they don't.
- **Retention suggestions** — periodically reviews the snapshots that
  exist on each subvolume against the retention policy and suggests
  removing the ones a *prune* would drop — but only the ones that are
  already past their tier budget.  The suggestions are profile-aware:
  the desktop profile keeps a limited count, server and kiosk profiles
  keep their own budgets.
- **Scrub reminders** — nudges when no scrub run is recorded for a
  filesystem, the one check that catches bit-rot on long-lived servers.

Every check is individually switchable (`[tray]` config, below), runs
on its own interval, and is *additive*: the applet only ever reports
problems, never manages snapshots itself.  All destructive work stays in
the CLI/GUI where the privilege model lives.

## How it works

The applet is a **StatusNotifierItem** (SNI) over the session D-Bus —
the protocol KDE Plasma, GNOME, and Xfce tray implementations speak —
implemented directly with `python3-dbus`, with a `com.canonical.dbusmenu`
menu and `org.freedesktop.Notifications` for popups.  It ships **in the
same package** as the rest of manaradix; there is no separate tray
package.

```
user session
┌─────────────────────────────────────────────┐
│ tray applet (manaradix tray serve)          │
│                                             │
│  scheduler ──► worker thread ──► findings   │
│     │                │                      │
│     │          ManaradixClient (D-Bus)      │
│     │                │                      │
│     │                ▼                      │
│     │     org.mageia.manaradix daemon       │
│     │     (system bus, polkit-gated)        │
│     │                │                      │
│     │                ▼                      │
│     │     core services (BtrfsOps, health)  │
│     │                                       │
│     └──► icon + menu + notifications        │
└─────────────────────────────────────────────┘
```

- Checks run on a worker thread; results flow back to the GLib mainloop
  through a queue drained at 100 ms, with generation counters so stale
  results from a just-triggered re-check are never displayed.
- Each check batch gets a **fresh client connection** — the applet never
  holds the D-Bus daemon open permanently, it is bus-activated on demand.
- The icon is **two-state**: a healthy manaradix icon while nothing is
  wrong, an attention variant while problems exist.  By default the
  icon stays visible and clickable even when everything is fine (quick
  GUI access is the applet's first job); set `hide_when_ok = true` to
  get the hide-until-problem behavior instead (Plasma parks the healthy
  icon in the hidden tray overflow).
- **Quiet hours** (`quiet_hours = "22:00-07:00"`) suppress notifications
  (but not the icon state) during the configured window.

### Single instance

`manaradix tray serve` claims the well-known name
`org.mageia.manaradix.Tray` on the session bus; a second instance exits
silently.  If no StatusNotifierWatcher is present (no tray-capable
desktop), the applet exits 0 without complaint — it starts at login
from the autostart file and a desktop that cannot show it simply never
sees it.

### Never elevated

The applet is a user-session process: it reads through the D-Bus daemon
(polkit-gated per method) and never re-executes via pkexec.  `tray
serve`/`tray status` are excluded from elevation in `utils/pkexec.py`.

## Installation

The tray applet ships with the manaradix package.  It autostarts at
login via `/etc/xdg/autostart/manaradix-tray.desktop` (`X-GNOME`-aware,
so GNOME also honours it); `autostart = false` in the config removes
the autostart entry from the user's desktop session (the file itself is
owned by the package — remove it from
`~/.config/autostart/`-equivalents as the desktop applies them, or
disable the applet entirely, see below).

## Configuration

The applet reads the `[tray]` table of
[`/etc/manaradix/config.toml`](configuration.md).  The table is
session-bound: the applet never writes the system config, and the GUI's
Settings page does not (yet) edit it — edit the file as root.

```toml
[tray]
enabled = true          # master switch; false stops the applet
autostart = true
hide_when_ok = false   # true: healthy state hides the icon in the tray overflow
quiet_hours = ""        # "HH:MM-HH:MM", start > end crosses midnight

space_enabled = true
space_warn_pct = 85     # warn below 15 % free
space_critical_pct = 95 # critical below 5 % free
space_interval_min = 10

device_enabled = true   # btrfs device stats + SMART (when UDisks2 has it)
device_interval_min = 60

retention_enabled = true   # suggest pruning snapshots past the tier budget
retention_interval_min = 1440

scrub_enabled = true    # remind when no scrub is recorded
scrub_interval_min = 60
```

## CLI

```
manaradix tray serve    # run the applet in the foreground (autostart uses this)
manaradix tray status   # print whether the applet is running (exit 0/2)
```

`tray status` probes the bus name without activating anything and never
elevates.

## What the notifications say

| Finding | Body | Urgency |
|---|---|---|
| free space below warn | `Free space below 85% on /` | normal |
| free space below critical | `Free space below 95% on /` | **critical** |
| btrfs error counters | `N corruption, N generation errors recorded by btrfs.` | critical |
| SMART failing | `SMART health failing on /dev/sda` | **critical** |
| SATA link up/down cycles | `SATA link up/down cycles: N on /dev/sda` | critical |
| retention suggestions | `Snapshot 2026-08-10_00-00-00 of / can be removed` | normal |
| no scrub recorded | `No scrub run is recorded. ...` | normal |

Every notification carries an **Open manaradix** action button; the
critical ones are marked urgent in the desktop's notification daemon.
Suggestions are re-nagged on a schedule (retention: 7 days) until acted
on; a finding that disappears (space freed, snapshot pruned) stops
being notified at the next check.

## Code map

| Module | Role |
|---|---|
| `manaradix/tray/app.py` | `TrayApp` wiring: SNI name claim, worker thread, drain queue, timers, icon/tooltip/notify |
| `manaradix/tray/checks.py` | `build_check_registry()`: the four `CheckSpec`s (space/devices/retention/scrub) |
| `manaradix/tray/status_icon.py` | StatusNotifierItem object (icon, tooltip, context-menu signals) |
| `manaradix/tray/dbus_menu.py` | `MenuModel`: canonical DBusMenu GetLayout/Get/Event → open/check/about/quit |
| `manaradix/tray/notifier.py` | Freedesktop notifications with actions |
| `manaradix/tray/scheduler.py` | `FindingTracker`: new/upgrade/gone rules + per-finding nag cadence |
| `manaradix/core/health.py` | `HealthReport` merging btrfs counters + SMART fields |
| `manaradix/core/health_sources.py` | Per-device collectors: btrfs `device stats`, UDisks2 SMART |
| `manaradix/dbus/` | `GetDeviceStats` read method (ACTION_READ) on the system daemon |
| `manaradix/utils/pkexec.py` | `_SESSION_COMMANDS`: tray commands never elevate |

## Design notes

- **Read-only observer.**  The applet never creates, deletes, or
  restores anything.  Retention *suggestions* describe what `prune`
  would do; the user acts through the GUI.
- **Quiet by default, present when needed.**  `hide_when_ok` + the silent exit without a
  StatusNotifierWatcher mean a healthy system shows no extra tray icon.
- **Fail open.**  A check that cannot run (daemon down, UDisks2 absent,
  no SMART) contributes no findings — a missing source is never treated
  as an error, and no check can crash the applet.
- **dnfdragora lineage.**  The worker-thread + drain-timer + generation
  pattern and the two-state icon follow dnfdragora's tray implementation,
  which is the reference tray client in Mageia.
