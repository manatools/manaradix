# Packaging for other distributions

manaradix is developed on Mageia and `manaradix.spec` is the reference
package, but nothing in the code is Mageia-specific: it needs Btrfs,
systemd, polkit, D-Bus, and (optionally) GRUB.  This page lists what a
package for another distribution has to ship and the one piece that is
package-manager specific — the pre-upgrade snapshot trigger.

## Runtime dependencies

| Dependency | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | |
| `tomlkit` | ≥ 0.12.0 | the only PyPI dependency (`pyproject.toml`) |
| `python-manatools` | with the `aui` backend | GUI only; often `python3-manatools` |
| `btrfs-progs` | ≥ 6.7 | `btrfs` found via `PATH`, fallback `/usr/sbin/btrfs` |
| systemd | ≥ 255 | timers, the bus-activated daemon, the usage collector |
| polkit | ≥ 0.120 | `pkexec` and the per-method D-Bus authorization |
| `python3-dbus` (dbus-python) | ≥ 1.2 | D-Bus daemon, client, tray applet |
| PyGObject (`python3-gobject`) | | GLib main loop, polkit via GI |
| GRUB 2 | | optional: boot-from-snapshot menu (`grub-mkconfig` or `grub2-mkconfig`) |
| UDisks2 | | optional: SMART data in the health checks |

## File layout

Install the Python package with your usual PEP 517 tooling (`pip install .`,
`python -m installer`, ...), then the data files:

| Source | Destination |
|---|---|
| `data/polkit/org.mageia.manaradix.policy` | `/usr/share/polkit-1/actions/` |
| `data/dbus/org.mageia.manaradix.service` | `/usr/share/dbus-1/system-services/` |
| `data/dbus/org.mageia.manaradix.conf` | `/etc/dbus-1/system.d/` (or `/usr/share/dbus-1/system.d/`) |
| `data/systemd/manaradix-dbus.service` | systemd system unit dir (never enabled — bus-activated) |
| `data/systemd/manaradix-usage.{service,timer}` | systemd system unit dir (enable the timer once on install) |
| `data/libexec/manaradix-pre-upgrade` | `/usr/libexec/manaradix/pre-upgrade` (mode 0755) |
| `data/desktop/manaradix.desktop` | `/usr/share/applications/` |
| `data/desktop/manaradix-tray.desktop` | `/etc/xdg/autostart/` |
| `data/icons/hicolor/*` | `/usr/share/icons/hicolor/` |
| `data/images/manaradix.png` | `/usr/share/manaradix/images/` |
| `data/templates/*.toml` | `/usr/share/manaradix/templates/` |
| `data/man/man1/manaradix.1`, `data/man/man5/manaradix-config.5` | man page dirs |
| `locale/<lang>.po` | compile with `msgfmt` to `/usr/share/locale/<lang>/LC_MESSAGES/manaradix.mo` |
| (empty dir) | `/var/lib/manaradix/` (usage database) |

Paths that are fixed in code or data files:

- `/usr/bin/manaradix` — the generated snapshot timer units, the D-Bus
  unit, the usage service, and the pre-upgrade script call the CLI there.
- `/usr/libexec/manaradix/pre-upgrade` — `core/pre_upgrade.py`
  (`SCRIPT_PATH`) probes for the trigger script there, and installs it on
  demand when the flag is enabled and the file is missing.
- `/etc/manaradix/config.toml` — configuration.

The D-Bus name and polkit action ids keep the upstream
`org.mageia.manaradix` namespace on every distribution — they are part of
the public interface ([D-Bus interface](api/dbus.md)), not a branding
detail, so do not rename them downstream.

After install, mirror the spec's `%post`: `systemctl daemon-reload`, reload
the bus (`systemctl reload dbus` or `dbus-broker`), and refresh the icon
cache.  On removal, mirror `%preun`: stop and disable the policy-generated
`manaradix-snapshot-*` units and `manaradix-usage.timer`, and stop
`manaradix-dbus.service`.

## Pre-upgrade trigger

`data/libexec/manaradix-pre-upgrade` is package-manager neutral: it
throttles itself (one snapshot per 10 minutes), calls
`manaradix snapshot create --type pre-upgrade`, and always exits 0 —
whether a snapshot is taken is decided by the `snapshot_before_upgrade`
config flag.  Only the hook that runs it differs.  Design:
[Pre-upgrade snapshots](pre-upgrade-snapshots.md).

**RPM** (Mageia, Fedora, openSUSE, ...) — a transaction file trigger in
the spec, fired once before any file of the transaction changes:

```spec
%transfiletriggerun -- /usr /etc
%{_libexecdir}/manaradix/pre-upgrade || :
```

**apt / dpkg** (Debian, Ubuntu, ...) — `/etc/apt/apt.conf.d/80manaradix`:

```text
DPkg::Pre-Invoke { "[ -x /usr/libexec/manaradix/pre-upgrade ] && /usr/libexec/manaradix/pre-upgrade || true"; };
```

apt runs `Pre-Invoke` before each dpkg run; the script's throttle
collapses multiple runs of one upgrade into a single snapshot.

**pacman** (Arch, ...) — `/usr/share/libalpm/hooks/manaradix-pre-upgrade.hook`:

```ini
[Trigger]
Operation = Install
Operation = Upgrade
Operation = Remove
Type = Package
Target = *

[Action]
Description = Taking a pre-upgrade Btrfs snapshot (manaradix)...
When = PreTransaction
Exec = /usr/libexec/manaradix/pre-upgrade
```

Do not set `AbortOnFail` — the hook must never block a transaction.

The Triggers page labels the mechanism "rpm file trigger"; on other
package managers the label is cosmetic, the behaviour is the same.
