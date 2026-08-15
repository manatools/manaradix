# Activity: the manaradix tray applet

A write-up of the tray-applet development activity (manaradix 0.1.3,
August 2026), in the "what, why, how" style used for the project's
release posts.  Free to adapt for websites and blog posts.

---

## The problem

manaradix protects a Mageia system's data: Btrfs snapshots on a
schedule, boot-time snapshots, pre-upgrade snapshots before every rpm
transaction.  But a snapshot is only useful if you notice you need it
while the problem is still cheap — and the one thing no utility told the
user was *when* the filesystem started misbehaving.  Low disk space,
btrfs error counters climbing, a drive failing SMART, snapshots
accumulating past their retention budget: all of these are exactly the
kind of "quiet" problems that turn into data loss on a server nobody
looks at.

So manaradix grew a **tray applet**: a small process living in the user
session that watches the filesystems, keeps a healthy desktop's tray
clean, and speaks up — icon and notification — the moment something
needs a human.

## The design decisions

### 1. A real health picture, not just btrfs

The applet had to answer "is this disk OK?".  btrfs alone gives the five
error counters (`device stats`) — corruption, generation, read/write/
flush failures — but nothing about the drive itself.  The system already
has that: UDisks2 exposes SMART data for SATA/NVMe drives, and Mageia's
desktop stacks ship it.  So the D-Bus daemon gained a new read method,
`GetDeviceStats`, that merges both sources into one per-device report:
btrfs counters always, SMART fields (health, power-on hours, SATA link
up/down cycles) when UDisks2 has them, never an error when it doesn't.
One read gives the applet a comprehensive statistics view of every
Btrfs filesystem.

### 2. Profile-aware retention suggestions

Snapshots that outlive their retention tier are exactly the ones that
should be pruned — but "should" is a policy question, and manaradix's
setup profiles think differently about it.  The desktop profile keeps a
limited count (a desktop user wants the disk, not archaeology); server
and kiosk profiles keep their own budgets.  The applet reads the
configured retention policy — the same numbers `prune` uses — and
suggests removing only the snapshots a prune would actually drop, so
the suggestion and the command always agree.

### 3. The tray ships in the same package

One `urpmi manaradix`, one tray applet.  No separate tray package, no
repo juggling: the autostart file installs with the package and the
applet starts at login.  This was an explicit product decision — the
utility and its watchdog are one thing.

### 4. Hide until it matters

The applet is an SNI (StatusNotifierItem) client on the session bus —
the protocol Plasma, GNOME, and Xfce trays all speak — with a
`com.canonical.dbusmenu` menu and Freedesktop notifications.  Three
behaviours keep it unobtrusive:

- **Two-state icon.**  Healthy → the manaradix logo; problems → an
  attention state.  With `hide_when_ok` (default) the icon is hidden
  entirely while nothing is wrong.
- **Silent start.**  No StatusNotifierWatcher (no tray-capable desktop)?
  Exit 0 without a word.  It starts at login; a desktop that can't show
  it just never sees it.
- **Single instance.**  The applet claims `org.mageia.manaradix.Tray` on
  the session bus; a second `tray serve` exits quietly.

## The architecture

```
user session                          system bus
┌────────────────────────────┐
│ tray applet                │          ┌─────────────────────────┐
│  ┌──────────┐  ┌────────┐  │          │ org.mageia.manaradix    │
│  │ scheduler│─►│ worker │─►│─ client ─►│  daemon (polkit-gated)  │
│  │ 4 checks │  │ thread │  │          │  └─ GetDeviceStats      │
│  └──────────┘  └────────┘  │          │     + core services     │
│       │  ▲                 │          └─────────────────────────┘
│       ▼  └─ queue drained  │
│  icon · menu · notifications (SNI + DBusMenu + Notify)
└────────────────────────────┘
```

- Checks run on a **worker thread**; findings come back through a queue
  drained at 100 ms on the GLib mainloop, with generation counters so a
  stale result from a just-triggered re-check is never shown.
- Each batch uses a **fresh client connection** — the applet never holds
  the daemon open, and the daemon is bus-activated on demand.
- **Quiet hours** suppress notifications (but not the icon) in a
  configurable window.
- **Fail open.**  A check that cannot run (daemon down, UDisks2 absent,
  no SMART on a drive) contributes no findings.  Missing sources are
  never errors; no check can crash the applet.
- **Read-only observer.**  The applet never creates, deletes, or
  restores anything.  Retention *suggestions* describe what `prune`
  would do; the user acts through the GUI.
- **Never elevated.**  The applet is a user-session process; `tray
  serve`/`tray status` are excluded from pkexec elevation.

The worker-thread + drain-timer pattern and the two-state icon follow
dnfdragora's tray implementation — the reference tray client in Mageia.

## What it monitors

| Check | Interval | Notifies when |
|---|---|---|
| Space | 10 min | a filesystem passes 85 % used (warn) / 95 % (critical) |
| Devices | 60 min | btrfs error counters move; SMART health fails; SATA link up/down cycles climb |
| Retention | 24 h | snapshots exist that a prune would drop (per configured profile budgets) |
| Scrub | 60 min | no scrub run is recorded for a filesystem (bit-rot protection on long-lived servers) |

All four are individually switchable in `[tray]` in
`/etc/manaradix/config.toml`; thresholds and intervals are configurable.

## The code

| Piece | What it is |
|---|---|
| `manaradix/tray/` | the applet: `app.py` (wiring), `checks.py` (the four checks), `status_icon.py` (SNI), `dbus_menu.py` (DBusMenu), `notifier.py` (notifications), `scheduler.py` (finding tracking + nag cadence) |
| `manaradix/core/health.py` + `health_sources.py` | the merged health report: btrfs counters + UDisks2 SMART |
| `manaradix/dbus/` | `GetDeviceStats` read method on the system daemon |
| `manaradix/utils/pkexec.py` | session commands never elevate |
| `data/desktop/manaradix-tray.desktop` | autostart at login, installed to `%{_sysconfdir}/xdg/autostart/` |
| `locale/it.po`, `locale/zh_CN.po` | the applet's strings ship in the Italian and Chinese translations |

## The numbers

- 76 new unit tests (tray package + CLI wiring), ~800 tests in the full
  suite, all passing.
- 1 new D-Bus method (42 total on the daemon), 1 new CLI family
  (`tray serve` / `tray status`), 1 new config section (`[tray]`).
- Translations: the applet's strings are covered in both shipped
  languages (Italian sample expanded, Chinese added).

## Try it

```bash
urpmi manaradix          # or upgrade to 0.1.3
manaradix tray status    # is it running? (autostart starts it at login)
```

Edit `[tray]` in `/etc/manaradix/config.toml` to tune thresholds,
intervals, quiet hours, or to disable individual checks.  The icon
appears in the tray when a check finds something — or run
`manaradix tray serve` in a terminal to watch it live.
