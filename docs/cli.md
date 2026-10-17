# CLI reference

`manaradix` is a single command with four subcommand groups. With no
subcommand it launches the GUI; subcommands run headless and are suitable
for scripts and systemd service units.

```
manaradix [GLOBAL OPTIONS] <command> <subcommand> [OPTIONS]
```

## Global options

| Option | Default | Description |
|---|---|---|
| `--config PATH` | `/etc/manaradix/config.toml` | Path to the configuration file |
| `--snapshot-dir DIR` | `.snapshots` | Snapshot storage directory relative to the mount point |
| `--debug` | off | Enable debug logging |
| `--frontend {qt,gtk,ncurses}` | auto-detect | UI toolkit frontend (GUI mode only; qt/gtk fall back to ncurses) |

## Commands

| Command | Subcommands | Purpose |
|---|---|---|
| *(none)* | — | Launch the GUI |
| `snapshot` | `create`, `list`, `delete`, `prune`, `info` | Snapshot lifecycle and pruning |
| `setup` | `status`, `apply`, `list-profiles` | First-run setup wizard |
| `activity` | `status`, `record` | Automated snapshot run status and records |
| `dbus` | `serve`, `status` | System D-Bus service daemon and status |
| `tray` | `serve`, `status` | Notification-area applet (user session) and status |
| `usage` | `collect`, `status`, `show` | Snapshot disk-usage collector and stored results |

## snapshot create

```bash
manaradix snapshot create [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--subvolume PATH` | `/` | Source subvolume path |
| `--type TYPE` | `manual` | Snapshot type: `manual`, `readonly`, `timed`, `boot`, `pre-restore`, `pre-install`, `pre-upgrade` |
| `--schedule S` | — | Retention tier (`hourly`, `daily`, `weekly`, `monthly`, `boot`, `pre-upgrade`); only meaningful with `--type timed` or `--type pre-upgrade` |
| `--description TEXT` | — | Human-readable description |
| `--read-write` | read-only | Create a read-write snapshot |
| `--mount PATH` | `/` | Mount point for the subvolume |
| `--dry-run` | — | Preview the operation without executing |

Notes:

- `--type pre-upgrade` is silently skipped (exit 0) when
  `snapshot_before_upgrade` is disabled in the configuration — this is what
  lets the rpm file trigger no-op harmlessly.
- `readonly`/`pre-restore`/`pre-install` are valid `--type` values but have
  no matching `--schedule` tier.

## snapshot list

```bash
manaradix snapshot list [--mount PATH] [--subvolume PATH]
```

| Option | Default | Description |
|---|---|---|
| `--mount PATH` | `/` | Mount point to list snapshots for |
| `--subvolume PATH` | — | Filter snapshots by source subvolume |

## snapshot info

```bash
manaradix snapshot info --id ID [--mount PATH]
```

| Option | Description |
|---|---|
| `--id ID` | Snapshot ID (timestamp, e.g. `2026-01-15_14-00-00`) |
| `--mount PATH` | Mount point (default `/`) |

## snapshot delete

```bash
manaradix snapshot delete --id ID [--mount PATH] [--dry-run]
```

## snapshot prune

```bash
manaradix snapshot prune --subvolume PATH --schedule S [--mount PATH] [--dry-run]
```

Keeps the N most recent snapshots for the schedule (N from the retention
policy in the configuration) and deletes the rest. Invoked automatically by
the generated systemd timer units. If no policy is configured for the
subvolume it warns and exits 0.

## setup status / list-profiles / apply

```bash
manaradix setup status
manaradix setup list-profiles
manaradix setup apply [--profile ID] [--dry-run]
```

| Option | Description |
|---|---|
| `--profile ID` | Profile to apply (`desktop`, `server`, `kiosk`, or a custom template id); defaults to the profile detected for this system |
| `--dry-run` | Preview the setup without applying anything |

`setup status` reports whether the recommended setup is still needed
(no snapshots and no policies). Applying a profile writes the global
configuration, per-subvolume policies, systemd units, and the GRUB script
(as the layout permits). See [Setup profiles](profiles.md).

