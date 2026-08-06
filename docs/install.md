# Installation

## From the Mageia repositories

```bash
urpmi manaradix
```

(or Rpmdrake / GNOME Software once the package is available for your
release). The package installs:

- `/usr/bin/manaradix` — the CLI and GUI launcher
- `man manaradix` and `man 5 manaradix-config` — reference pages
- `/usr/share/polkit-1/actions/org.mageia.manaradix.policy` — privilege rules
- `/usr/share/manaradix/templates/` — the desktop/server/kiosk setup profiles
- the bus-activated system D-Bus service
  (`manaradix-dbus.service`, `/usr/share/dbus-1/system-services/`)
- `/usr/libexec/manaradix/pre-upgrade` — the pre-upgrade snapshot trigger,
  fired by an rpm file trigger before every package transaction

## System requirements

| Dependency | Minimum | Notes |
|---|---|---|
| Python | 3.11+ | Core runtime |
| `btrfs-progs` | 6.7+ | `/usr/bin/btrfs` for all filesystem operations |
| `python-manatools` | Mageia 10 official repos | UI abstraction layer (GUI) |
| systemd | 255+ | Timers for automated snapshots |
| polkit | 0.120+ | Privilege escalation for non-root users |
| grub2 | — | Boot-from-snapshot support |

## After install

Run `manaradix` as your user. On a stock Mageia btrfs install the GUI opens
on the **Setup Wizard** tab proposing a profile for this system — see
[Quick start](quickstart.md) and [Setup profiles](profiles.md).

## From source (development)

See [Development](development.md) for the virtualenv workflow.