## activity status / record

```bash
manaradix activity status [--subvolume PATH] [--schedule S]
manaradix activity record --subvolume PATH --schedule S \
    [--result {success,failed,cancelled,unknown}] [--exit-status N]
```

The generated systemd service units call `activity record` after every
fire, so the current state of each scheduled snapshot tier is visible in
the GUI and over D-Bus. `activity status` is read-only and reports
per-activity status. See [Design & internals](activities.md).

| Option | Description |
|---|---|
| `--result` | Run result; defaults to `$SERVICE_RESULT` set by systemd |
| `--exit-status` | Exit status of the unit; defaults to `$EXIT_STATUS` |

## dbus serve / status

```bash
manaradix dbus serve
manaradix dbus status
```

| Command | Behavior |
|---|---|
| `dbus serve` | Run the system D-Bus service in the foreground (root only; normally started on demand by bus activation via the systemd unit). Exits 1 if not root or if the bus name is already taken; shuts down cleanly on SIGTERM |
| `dbus status` | Report whether the service is running — **never** activates it (uses `NameHasOwner`). Prints `running` and exits 0, or prints a hint and exits 2 |

## usage collect / status / show

```bash
manaradix usage collect     # measure now (root; what the systemd unit runs)
manaradix usage status      # collector state and timestamps (read-only)
manaradix usage show        # stored per-mount and per-snapshot sizes (read-only)
```

Measuring snapshot disk usage means walking every extent of every
snapshot (`btrfs filesystem du`), which can take minutes. The GUI never
does this itself: `manaradix-usage.timer` runs `usage collect` daily and
the result is stored in `/var/lib/manaradix/usage.sqlite`. The Dashboard
reads that file, shows when it was last updated, and its **Force Usage
Data Update** button starts the collector in the background. See
[Usage tracker](usage-tracker.md).

`usage collect` exits non-zero when the measurement failed (the failure
is also recorded in the store). `usage status` prints `Status:` as one of
`idle` (never run), `running`, `ok` or `failed`.

## tray serve / status

The notification-area applet, a **user-session** process (session bus,
StatusNotifierItem).  It autostarts at login from
`/etc/xdg/autostart/manaradix-tray.desktop`; see
[tray-applet.md](tray-applet.md).

| Command | Effect |
|---|---|
| `tray serve` | Run the applet in the foreground. Exits 0 immediately when the applet is disabled in the config, when no StatusNotifierWatcher exists (no tray-capable desktop), or when another instance owns `org.mageia.manaradix.Tray`. Checks run on a worker thread and read through the D-Bus daemon; the applet never elevates. Shuts down cleanly on SIGTERM/SIGINT |
| `tray status` | Report whether the applet is running — **never** activates it (uses `NameHasOwner`). Prints a confirmation and exits 0, or prints a hint and exits 2 |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success (including the documented no-ops: pre-upgrade snapshots with the feature disabled, prune with no policy) |
| `1` | Operation failure, pkexec unavailable/failed, daemon refused (not root, name taken), GUI startup failure |
| `2` | `dbus status`: the service is not running (also argparse's usage-error code) |
| `0` with `--help` | argparse help display |

## Privilege model

The CLI re-executes privileged operations via `pkexec`:

- **Read-only, never elevated**: `snapshot list`, `snapshot info`,
  `setup status`, `setup list-profiles`, `activity status`
- **Always elevated**: `snapshot create`, `snapshot delete`,
  `snapshot prune`, `setup apply`, and GUI mode (no subcommand)
- **Exempt**: `dbus serve` (refuses non-root itself), `dbus status`
  (read-only), `activity record` (runs as root via systemd), `tray
  serve`/`tray status` (user-session process — elevation would put it
  in a root-owned session)
- Unknown subcommands default to elevated

The `MANARADIX_PKEXEC` environment variable guards against elevation
loops; the `MUI_BACKEND` variable selects the UI backend.
